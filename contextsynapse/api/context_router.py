"""
Context-as-a-Service API Router
================================
All ``/context/`` endpoints for multi-agent shared context.

Mount on the main FastAPI app with::

    from contextsynapse.api.context_router import create_context_router
    app.include_router(create_context_router(graph_registry))
"""

from __future__ import annotations

import base64
import logging
import secrets
from typing import Any, Dict, List, Optional

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    HTTPException,
    Query,
    UploadFile,
    File,
    WebSocket,
    WebSocketDisconnect,
)
from pydantic import BaseModel, Field

from .auth import AgentAuth
from ..context.agents import AgentIdentity, ProvenanceRecord
from ..context.store_factory import create_agent_registry
from ..context.session import ContextSessionManager
from ..context.hub import ContextHub, ContextItem, ContextRole
from ..context.blob import BlobStore
from ..context.ingest import UnifiedIngestor
from ..context.document_processor import DocumentProcessor
from ..context.pubsub import ContextEvent, PubSubHub, pubsub_hub
from ..context.vector_integration import SessionVectorStore
from ..context.dedup import ContextDedup
from ..context.scoping import ContextScoper, ScopingConfig
from ..context.conversation import ConversationStore
from ..context.embedding_hooks import EmbeddingHooks
from ..context.session_graph import SessionGraphBuilder

logger = logging.getLogger(__name__)


# ======================================================================
# Pydantic request/response models
# ======================================================================

class AgentRegisterRequest(BaseModel):
    name: str
    role: str = "agent"
    platform: str = "app"  # desktop | mobile | app | browser | embedded
    capabilities: List[str] = Field(default_factory=lambda: ["read", "write"])
    metadata: Dict[str, Any] = Field(default_factory=dict)

class AgentResponse(BaseModel):
    agent_id: str
    name: str
    role: str
    platform: str = "app"
    capabilities: List[str]
    status: str = "active"
    created_at: str
    last_seen: Optional[str] = None
    metadata: Dict[str, Any]

class AgentRegisterResponse(AgentResponse):
    api_key: str  # returned only on registration

class SessionCreateRequest(BaseModel):
    name: str
    agent_id: Optional[str] = None
    context_id: Optional[str] = None    # auto-attach context (e.g. SDLC context)
    goal: Optional[str] = None          # session goal / objective
    config: Dict[str, Any] = Field(default_factory=dict)

class SessionResponse(BaseModel):
    session_id: str
    name: str
    created_at: str
    updated_at: str
    owner_agent_id: Optional[str]
    graph_namespace: str
    vector_collection: str
    document_collection: str
    config: Dict[str, Any]
    status: str

class AccessRequest(BaseModel):
    agent_id: str
    level: str = "read"  # read, write, admin
    allowed_tags: List[str] = Field(default_factory=list)  # extra tags this agent may see

class IngestRequest(BaseModel):
    data: Any
    agent_id: str
    data_type: str = "auto"
    metadata: Dict[str, Any] = Field(default_factory=dict)
    source: Optional[str] = None
    mime_type: Optional[str] = None
    filename: Optional[str] = None
    tags: List[str] = Field(default_factory=list)
    sensitivity: Optional[str] = None           # public|internal|confidential|restricted
    auto_pii_scan: bool = True

class ContextItemRequest(BaseModel):
    content: str
    agent_id: str
    role: str = "user"
    label: Optional[str] = None
    source: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)
    tags: List[str] = Field(default_factory=list)
    sensitivity: str = "public"

class DocumentProcessRequest(BaseModel):
    file_path: str
    agent_id: str
    extract_tables: bool = True
    extract_images: bool = True
    chunk_size: Optional[int] = None
    chunk_overlap: Optional[int] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)

class ContributeRequest(BaseModel):
    content: str
    agent_id: Optional[str] = None  # auto-filled from auth if omitted
    role: str = "generated"  # generated, synthesis, decision
    label: Optional[str] = None
    source_ids: List[str] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)
    tags: List[str] = Field(default_factory=list)
    sensitivity: str = "internal"

class QueryRequest(BaseModel):
    query: str
    agent_id: Optional[str] = None
    limit: int = 50

class SemanticSearchRequest(BaseModel):
    query: str
    k: int = 10
    agent_id: Optional[str] = None


# --- Phase 2 models ---

class ScopingConfigRequest(BaseModel):
    max_tokens: int = 8000
    strategy: str = "combined"
    role_weights: Dict[str, float] = Field(default_factory=dict)
    recency_decay: float = 0.95
    reserve_system: bool = True

class ConversationCreateRequest(BaseModel):
    session_id: str
    title: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)
    ttl_seconds: Optional[int] = None

class ConversationMessageRequest(BaseModel):
    role: str  # user | assistant | system | tool
    content: str
    agent_id: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)

class ConversationKVRequest(BaseModel):
    key: str
    value: Any


# ======================================================================
# Router factory
# ======================================================================

def create_context_router(graph_registry=None) -> APIRouter:
    """
    Build and return the ``/context`` router.

    Instantiates the shared singletons (AgentRegistry, SessionManager, etc.)
    so they live for the lifetime of the FastAPI app.
    """
    router = APIRouter(prefix="/context", tags=["Context Service"])

    # Shared state
    agent_registry = create_agent_registry()
    session_manager = ContextSessionManager(graph_registry=graph_registry)
    blob_store = BlobStore()

    # Vector store — auto-embed on ingest, semantic search
    vector_store = SessionVectorStore()
    dedup = ContextDedup(vector_store=vector_store)

    # Phase 2: embedding hooks, conversation store
    embedding_hooks = EmbeddingHooks(vector_store=vector_store)
    conversation_store = ConversationStore()

    ingestor = UnifiedIngestor(
        session_manager=session_manager,
        agent_registry=agent_registry,
        blob_store=blob_store,
        embedding_hooks=embedding_hooks,
    )
    doc_processor = DocumentProcessor(
        session_manager=session_manager,
        agent_registry=agent_registry,
        graph_registry=graph_registry,
        blob_store=blob_store,
    )

    # Per-session scoping configs
    _scoping_configs: Dict[str, ScopingConfig] = {}

    # Session graph builders (session_id -> SessionGraphBuilder)
    _session_builders: Dict[str, SessionGraphBuilder] = {}

    async def _get_or_create_builder(session_id: str) -> SessionGraphBuilder:
        """Lazily get or create a SessionGraphBuilder for a session."""
        if session_id not in _session_builders:
            session = session_manager.get_session(session_id)
            if session:
                builder = SessionGraphBuilder(session, graph_registry)
                await builder.initialize()
                _session_builders[session_id] = builder
        return _session_builders.get(session_id)

    # Auth dependency — inject into protected endpoints
    from .auth import require_agent_capability
    require_agent = AgentAuth(agent_registry)

    # RBAC: capability-gated dependencies
    require_read        = require_agent_capability(require_agent, "read")
    require_write       = require_agent_capability(require_agent, "read", "write")
    require_agent_admin = require_agent_capability(require_agent, "read", "write", "admin")

    # Per-session ContextHub cache (session_id -> ContextHub)
    _hubs: Dict[str, ContextHub] = {}

    def _get_hub(session_id: str) -> ContextHub:
        if session_id not in _hubs:
            _hubs[session_id] = ContextHub()
        return _hubs[session_id]

    # ==================================================================
    # Agent endpoints (registration is PUBLIC, others require auth)
    # ==================================================================

    @router.post("/agents", response_model=AgentRegisterResponse)
    async def register_agent(req: AgentRegisterRequest):
        agent, api_key = agent_registry.register(
            name=req.name,
            role=req.role,
            platform=req.platform,
            capabilities=req.capabilities,
            metadata=req.metadata,
        )
        # Return composite key: "<agent_id>:<secret>"
        # Clients use this as: Authorization: Bearer <agent_id>:<secret>
        composite_key = f"{agent.agent_id}:{api_key}"
        return AgentRegisterResponse(
            **agent.to_dict(),
            api_key=composite_key,
        )

    @router.get("/agents", response_model=List[AgentResponse])
    async def list_agents(caller: AgentIdentity = Depends(require_read)):
        agents = agent_registry.list_agents()
        return [AgentResponse(**a.to_dict()) for a in agents]

    @router.get("/agents/{agent_id}", response_model=AgentResponse)
    async def get_agent(agent_id: str, caller: AgentIdentity = Depends(require_read)):
        agent = agent_registry.get(agent_id)
        if not agent:
            raise HTTPException(404, "Agent not found")
        return AgentResponse(**agent.to_dict())

    @router.delete("/agents/{agent_id}")
    async def deregister_agent(agent_id: str, caller: AgentIdentity = Depends(require_agent_admin)):
        # Only allow self-deregistration or admin
        if caller.agent_id != agent_id and caller.role != "admin":
            raise HTTPException(403, "Can only deregister yourself unless admin")
        ok = agent_registry.deregister(agent_id)
        if not ok:
            raise HTTPException(404, "Agent not found")
        return {"success": True, "message": f"Agent {agent_id} deregistered"}

    # ==================================================================
    # Session endpoints
    # ==================================================================

    @router.post("/sessions", response_model=SessionResponse)
    async def create_session(req: SessionCreateRequest, caller: AgentIdentity = Depends(require_agent_admin)):
        # Merge goal into config if provided
        config = dict(req.config)
        if req.goal:
            config["goal"] = req.goal

        try:
            session = session_manager.create_session(
                name=req.name,
                owner_agent_id=req.agent_id or caller.agent_id,
                config=config,
            )
        except Exception as e:
            raise HTTPException(400, str(e))

        # Initialize session graph builder (unified knowledge + conversation layer)
        try:
            builder = SessionGraphBuilder(session, graph_registry)
            await builder.initialize()
            _session_builders[session.session_id] = builder
        except Exception as e:
            logger.warning("Failed to initialize session graph builder: %s", e)

        # Auto-attach context and create thread if context_id provided
        if req.context_id:
            try:
                session_manager.attach_context(session.session_id, req.context_id)

                # Create thread conversation
                thread = conversation_store.create(
                    session_id=session.session_id,
                    title="Thread",
                    metadata={"type": "sdlc_thread", "context_id": req.context_id, "goal": req.goal or ""},
                )

                # Store thread_id in session config
                config["thread_id"] = thread.conversation_id
                config["context_id"] = req.context_id
                session_manager.update_session(session.session_id, config=config)

                # Post initial system message with context stats
                doc_count = 0
                chunk_count = 0
                ctx_name = req.context_id
                try:
                    ns = session.graph_namespace
                    db = graph_registry.get_graph(ns, load_if_missing=True)
                    if db:
                        for n in db.get_all_nodes():
                            if n.label in ("Document", "Source"):
                                doc_count += 1
                            elif n.label in ("TextChunk", "Chunk", "Passage"):
                                chunk_count += 1
                except Exception:
                    pass  # optional

                goal_text = f" Goal: {req.goal}" if req.goal else ""
                conversation_store.append_message(
                    conversation_id=thread.conversation_id,
                    role="system",
                    content=(
                        f"Session started.{goal_text} "
                        f"Context '{ctx_name}' attached with {doc_count} documents, "
                        f"{chunk_count} chunks available via RAG."
                    ),
                    agent_id=None,
                    metadata={"type": "system_event", "event": "session_started"},
                )
                logger.info("Auto-created thread %s for session %s", thread.conversation_id, session.session_id)
            except Exception as e:
                logger.warning("Failed to auto-create thread for session: %s", e)

        return SessionResponse(**session.to_dict())

    @router.get("/sessions", response_model=List[SessionResponse])
    async def list_sessions(agent_id: Optional[str] = Query(None), caller: AgentIdentity = Depends(require_read)):
        sessions = session_manager.list_sessions(agent_id=agent_id)
        return [SessionResponse(**s.to_dict()) for s in sessions]

    @router.get("/sessions/{session_id}", response_model=SessionResponse)
    async def get_session(session_id: str, caller: AgentIdentity = Depends(require_read)):
        session = session_manager.get_session(session_id)
        if not session:
            raise HTTPException(404, "Session not found")
        return SessionResponse(**session.to_dict())

    @router.delete("/sessions/{session_id}")
    async def delete_session(session_id: str, caller: AgentIdentity = Depends(require_agent_admin)):
        ok = session_manager.delete_session(session_id)
        if not ok:
            raise HTTPException(404, "Session not found")
        _hubs.pop(session_id, None)
        vector_store.delete_session(session_id)
        return {"success": True, "message": "Session deleted"}

    # --- access control ---

    @router.post("/sessions/{session_id}/access")
    async def grant_access(session_id: str, req: AccessRequest, caller: AgentIdentity = Depends(require_agent_admin)):
        session = session_manager.get_session(session_id)
        if not session:
            raise HTTPException(404, "Session not found")
        session_manager.grant_access(
            session_id, req.agent_id, req.level, allowed_tags=req.allowed_tags,
            agent_registry=agent_registry,
        )
        return {
            "success": True,
            "agent_id": req.agent_id,
            "level": req.level,
            "allowed_tags": req.allowed_tags,
        }

    @router.delete("/sessions/{session_id}/access/{agent_id}")
    async def revoke_access(session_id: str, agent_id: str, caller: AgentIdentity = Depends(require_agent_admin)):
        ok = session_manager.revoke_access(session_id, agent_id)
        if not ok:
            raise HTTPException(404, "Access entry not found")
        return {"success": True}

    @router.get("/sessions/{session_id}/access")
    async def get_access_list(session_id: str, caller: AgentIdentity = Depends(require_agent_admin)):
        return session_manager.get_access_list(session_id)

    # ==================================================================
    # Join requests (approval gate)
    # ==================================================================

    @router.post("/sessions/{session_id}/join")
    async def request_join(session_id: str, level: str = "read", reason: str = None, caller: AgentIdentity = Depends(require_read)):
        """Agent requests to join a session. Goes to pending queue until approved."""
        session = session_manager.get_session(session_id)
        if not session:
            raise HTTPException(404, "Session not found")
        result = session_manager.request_join(session_id, caller.agent_id, level, reason)
        return result

    @router.get("/sessions/{session_id}/join-requests")
    async def list_join_requests(session_id: str, caller: AgentIdentity = Depends(require_agent_admin)):
        """List pending join requests (admin only)."""
        requests = session_manager.get_pending_requests(session_id)
        enriched = []
        for req in requests:
            entry = dict(req)
            agent = agent_registry.get(req["agent_id"])
            if agent:
                entry["agent"] = agent.to_dict()
            enriched.append(entry)
        return {"requests": enriched, "total": len(enriched)}

    @router.post("/sessions/{session_id}/join-requests/{request_id}/approve")
    async def approve_join(session_id: str, request_id: str, level: str = None, caller: AgentIdentity = Depends(require_agent_admin)):
        """Approve a pending join request."""
        ok = session_manager.approve_join(request_id, caller.agent_id, level)
        if not ok:
            raise HTTPException(404, "Request not found or already reviewed")
        return {"status": "approved", "request_id": request_id}

    @router.post("/sessions/{session_id}/join-requests/{request_id}/deny")
    async def deny_join(session_id: str, request_id: str, caller: AgentIdentity = Depends(require_agent_admin)):
        """Deny a pending join request."""
        ok = session_manager.deny_join(request_id, caller.agent_id)
        if not ok:
            raise HTTPException(404, "Request not found or already reviewed")
        return {"status": "denied", "request_id": request_id}

    # ==================================================================
    # Ingest endpoints
    # ==================================================================

    @router.post("/sessions/{session_id}/ingest")
    async def ingest_data(session_id: str, req: IngestRequest, caller: AgentIdentity = Depends(require_write)):
        from ..ingestion.amplifier import build_context_purpose

        session = session_manager.get_session(session_id)
        if not session:
            raise HTTPException(404, "Session not found")

        # Auto-detect context purpose from session metadata for amplifier scoring
        ctx_meta = session.config.get("context_meta", {}) if hasattr(session, "config") and session.config else {}
        context_purpose = build_context_purpose(
            context_name=ctx_meta.get("name", session.name if hasattr(session, "name") else ""),
            context_description=ctx_meta.get("description", ""),
            tags=ctx_meta.get("tags", req.tags or []),
        )

        # --- Dedup check ---
        text_repr = str(req.data) if not isinstance(req.data, str) else req.data
        dup = dedup.check(session_id, text_repr)
        if dup.is_duplicate:
            return {
                "success": False,
                "duplicate": True,
                "match_type": dup.match_type,
                "matched_id": dup.matched_id,
                "score": dup.score,
                "message": f"Duplicate detected ({dup.match_type}, score={dup.score:.3f})",
            }

        # Inject context_purpose into metadata so it propagates downstream
        enriched_metadata = {**(req.metadata or {})}
        if context_purpose:
            enriched_metadata["_context_purpose"] = context_purpose

        try:
            result = ingestor.ingest(
                session_id=session_id,
                agent_id=req.agent_id,
                data=req.data,
                data_type=req.data_type,
                metadata=enriched_metadata,
                source=req.source,
                mime_type=req.mime_type,
                filename=req.filename,
                tags=req.tags,
                sensitivity=req.sensitivity,
                auto_pii_scan=req.auto_pii_scan,
            )
        except ValueError as e:
            raise HTTPException(400, str(e))
        except Exception as e:
            logger.error(f"Ingest error: {e}")
            raise HTTPException(500, f"Ingestion failed: {e}")

        # Auto-embed ingested text into vector store
        embedded = False
        if vector_store.available and result.get("node_ids"):
            if text_repr and len(text_repr.strip()) > 0:
                for nid in result["node_ids"]:
                    vector_store.add_text(
                        session_id=session_id,
                        text=text_repr,
                        node_id=nid,
                        metadata={"agent_id": req.agent_id, "source": req.source or "ingest"},
                    )
                embedded = True

        # Register content hash so future duplicates are caught
        for nid in result.get("node_ids", []):
            dedup.register(session_id, nid, text_repr)

        # Publish event
        await pubsub_hub.publish(ContextEvent(
            event_type="context_added",
            session_id=session_id,
            agent_id=req.agent_id,
            payload={"data_type": result["data_type"], "node_ids": result["node_ids"]},
        ))

        return {"success": True, "embedded": embedded, **result}

    # ==================================================================
    # Document processing endpoints
    # ==================================================================

    @router.post("/sessions/{session_id}/documents")
    async def process_document(session_id: str, req: DocumentProcessRequest, caller: AgentIdentity = Depends(require_write)):
        """Process a document (PDF, CSV, TXT, etc.) into the session's graph.

        Extracts text, tables, images → creates Source, Document, Chunk, Table,
        Image nodes with edges → permanent context.
        """
        session = session_manager.get_session(session_id)
        if not session:
            raise HTTPException(404, "Session not found")

        try:
            result = doc_processor.process(
                session_id=session_id,
                agent_id=req.agent_id,
                file_path=req.file_path,
                extract_tables=req.extract_tables,
                extract_images=req.extract_images,
                chunk_size=req.chunk_size,
                chunk_overlap=req.chunk_overlap,
                metadata=req.metadata,
            )
        except FileNotFoundError as e:
            raise HTTPException(404, str(e))
        except Exception as e:
            logger.error(f"Document processing error: {e}")
            raise HTTPException(500, f"Document processing failed: {e}")

        await pubsub_hub.publish(ContextEvent(
            event_type="context_added",
            session_id=session_id,
            agent_id=req.agent_id,
            payload={
                "type": "document",
                "document_id": result["document_id"],
                "chunk_count": result["chunk_count"],
                "table_count": result["table_count"],
                "image_count": result["image_count"],
            },
        ))

        return {"success": True, **result}

    @router.post("/sessions/{session_id}/documents/upload")
    async def upload_document(
        session_id: str,
        file: UploadFile = File(...),
        agent_id: str = Query(...),
        extract_tables: bool = Query(True),
        extract_images: bool = Query(True),
        caller: AgentIdentity = Depends(require_write),
    ):
        """Upload a document file and process it into the session's graph."""
        session = session_manager.get_session(session_id)
        if not session:
            raise HTTPException(404, "Session not found")

        raw_bytes = await file.read()
        try:
            result = doc_processor.process(
                session_id=session_id,
                agent_id=agent_id,
                raw_bytes=raw_bytes,
                filename=file.filename or "upload",
                extract_tables=extract_tables,
                extract_images=extract_images,
            )
        except Exception as e:
            logger.error(f"Document upload processing error: {e}")
            raise HTTPException(500, f"Document processing failed: {e}")

        await pubsub_hub.publish(ContextEvent(
            event_type="context_added",
            session_id=session_id,
            agent_id=agent_id,
            payload={
                "type": "document",
                "document_id": result["document_id"],
                "chunk_count": result["chunk_count"],
            },
        ))

        return {"success": True, **result}

    # ==================================================================
    # Context item endpoints
    # ==================================================================

    @router.post("/sessions/{session_id}/items")
    async def add_context_item(session_id: str, req: ContextItemRequest, caller: AgentIdentity = Depends(require_write)):
        session = session_manager.get_session(session_id)
        if not session:
            raise HTTPException(404, "Session not found")

        hub = _get_hub(session_id)
        hub.add_text(
            text=req.content,
            role=req.role,
            label=req.label,
            source=req.source,
            metadata={**req.metadata, "agent_id": req.agent_id},
            tags=req.tags,
            sensitivity=req.sensitivity,
        )
        # Tag the last item with agent_id
        hub._items[-1].agent_id = req.agent_id

        await pubsub_hub.publish(ContextEvent(
            event_type="context_added",
            session_id=session_id,
            agent_id=req.agent_id,
            payload={"label": req.label, "role": req.role},
        ))

        return {"success": True, "item_count": len(hub)}

    @router.get("/sessions/{session_id}/items")
    async def list_context_items(
        session_id: str,
        role: Optional[str] = Query(None),
        agent_id: Optional[str] = Query(None),
        source: Optional[str] = Query(None),
        label: Optional[str] = Query(None),
        tag: Optional[str] = Query(None, description="Filter by tag"),
        sensitivity: Optional[str] = Query(None),
        caller: AgentIdentity = Depends(require_read),
    ):
        hub = _get_hub(session_id)

        # First apply authorization filter based on caller's access
        access_info = session_manager.get_agent_access(session_id, caller.agent_id)
        if access_info:
            access_level = access_info["access_level"]
            allowed_tags = access_info["allowed_tags"]
        else:
            access_level = "read"
            allowed_tags = []

        visible_items = hub.filter_for_agent(access_level, allowed_tags)

        # Then apply user-specified filters on visible items
        filtered_hub = ContextHub()
        filtered_hub._items = visible_items
        items = filtered_hub.filter(
            role=role, agent_id=agent_id, source=source, label=label,
            tags=[tag] if tag else None, sensitivity=sensitivity,
        )
        return {"items": [i.to_dict() for i in items], "count": len(items)}

    # ==================================================================
    # Agent contributions (write outputs back into shared context)
    # ==================================================================

    @router.post("/sessions/{session_id}/contribute")
    async def contribute(session_id: str, req: ContributeRequest, caller: AgentIdentity = Depends(require_write)):
        """
        Agent writes its output (summary, analysis, decision) back into the
        session.  The output is:

        1. Added to the in-memory ContextHub (available via /export)
        2. Persisted as an ``Insight`` graph node (survives restarts)
        3. Linked to source nodes via ``DERIVED_FROM`` edges (lineage)
        4. Broadcast to subscribers via WebSocket
        """
        session = session_manager.get_session(session_id)
        if not session:
            raise HTTPException(404, "Session not found")

        # Verify agent has write access to this session
        if session.owner_agent_id != caller.agent_id:
            if not session_manager.check_access(session_id, caller.agent_id, "write"):
                raise HTTPException(403, "No write access to this session")

        # Auto-fill agent_id from authenticated caller if not provided
        if not req.agent_id:
            req.agent_id = caller.agent_id

        # Dedup check on contributions too
        dup = dedup.check(session_id, req.content)
        if dup.is_duplicate:
            return {
                "success": False,
                "duplicate": True,
                "match_type": dup.match_type,
                "matched_id": dup.matched_id,
                "score": dup.score,
                "message": f"Duplicate contribution ({dup.match_type}, score={dup.score:.3f})",
            }

        # 1. Add to hub
        hub = _get_hub(session_id)
        hub.contribute(
            content=req.content,
            agent_id=req.agent_id,
            role=req.role,
            label=req.label,
            source_ids=req.source_ids,
            metadata=req.metadata,
            tags=req.tags,
            sensitivity=req.sensitivity,
        )

        # 2. Persist as Insight node in graph
        insight_node_id = None
        if graph_registry:
            graph = graph_registry.get_graph(session.graph_namespace, load_if_missing=True)
            if graph:
                import uuid as _uuid
                from ..core.graph_structures import GraphNode, GraphEdge

                insight_node_id = str(_uuid.uuid4())
                node = GraphNode(
                    id=insight_node_id,
                    label="Insight",
                    properties={
                        "content": req.content,
                        "insight_type": req.role,
                        "derived_from": req.source_ids,
                        "_ctx_session_id": session.session_id,
                        "_ctx_agent_id": req.agent_id,
                        "_ctx_source": "contribute",
                        **req.metadata,
                    },
                )
                graph.add_node(node)

                # 3. Create DERIVED_FROM edges to source nodes
                for src_id in req.source_ids:
                    try:
                        edge = GraphEdge(
                            id=str(_uuid.uuid4()),
                            source=insight_node_id,
                            target=src_id,
                            label="DERIVED_FROM",
                            properties={"agent_id": req.agent_id},
                        )
                        graph.add_edge(edge)
                    except Exception:
                        pass  # source node may not exist

                # Save
                graph_registry.save_graph(session.graph_namespace, create_checkpoint=False)

        # Provenance
        if insight_node_id:
            agent_registry.record_provenance(ProvenanceRecord(
                session_id=session_id,
                agent_id=req.agent_id,
                operation="contribute",
                target_type="insight",
                target_id=insight_node_id,
                source_ids=req.source_ids,
                metadata={"role": req.role},
            ))

        # 4. Auto-embed the contribution into vector store
        if vector_store.available and insight_node_id:
            vector_store.add_text(
                session_id=session_id,
                text=req.content,
                node_id=insight_node_id,
                metadata={"agent_id": req.agent_id, "role": req.role, "source": "contribute"},
            )

        # Register hash for dedup
        if insight_node_id:
            dedup.register(session_id, insight_node_id, req.content)

        # 5. Broadcast
        await pubsub_hub.publish(ContextEvent(
            event_type="context_added",
            session_id=session_id,
            agent_id=req.agent_id,
            payload={
                "type": "insight",
                "role": req.role,
                "label": req.label,
                "insight_node_id": insight_node_id,
                "source_ids": req.source_ids,
            },
        ))

        session_manager.touch_session(session_id)

        return {
            "success": True,
            "insight_node_id": insight_node_id,
            "item_count": len(hub),
        }

    # ==================================================================
    # Export / query endpoints
    # ==================================================================

    @router.get("/sessions/{session_id}/export")
    async def export_context(
        session_id: str,
        format: str = Query("messages", description="messages|prompt|markdown|dict"),
        pii_mode: str = Query("block", description="block|mask|redact|allow"),
        max_tokens: Optional[int] = Query(None, description="Token budget (applies scoping)"),
        strategy: Optional[str] = Query(None, description="Scoping strategy: recency|relevance|role_priority|combined"),
        caller: AgentIdentity = Depends(require_read),
    ):
        """Export context filtered by the calling agent's access level + tag grants."""
        session = session_manager.get_session(session_id)
        if not session:
            raise HTTPException(404, "Session not found")

        # Verify agent has access to this session
        if session.owner_agent_id != caller.agent_id:
            if not session_manager.check_access(session_id, caller.agent_id, "read"):
                raise HTTPException(403, "No access to this session")

        if pii_mode not in ("block", "mask", "redact", "allow"):
            raise HTTPException(400, "pii_mode must be one of: block, mask, redact, allow")

        hub = _get_hub(session_id)

        # Also pull graph nodes into the hub if it's empty
        if len(hub) == 0 and graph_registry:
            graph = graph_registry.get_graph(session.graph_namespace, load_if_missing=True)
            if graph:
                try:
                    all_nodes = graph.get_all_nodes()
                    if all_nodes:
                        hub.add_nodes(all_nodes, label="Session Graph Nodes")
                except Exception:
                    pass  # optional

        # --- Authorization-filtered export ---
        access_info = session_manager.get_agent_access(session_id, caller.agent_id)
        if access_info:
            access_level = access_info["access_level"]
            allowed_tags = access_info["allowed_tags"]
        else:
            access_level = "read"
            allowed_tags = []

        # Build a filtered hub containing only items this agent may see
        visible_items = hub.filter_for_agent(access_level, allowed_tags, pii_mode=pii_mode)
        filtered_hub = ContextHub(max_tokens=hub.max_tokens)
        filtered_hub._items = visible_items

        # Apply scoping if requested
        budget = max_tokens
        if budget or strategy or session_id in _scoping_configs:
            cfg = _scoping_configs.get(session_id, ScopingConfig())
            if strategy:
                cfg.strategy = strategy
            if budget:
                cfg.max_tokens = budget
            filtered_hub.set_scoping(cfg)

        if format in ("messages", "prompt"):
            return filtered_hub.export(format) if not budget else (
                filtered_hub.to_messages(budget=budget) if format == "messages"
                else filtered_hub.to_prompt(budget=budget)
            )
        return filtered_hub.export(format)

    @router.post("/sessions/{session_id}/query")
    async def query_session(session_id: str, req: QueryRequest, caller: AgentIdentity = Depends(require_read)):
        """Execute an AIQL query scoped to the session's graph namespace."""
        session = session_manager.get_session(session_id)
        if not session:
            raise HTTPException(404, "Session not found")

        # Verify agent has access to this session
        if session.owner_agent_id != caller.agent_id:
            if not session_manager.check_access(session_id, caller.agent_id, "read"):
                raise HTTPException(403, "No access to this session")

        if not graph_registry:
            raise HTTPException(500, "Graph registry not available")

        graph = graph_registry.get_graph(session.graph_namespace, load_if_missing=True)
        if not graph:
            # Create graph on first query if it doesn't exist yet
            graph = graph_registry.create_graph(session.graph_namespace)
        if not graph:
            return {"nodes": [], "edges": [], "message": "Could not create graph"}

        try:
            from ..aiql.engine import AIQLExecutor

            ex = AIQLExecutor(contextcore=graph, graph_registry=graph_registry)
            ex.active_namespace = session.graph_namespace
            result = ex.execute(req.query)

            # Persist after write operations
            query_upper = req.query.strip().upper()
            if query_upper.startswith("CREATE") or query_upper.startswith("DELETE") or query_upper.startswith("UPDATE"):
                try:
                    graph_registry.save_graph(session.graph_namespace, create_checkpoint=False)
                except Exception:
                    pass  # optional

            return {"success": True, "data": result}
        except Exception as e:
            raise HTTPException(400, f"Query failed: {e}")

    # ==================================================================
    # Semantic search (vector DB)
    # ==================================================================

    @router.post("/sessions/{session_id}/search")
    async def semantic_search(
        session_id: str,
        req: SemanticSearchRequest,
        caller: AgentIdentity = Depends(require_read),
    ):
        """Semantic similarity search across a session's embedded context."""
        session = session_manager.get_session(session_id)
        if not session:
            raise HTTPException(404, "Session not found")

        if not vector_store.available:
            raise HTTPException(503, "Vector search unavailable (no vector backend)")

        results = vector_store.search(
            session_id=session_id,
            query=req.query,
            k=req.k,
        )

        return {"success": True, "results": results, "count": len(results)}

    @router.get("/sessions/{session_id}/vectors/stats")
    async def vector_stats(session_id: str, caller: AgentIdentity = Depends(require_read)):
        """Get vector store statistics for a session."""
        return vector_store.get_stats(session_id)

    # ==================================================================
    # Provenance
    # ==================================================================

    @router.get("/sessions/{session_id}/provenance")
    async def get_provenance(
        session_id: str,
        agent_id: Optional[str] = Query(None),
        limit: int = Query(100),
        caller: AgentIdentity = Depends(require_read),
    ):
        records = agent_registry.get_provenance(
            session_id=session_id,
            agent_id=agent_id,
            limit=limit,
        )
        return {"provenance": records, "count": len(records)}

    # ==================================================================
    # Blob endpoints
    # ==================================================================

    @router.post("/sessions/{session_id}/blobs")
    async def upload_blob(
        session_id: str,
        file: UploadFile = File(...),
        agent_id: str = Query(...),
        caller: AgentIdentity = Depends(require_read),
    ):
        session = session_manager.get_session(session_id)
        if not session:
            raise HTTPException(404, "Session not found")

        data = await file.read()
        mime = file.content_type or "application/octet-stream"

        blob_info = blob_store.store(
            session_id=session_id,
            data=data,
            filename=file.filename or "upload",
            mime_type=mime,
            agent_id=agent_id,
        )

        agent_registry.record_provenance(ProvenanceRecord(
            session_id=session_id,
            agent_id=agent_id,
            operation="ingest",
            target_type="blob",
            target_id=blob_info["blob_id"],
        ))

        await pubsub_hub.publish(ContextEvent(
            event_type="context_added",
            session_id=session_id,
            agent_id=agent_id,
            payload={"blob_id": blob_info["blob_id"], "filename": file.filename},
        ))

        return {"success": True, **blob_info}

    @router.get("/sessions/{session_id}/blobs")
    async def list_blobs(session_id: str, caller: AgentIdentity = Depends(require_read)):
        return blob_store.list_blobs(session_id)

    @router.get("/sessions/{session_id}/blobs/{blob_id}")
    async def get_blob(session_id: str, blob_id: str, caller: AgentIdentity = Depends(require_read)):
        result = blob_store.get(blob_id)
        if not result:
            raise HTTPException(404, "Blob not found")
        raw_bytes, meta = result
        from fastapi.responses import Response
        return Response(
            content=raw_bytes,
            media_type=meta.get("mime_type", "application/octet-stream"),
            headers={"Content-Disposition": f'attachment; filename="{meta.get("filename", "download")}"'},
        )

    @router.delete("/sessions/{session_id}/blobs/{blob_id}")
    async def delete_blob(session_id: str, blob_id: str, caller: AgentIdentity = Depends(require_write)):
        ok = blob_store.delete(blob_id)
        if not ok:
            raise HTTPException(404, "Blob not found")
        return {"success": True}

    # ==================================================================
    # WebSocket — real-time context subscription
    # ==================================================================

    @router.websocket("/sessions/{session_id}/ws")
    async def websocket_subscribe(websocket: WebSocket, session_id: str):
        await websocket.accept()

        # Expect first message to be {"agent_id": "..."}
        try:
            init = await websocket.receive_json()
            agent_id = init.get("agent_id", "anonymous")
        except Exception:
            agent_id = "anonymous"

        conn_id = secrets.token_hex(8)
        await pubsub_hub.subscribe(
            session_id=session_id,
            connection_id=conn_id,
            agent_id=agent_id,
            send_fn=websocket.send_text,
        )

        try:
            while True:
                # Keep connection alive; clients can also send messages
                data = await websocket.receive_text()
                # Echo back or handle client-side events if needed
        except WebSocketDisconnect:
            await pubsub_hub.unsubscribe(conn_id)

    # ==================================================================
    # Scoping config endpoints
    # ==================================================================

    @router.put("/sessions/{session_id}/scoping")
    async def set_scoping_config(
        session_id: str, req: ScopingConfigRequest,
        caller: AgentIdentity = Depends(require_agent_admin),
    ):
        """Set token-budget scoping config for a session (requires admin)."""
        session = session_manager.get_session(session_id)
        if not session:
            raise HTTPException(404, "Session not found")
        cfg = ScopingConfig(
            max_tokens=req.max_tokens,
            strategy=req.strategy,
            role_weights=req.role_weights or dict(ScopingConfig().role_weights),
            recency_decay=req.recency_decay,
            reserve_system=req.reserve_system,
        )
        _scoping_configs[session_id] = cfg
        return {"success": True, "config": {
            "max_tokens": cfg.max_tokens,
            "strategy": cfg.strategy,
            "recency_decay": cfg.recency_decay,
            "reserve_system": cfg.reserve_system,
        }}

    @router.get("/sessions/{session_id}/scoping")
    async def get_scoping_config(session_id: str, caller: AgentIdentity = Depends(require_read)):
        """Get current scoping config for a session."""
        cfg = _scoping_configs.get(session_id)
        if not cfg:
            return {"configured": False, "config": None}
        return {"configured": True, "config": {
            "max_tokens": cfg.max_tokens,
            "strategy": cfg.strategy,
            "recency_decay": cfg.recency_decay,
            "reserve_system": cfg.reserve_system,
        }}

    # ==================================================================
    # Conversation endpoints
    # ==================================================================

    @router.post("/conversations")
    async def create_conversation(
        req: ConversationCreateRequest,
        caller: AgentIdentity = Depends(require_write),
    ):
        """Create a new conversation tied to a session."""
        session = session_manager.get_session(req.session_id)
        if not session:
            raise HTTPException(404, "Session not found")
        conv = conversation_store.create(
            session_id=req.session_id,
            title=req.title,
            metadata=req.metadata,
            ttl_seconds=req.ttl_seconds,
        )
        return {"success": True, **conv.to_dict()}

    @router.get("/conversations")
    async def list_conversations(
        session_id: str = Query(...),
        status: str = Query("active"),
        caller: AgentIdentity = Depends(require_read),
    ):
        convs = conversation_store.list_conversations(session_id, status=status)
        return {"conversations": [c.to_dict() for c in convs], "count": len(convs)}

    @router.get("/conversations/{conversation_id}")
    async def get_conversation(
        conversation_id: str,
        caller: AgentIdentity = Depends(require_read),
    ):
        conv = conversation_store.get(conversation_id)
        if not conv:
            raise HTTPException(404, "Conversation not found")
        return {
            **conv.to_dict(),
            "messages": [m.to_dict() for m in conv.messages],
        }

    @router.delete("/conversations/{conversation_id}")
    async def delete_conversation(
        conversation_id: str,
        caller: AgentIdentity = Depends(require_agent_admin),
    ):
        ok = conversation_store.delete(conversation_id)
        if not ok:
            raise HTTPException(404, "Conversation not found")
        return {"success": True}

    @router.post("/conversations/{conversation_id}/messages")
    async def append_conversation_message(
        conversation_id: str,
        req: ConversationMessageRequest,
        background_tasks: BackgroundTasks,
        caller: AgentIdentity = Depends(require_write),
    ):
        """Append a message to a conversation.

        Also graphifies the interaction into the session's unified graph
        (fire-and-forget background task) so agents can later traverse
        from conversation turns to knowledge entities.
        """
        conv = conversation_store.get(conversation_id, include_messages=False)
        if not conv:
            raise HTTPException(404, "Conversation not found")
        msg = conversation_store.append_message(
            conversation_id=conversation_id,
            role=req.role,
            content=req.content,
            agent_id=req.agent_id,
            metadata=req.metadata,
        )

        # Fire-and-forget: graphify interaction into session graph
        if hasattr(conv, "session_id") and conv.session_id:
            builder = await _get_or_create_builder(conv.session_id)
            if builder:
                background_tasks.add_task(
                    builder.graphify_interaction,
                    role=req.role,
                    content=req.content,
                    agent_id=req.agent_id or caller.agent_id,
                    conversation_id=conversation_id,
                )

        return {"success": True, **msg.to_dict()}

    @router.get("/conversations/{conversation_id}/messages")
    async def get_conversation_messages(
        conversation_id: str,
        limit: Optional[int] = Query(None),
        offset: int = Query(0),
        caller: AgentIdentity = Depends(require_read),
    ):
        messages = conversation_store.get_history(conversation_id, limit=limit, offset=offset)
        return {"messages": [m.to_dict() for m in messages], "count": len(messages)}

    @router.put("/conversations/{conversation_id}/kv")
    async def set_conversation_kv(
        conversation_id: str,
        req: ConversationKVRequest,
        caller: AgentIdentity = Depends(require_write),
    ):
        """Set a key-value pair on a conversation."""
        conv = conversation_store.get(conversation_id, include_messages=False)
        if not conv:
            raise HTTPException(404, "Conversation not found")
        conversation_store.set_kv(conversation_id, req.key, req.value)
        return {"success": True, "key": req.key}

    @router.get("/conversations/{conversation_id}/kv")
    async def get_conversation_kv_all(
        conversation_id: str,
        caller: AgentIdentity = Depends(require_read),
    ):
        """Get all KV pairs for a conversation."""
        return conversation_store.get_all_kv(conversation_id)

    @router.get("/conversations/{conversation_id}/kv/{key}")
    async def get_conversation_kv(
        conversation_id: str,
        key: str,
        caller: AgentIdentity = Depends(require_read),
    ):
        """Get a single KV value."""
        value = conversation_store.get_kv(conversation_id, key)
        if value is None:
            raise HTTPException(404, f"Key '{key}' not found")
        return {"key": key, "value": value}

    @router.delete("/conversations/{conversation_id}/kv/{key}")
    async def delete_conversation_kv(
        conversation_id: str,
        key: str,
        caller: AgentIdentity = Depends(require_write),
    ):
        ok = conversation_store.delete_kv(conversation_id, key)
        if not ok:
            raise HTTPException(404, f"Key '{key}' not found")
        return {"success": True}

    @router.get("/conversations/{conversation_id}/export")
    async def export_conversation(
        conversation_id: str,
        format: str = Query("messages", description="messages|prompt|markdown|dict"),
        last_n: Optional[int] = Query(None, description="Only export last N messages"),
        system_prompt: Optional[str] = Query(None),
        caller: AgentIdentity = Depends(require_read),
    ):
        """Export a conversation as a ContextHub format."""
        conv = conversation_store.get(conversation_id, include_messages=False)
        if not conv:
            raise HTTPException(404, "Conversation not found")
        hub = conversation_store.to_hub(conversation_id, system_prompt=system_prompt, last_n=last_n)
        return hub.export(format)

    # ==================================================================
    # Embedding capabilities
    # ==================================================================

    @router.get("/capabilities")
    async def get_capabilities():
        """Report available embedding/extraction capabilities."""
        return {
            **embedding_hooks.capabilities,
            "embedding_service": vector_store._embedding_service is not None,
        }

    # ==================================================================
    # Health / info
    # ==================================================================

    @router.get("/health")
    async def context_health():
        # Lazy TTL cleanup
        try:
            conversation_store.cleanup_expired()
        except Exception:
            pass  # optional

        return {
            "status": "ok",
            "agents": len(agent_registry.list_agents()),
            "sessions": len(session_manager.list_sessions()),
            "vector_backend": vector_store._manager.backend_name if vector_store._manager else None,
            "vector_available": vector_store.available,
            "embedding_available": vector_store._embedding_service is not None,
            "embedding_capabilities": embedding_hooks.capabilities,
        }

    # ------------------------------------------------------------------
    # Agent Memory endpoints
    # ------------------------------------------------------------------

    _agent_memory = None

    def _get_memory():
        nonlocal _agent_memory
        if _agent_memory is None:
            if graph_registry:
                from ..context.agent_memory import AgentMemory
                _agent_memory = AgentMemory(graph_registry)
            else:
                raise HTTPException(status_code=503, detail="Graph registry not available")
        return _agent_memory

    class RememberRequest(BaseModel):
        content: str = Field(..., min_length=1)
        tags: Optional[List[str]] = None
        confidence: float = 1.0
        source: Optional[str] = None
        metadata: Optional[Dict[str, Any]] = None

    class InteractionRequest(BaseModel):
        role: str = Field(..., min_length=1)
        content: str = Field(..., min_length=1)
        session_id: Optional[str] = None
        metadata: Optional[Dict[str, Any]] = None

    class BuildContextRequest(BaseModel):
        system_prompt: Optional[str] = None
        include_memories: bool = True
        include_recent_interactions: int = 10
        memory_tags: Optional[List[str]] = None
        memory_limit: int = 20
        max_tokens: int = 8000

    @router.post("/context/agents/{agent_id}/memory")
    async def remember(agent_id: str, req: RememberRequest, agent=Depends(require_write)):
        """Store a memory for an agent."""
        memory = _get_memory()
        mem_id = memory.remember(
            agent_id=agent_id,
            content=req.content,
            tags=req.tags,
            confidence=req.confidence,
            source=req.source,
            metadata=req.metadata,
        )
        return {"memory_id": mem_id, "agent_id": agent_id}

    @router.get("/context/agents/{agent_id}/memory")
    async def recall(
        agent_id: str,
        query: Optional[str] = None,
        tags: Optional[str] = None,
        limit: int = 10,
        agent=Depends(require_read),
    ):
        """Recall memories for an agent."""
        memory = _get_memory()
        tag_list = tags.split(",") if tags else None
        memories = memory.recall(agent_id=agent_id, query=query, tags=tag_list, limit=limit)
        return {"agent_id": agent_id, "memories": memories, "count": len(memories)}

    @router.delete("/context/agents/{agent_id}/memory/{memory_id}")
    async def forget(agent_id: str, memory_id: str, agent=Depends(require_write)):
        """Remove a specific memory."""
        memory = _get_memory()
        ok = memory.forget(agent_id, memory_id)
        if not ok:
            raise HTTPException(status_code=404, detail="Memory not found")
        return {"message": "Memory removed", "memory_id": memory_id}

    @router.post("/context/agents/{agent_id}/sessions")
    async def start_session(agent_id: str, agent=Depends(require_write)):
        """Start a new interaction session."""
        memory = _get_memory()
        session_id = memory.start_session(agent_id)
        return {"session_id": session_id, "agent_id": agent_id}

    @router.post("/context/agents/{agent_id}/interactions")
    async def log_interaction(agent_id: str, req: InteractionRequest, agent=Depends(require_write)):
        """Log an interaction turn."""
        memory = _get_memory()
        interaction_id = memory.log_interaction(
            agent_id=agent_id,
            role=req.role,
            content=req.content,
            session_id=req.session_id,
            metadata=req.metadata,
        )
        return {"interaction_id": interaction_id, "session_id": req.session_id}

    @router.post("/context/agents/{agent_id}/build-context")
    async def build_agent_context(agent_id: str, req: BuildContextRequest, agent=Depends(require_write)):
        """Build a ContextHub pre-loaded with agent memories and interactions."""
        memory = _get_memory()
        hub = memory.build_context(
            agent_id=agent_id,
            system_prompt=req.system_prompt,
            include_memories=req.include_memories,
            include_recent_interactions=req.include_recent_interactions,
            memory_tags=req.memory_tags,
            memory_limit=req.memory_limit,
            max_tokens=req.max_tokens,
        )
        return {
            "agent_id": agent_id,
            "messages": hub.to_messages(),
            "item_count": len(hub._items),
        }

    @router.get("/context/agents/{agent_id}/summary")
    async def get_agent_memory_summary(agent_id: str, agent=Depends(require_read)):
        """Get summary of agent's memory state."""
        memory = _get_memory()
        return memory.get_agent_summary(agent_id)

    @router.post("/context/agents/{agent_id}/share/{to_agent_id}")
    async def share_memory(
        agent_id: str, to_agent_id: str, memory_id: str = Query(...), agent=Depends(require_write)
    ):
        """Share a memory from one agent to another."""
        memory = _get_memory()
        ok = memory.share_memory(agent_id, to_agent_id, memory_id)
        if not ok:
            raise HTTPException(status_code=404, detail="Memory not found")
        return {"message": "Memory shared", "from": agent_id, "to": to_agent_id}

    # ==================================================================
    # Session Graph endpoints (unified knowledge + conversation layer)
    # ==================================================================

    @router.get("/sessions/{session_id}/graph/schema")
    async def get_session_graph_schema(
        session_id: str,
        caller: AgentIdentity = Depends(require_read),
    ):
        """Get the session graph schema definition."""
        builder = await _get_or_create_builder(session_id)
        if not builder:
            raise HTTPException(404, "Session not found")
        return builder.get_schema_dict()

    @router.put("/sessions/{session_id}/graph/schema")
    async def update_session_graph_schema(
        session_id: str,
        req: Dict[str, Any],
        caller: AgentIdentity = Depends(require_agent_admin),
    ):
        """Update the session graph schema with new YAML content."""
        builder = await _get_or_create_builder(session_id)
        if not builder:
            raise HTTPException(404, "Session not found")
        yaml_content = req.get("yaml", "")
        if not yaml_content:
            raise HTTPException(400, "Missing 'yaml' field in request body")
        try:
            new_schema = await builder.update_schema(yaml_content)
            return {
                "success": True,
                "node_types": list(new_schema.node_types.keys()),
                "edge_types": list(new_schema.edge_types.keys()),
            }
        except Exception as e:
            raise HTTPException(400, f"Invalid schema: {e}")

    @router.get("/sessions/{session_id}/graph/context")
    async def get_session_graph_context(
        session_id: str,
        system_prompt: str = Query("You are an AI assistant."),
        max_tokens: int = Query(8000),
        recent_turns: int = Query(20),
        format: str = Query("messages", description="messages|prompt|markdown|dict"),
        caller: AgentIdentity = Depends(require_read),
    ):
        """Build graph-aware context from the unified session graph.

        Follows REFERENCES edges from recent conversation turns to pull
        in contextually relevant knowledge nodes.
        """
        builder = await _get_or_create_builder(session_id)
        if not builder:
            raise HTTPException(404, "Session not found")
        hub = builder.build_context(
            agent_id=caller.agent_id,
            system_prompt=system_prompt,
            max_tokens=max_tokens,
            recent_turns=recent_turns,
        )
        if format == "messages":
            return {"messages": hub.to_messages(), "item_count": len(hub.items())}
        elif format == "prompt":
            return {"prompt": hub.to_string(), "item_count": len(hub.items())}
        elif format == "markdown":
            return {"markdown": hub.to_markdown(), "item_count": len(hub.items())}
        else:
            return hub.to_dict()

    @router.get("/sessions/{session_id}/graph/turns")
    async def get_session_graph_turns(
        session_id: str,
        limit: int = Query(20),
        caller: AgentIdentity = Depends(require_read),
    ):
        """Get recent Turn nodes from the session graph."""
        builder = await _get_or_create_builder(session_id)
        if not builder:
            raise HTTPException(404, "Session not found")
        turns = builder.get_recent_turns(limit=limit)
        return {"turns": turns, "count": len(turns)}

    @router.get("/sessions/{session_id}/graph/decisions")
    async def get_session_graph_decisions(
        session_id: str,
        include_done: bool = Query(False),
        caller: AgentIdentity = Depends(require_read),
    ):
        """Get Decision nodes from the session graph."""
        builder = await _get_or_create_builder(session_id)
        if not builder:
            raise HTTPException(404, "Session not found")
        decisions = builder.get_decisions(include_done=include_done)
        return {"decisions": decisions, "count": len(decisions)}

    @router.get("/sessions/{session_id}/graph/topics")
    async def get_session_graph_topics(
        session_id: str,
        caller: AgentIdentity = Depends(require_read),
    ):
        """Get Topic nodes from the session graph, sorted by mention count."""
        builder = await _get_or_create_builder(session_id)
        if not builder:
            raise HTTPException(404, "Session not found")
        topics = builder.get_topics()
        return {"topics": topics, "count": len(topics)}

    @router.get("/sessions/{session_id}/graph/stats")
    async def get_session_graph_stats(
        session_id: str,
        caller: AgentIdentity = Depends(require_read),
    ):
        """Get summary statistics of the session graph."""
        builder = await _get_or_create_builder(session_id)
        if not builder:
            raise HTTPException(404, "Session not found")
        return builder.get_graph_stats()

    # ==================================================================
    # Context manifest endpoint
    # ==================================================================

    @router.get("/contexts/{context_id}/manifest")
    async def get_context_manifest(
        context_id: str,
        caller: AgentIdentity = Depends(require_read),
    ):
        """Get the pre-computed context quality manifest."""
        from contextsynapse.context.quality import get_manifest, ManifestBuilder, EntityIndex

        redis_client = None
        try:
            import redis as _redis_mod
            import os
            url = os.environ.get("CONTEXTSYNAPSE_REDIS_URL") or os.environ.get("AICONTEXTDB_REDIS_URL", "redis://localhost:6379")
            redis_client = _redis_mod.from_url(url)
        except Exception:
            pass

        manifest = get_manifest(context_id, redis_client)
        if manifest:
            return manifest

        # Build on demand if not cached
        try:
            eidx = EntityIndex(redis_client=redis_client)
            builder = ManifestBuilder(entity_index=eidx, redis_client=redis_client)
            manifest = builder.build(context_id, context_id)
            return manifest
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Failed to build manifest: {e}")

    return router

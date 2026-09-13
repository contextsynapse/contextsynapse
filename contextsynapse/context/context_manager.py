"""
Context Manager
===============
Independent, first-class Context entities backed by graph namespaces.

A Context is a curated, typed knowledge store that wraps a graph namespace
with metadata, access control, and lifecycle management.

Architecture:
    Graph (storage engine) → Context (curation layer) → Session (collaboration)
"""

from __future__ import annotations

import json
import logging
import re
import secrets
import sqlite3
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

CONTEXT_NS_PREFIX = ""

VALID_CONTEXT_TYPES = {"user", "system", "knowledge_base", "document", "web", "generated", "software_dev"}
VALID_SENSITIVITIES = {"public", "internal", "confidential", "restricted"}
VALID_STATUSES = {"active", "archived", "deleted"}

from .context_schema import get_categories as _get_schema_categories


@dataclass
class Context:
    """A first-class context entity."""
    context_id: str
    name: str
    description: str
    context_type: str  # user | system | knowledge_base | document | web | generated
    graph_namespace: str
    source: str  # manual | document:<file> | web:<url> | session:<id>
    sensitivity: str  # public | internal | confidential | restricted
    owner_id: Optional[str]
    status: str  # active | archived | deleted
    item_count: int
    estimated_tokens: int
    embedding_model: Optional[str]  # e.g. "text-embedding-3-small", "all-MiniLM-L6-v2"
    embedding_dimension: Optional[int]  # e.g. 384, 768, 1536
    tags: List[str] = field(default_factory=list)
    created_at: str = ""
    updated_at: str = ""
    config: Dict[str, Any] = field(default_factory=dict)

    @property
    def security_config(self) -> Dict[str, Any]:
        """Get the security configuration for this context.

        Stored in config['security']. Returns defaults if not set.
        """
        defaults = {
            "pii_detection": True,
            "auto_tagging": True,
            "encryption": False,
            "redaction_mode": "mask",
            "encrypt_fields": ["email", "phone", "ssn"],
        }
        return {**defaults, **self.config.get("security", {})}

    @security_config.setter
    def security_config(self, value: Dict[str, Any]):
        self.config["security"] = value

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _slugify(name: str) -> str:
    """Create a safe namespace slug from a context name."""
    slug = re.sub(r"[^a-zA-Z0-9_-]", "_", name.lower().strip())
    return slug[:64]


class ContextManager:
    """
    Manages independent Context entities backed by graph namespaces.

    Each Context maps to a graph namespace via GraphRegistry.
    SQLite-backed, same pattern as ContextSessionManager.
    """

    _SCHEMA = """
    CREATE TABLE IF NOT EXISTS contexts (
        context_id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        description TEXT DEFAULT '',
        context_type TEXT NOT NULL DEFAULT 'knowledge_base',
        graph_namespace TEXT NOT NULL UNIQUE,
        source TEXT DEFAULT 'manual',
        sensitivity TEXT DEFAULT 'public',
        owner_id TEXT,
        status TEXT DEFAULT 'active',
        item_count INTEGER DEFAULT 0,
        estimated_tokens INTEGER DEFAULT 0,
        embedding_model TEXT,
        embedding_dimension INTEGER,
        tags TEXT DEFAULT '[]',
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        config TEXT DEFAULT '{}'
    );

    CREATE INDEX IF NOT EXISTS idx_contexts_status ON contexts(status);
    CREATE INDEX IF NOT EXISTS idx_contexts_type ON contexts(context_type);
    CREATE INDEX IF NOT EXISTS idx_contexts_owner ON contexts(owner_id);
    CREATE INDEX IF NOT EXISTS idx_contexts_namespace ON contexts(graph_namespace);
    """

    def __init__(self, db_path: str = "contextcore_data/context.db", graph_registry=None):
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(self._SCHEMA)
        self._migrate_embedding_columns()
        self._graph_registry = graph_registry

    def _migrate_embedding_columns(self):
        """Add embedding and tags columns if missing (for existing DBs)."""
        try:
            self._conn.execute("SELECT embedding_model FROM contexts LIMIT 1")
        except sqlite3.OperationalError:
            self._conn.execute("ALTER TABLE contexts ADD COLUMN embedding_model TEXT")
            self._conn.execute("ALTER TABLE contexts ADD COLUMN embedding_dimension INTEGER")
            self._conn.commit()
        try:
            self._conn.execute("SELECT tags FROM contexts LIMIT 1")
        except sqlite3.OperationalError:
            self._conn.execute("ALTER TABLE contexts ADD COLUMN tags TEXT DEFAULT '[]'")
            self._conn.commit()

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    def create_context(
        self,
        name: str,
        context_type: str = "knowledge_base",
        description: str = "",
        source: str = "manual",
        sensitivity: str = "public",
        owner_id: Optional[str] = None,
        embedding_model: Optional[str] = None,
        embedding_dimension: Optional[int] = None,
        tags: Optional[List[str]] = None,
        config: Optional[Dict[str, Any]] = None,
        _graph_namespace: Optional[str] = None,
    ) -> Context:
        """Create a new context with a backing graph namespace."""
        if context_type not in VALID_CONTEXT_TYPES:
            raise ValueError(f"Invalid context_type: {context_type}")
        if sensitivity not in VALID_SENSITIVITIES:
            raise ValueError(f"Invalid sensitivity: {sensitivity}")

        # Enforce unique context names (case-insensitive)
        existing = self._conn.execute(
            "SELECT context_id FROM contexts WHERE LOWER(name) = LOWER(?) AND status != 'deleted'",
            (name,),
        ).fetchone()
        if existing:
            raise ValueError(f"A context named '{name}' already exists")

        context_id = secrets.token_hex(12)
        now = datetime.now(timezone.utc).isoformat()

        # Use provided namespace or generate from context name
        if _graph_namespace:
            ns = _graph_namespace
        else:
            ns = _slugify(name)
            # Only collide if another context already owns this namespace
            # (don't check graph registry — unrelated graphs like 'default' would cause false collisions)
            existing_ns = self._conn.execute(
                "SELECT context_id FROM contexts WHERE graph_namespace = ?", (ns,)
            ).fetchone()
            if existing_ns:
                ns = f"{ns}_{context_id[:6]}"

        # Purge any stale soft-deleted rows that hold this namespace
        # (legacy data from before hard-delete was implemented)
        self._conn.execute(
            "DELETE FROM contexts WHERE graph_namespace = ? AND status = 'deleted'",
            (ns,),
        )
        self._conn.commit()

        # Create backing graph namespace with root Context node
        if self._graph_registry:
            if not _graph_namespace:
                self._graph_registry.create_graph(ns)
            try:
                from ..core.graph_structures import GraphNode
                db = self._graph_registry.get_graph(ns)
                if not db:
                    db = self._graph_registry.create_graph(ns)
                db.add_node(GraphNode(
                    id=context_id,
                    label="Context",
                    properties={
                        "name": name,
                        "context_type": context_type,
                        "description": description[:200] if description else "",
                        "sensitivity": sensitivity,
                        "created_at": now,
                    },
                ), write_through=True)

                # Create ALL category nodes under Context root (from schema)
                from ..core.graph_structures import GraphEdge
                schema_cats = _get_schema_categories()
                for cat_key, (cat_label, cat_edge, _children) in schema_cats.items():
                    cat_node_id = f"{context_id}_{cat_key}"
                    db.add_node(GraphNode(
                        id=cat_node_id,
                        label=cat_label,
                        properties={
                            "name": f"{name} — {cat_label}",
                            "category": cat_key,
                            "created_at": now,
                        },
                    ), write_through=True)
                    db.add_edge(GraphEdge(
                        id=f"{context_id}_to_{cat_key}",
                        source=context_id,
                        target=cat_node_id,
                        label=cat_edge,
                        properties={},
                    ))

                # ── Search index pointer nodes ──
                # VectorIndex — tells search tools where embeddings live
                vec_node_id = f"{context_id}_vector_index"
                vec_collection = f"{ns}_passages"
                db.add_node(GraphNode(
                    id=vec_node_id, label="VectorIndex",
                    properties={
                        "name": f"Vector Index: {name}",
                        "context_id": context_id,
                        "collection": vec_collection,
                        "embedding_model": embedding_model or "",
                        "embedding_dimension": embedding_dimension or 0,
                        "status": "pending",  # becomes "active" after EMBED stage
                        "count": 0,
                        "created_at": now,
                    },
                ), write_through=True)
                db.add_edge(GraphEdge(
                    id=f"{context_id}_to_vector_index",
                    source=context_id, target=vec_node_id,
                    label="HAS_INDEX", properties={"index_type": "vector"},
                ))

                # BM25Index — tells search tools where full-text index lives
                bm25_node_id = f"{context_id}_bm25_index"
                safe_ns = ns.replace(":", "_")
                bm25_path = f"contextcore_data/bm25_index/{safe_ns}"
                db.add_node(GraphNode(
                    id=bm25_node_id, label="BM25Index",
                    properties={
                        "name": f"BM25 Index: {name}",
                        "context_id": context_id,
                        "index_path": bm25_path,
                        "graph_namespace": ns,
                        "status": "pending",  # becomes "active" after INDEX_BM25 stage
                        "count": 0,
                        "created_at": now,
                    },
                ), write_through=True)
                db.add_edge(GraphEdge(
                    id=f"{context_id}_to_bm25_index",
                    source=context_id, target=bm25_node_id,
                    label="HAS_INDEX", properties={"index_type": "bm25"},
                ))

                # Save to disk (synchronous)
                self._graph_registry.save_graph(ns, create_checkpoint=False)
            except Exception as e:
                logging.getLogger(__name__).debug("Failed to create root context node: %s", e)

        merged_config = config or {}

        # Software dev context: seed ProjectSpec node linked to root Context
        if context_type == "software_dev" and self._graph_registry:
            try:
                db = self._graph_registry.get_graph(ns)
                if db:
                    import uuid as _uuid
                    from ..core.graph_structures import GraphNode, GraphEdge
                    spec_id = str(_uuid.uuid4())
                    stack = merged_config.get("stack", {})
                    rules = merged_config.get("rules", [])
                    spec_text = merged_config.get("spec", description)
                    db.add_node(GraphNode(
                        id=spec_id, label="ProjectSpec",
                        properties={
                            "name": name,
                            "spec": spec_text,
                            "stack": json.dumps(stack) if isinstance(stack, dict) else str(stack),
                            "rules": json.dumps(rules) if isinstance(rules, list) else str(rules),
                            "repo": merged_config.get("repo", ""),
                            "status": "active",
                            "created_at": now,
                        },
                    ), write_through=True)
                    # Link: Context ──HAS_SPEC──► ProjectSpec
                    db.add_edge(GraphEdge(
                        id=str(_uuid.uuid4()), source=context_id, target=spec_id,
                        label="HAS_SPEC", properties={},
                    ))
                    # Store metadata in namespace store
                    try:
                        from ..storage.namespace_store import NamespaceStore
                        ns_store = NamespaceStore(ns)
                        ns_store.set_metadata("spec", spec_text)
                        ns_store.set_metadata("stack", stack)
                        ns_store.set_metadata("rules", rules)
                        ns_store.close()
                    except Exception:
                        pass
            except Exception as e:
                logging.getLogger(__name__).warning("Failed to seed code context: %s", e)

        ctx = Context(
            context_id=context_id,
            name=name,
            description=description,
            context_type=context_type,
            graph_namespace=ns,
            source=source,
            sensitivity=sensitivity,
            owner_id=owner_id,
            status="active",
            item_count=0,
            estimated_tokens=0,
            embedding_model=embedding_model,
            embedding_dimension=embedding_dimension,
            tags=tags or [],
            created_at=now,
            updated_at=now,
            config=merged_config,
        )

        self._conn.execute(
            "INSERT INTO contexts "
            "(context_id, name, description, context_type, graph_namespace, "
            "source, sensitivity, owner_id, status, item_count, estimated_tokens, "
            "embedding_model, embedding_dimension, tags, "
            "created_at, updated_at, config) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                ctx.context_id, ctx.name, ctx.description, ctx.context_type,
                ctx.graph_namespace, ctx.source, ctx.sensitivity, ctx.owner_id,
                ctx.status, ctx.item_count, ctx.estimated_tokens,
                ctx.embedding_model, ctx.embedding_dimension,
                json.dumps(ctx.tags),
                ctx.created_at, ctx.updated_at, json.dumps(ctx.config),
            ),
        )
        self._conn.commit()

        # Graph-first: register context + connect to its type node
        if self._graph_registry:
            try:
                from ..core.graph_structures import GraphNode, GraphEdge
                import uuid as _uuid
                default_graph = self._graph_registry.get_graph("default") or self._graph_registry.create_graph("default")

                # Ensure ContextType node exists (one per type, reused)
                type_node_id = f"context_type_{context_type}"
                existing_type = None
                for n in default_graph.get_all_nodes():
                    if getattr(n, 'id', '') == type_node_id:
                        existing_type = n
                        break
                if not existing_type:
                    default_graph.add_node(GraphNode(
                        id=type_node_id,
                        label="ContextType",
                        properties={"name": context_type, "label": context_type.replace("_", " ").title()},
                    ), write_through=True)

                # Create Context node
                default_graph.add_node(GraphNode(
                    id=ctx.context_id,
                    label="Context",
                    properties={
                        "name": ctx.name,
                        "context_type": ctx.context_type,
                        "graph_namespace": ctx.graph_namespace,
                        "description": ctx.description[:200] if ctx.description else "",
                        "sensitivity": ctx.sensitivity,
                        "status": ctx.status,
                        "created_at": ctx.created_at,
                    },
                ), write_through=True)

                # Connect: Context ──IS_TYPE──► ContextType
                default_graph.add_edge(GraphEdge(
                    id=str(_uuid.uuid4()),
                    source=ctx.context_id,
                    target=type_node_id,
                    label="IS_TYPE",
                    properties={},
                ))
            except Exception as e:
                logging.getLogger(__name__).debug("Failed to register context in default graph: %s", e)

        return ctx

    def ensure_boundaries(self, context_id: str) -> Optional[Dict[str, Any]]:
        """Ensure a context has boundary nodes; seed them if missing.

        Returns the boundary IDs dict, or None if the context doesn't exist.
        """
        ctx = self.get_context(context_id)
        if not ctx:
            return None

        existing = ctx.config.get("_boundary_ids")
        if existing:
            return existing

        if not self._graph_registry:
            return None

        db = self._graph_registry.get_graph(ctx.graph_namespace)
        if not db:
            return None

        try:
            from .boundaries import seed_boundary_nodes
            boundary_ids = seed_boundary_nodes(db, ctx.context_id, ctx.name)
            self._graph_registry.save_graph(ctx.graph_namespace, create_checkpoint=False)
            # Persist to context config
            new_config = dict(ctx.config)
            new_config["_boundary_ids"] = boundary_ids
            self.update_context(context_id, config=new_config)
            return boundary_ids
        except Exception as e:
            logging.getLogger(__name__).warning("Failed to ensure boundaries for %s: %s", context_id, e)
            return None

    def get_context(self, context_id: str) -> Optional[Context]:
        row = self._conn.execute(
            "SELECT * FROM contexts WHERE context_id = ? AND status != 'deleted'",
            (context_id,),
        ).fetchone()
        return self._row_to_context(row) if row else None

    def get_by_graph_namespace(self, namespace: str) -> Optional[Context]:
        row = self._conn.execute(
            "SELECT * FROM contexts WHERE graph_namespace = ? AND status != 'deleted'",
            (namespace,),
        ).fetchone()
        return self._row_to_context(row) if row else None

    def list_contexts(
        self,
        context_type: Optional[str] = None,
        status: str = "active",
        owner_id: Optional[str] = None,
        search: Optional[str] = None,
        tag: Optional[str] = None,
    ) -> List[Context]:
        query = "SELECT * FROM contexts WHERE status = ?"
        params: list = [status]

        if context_type:
            query += " AND context_type = ?"
            params.append(context_type)
        if owner_id:
            query += " AND owner_id = ?"
            params.append(owner_id)
        if search:
            query += " AND (name LIKE ? OR description LIKE ? OR tags LIKE ?)"
            like = f"%{search}%"
            params.extend([like, like, like])
        if tag:
            query += " AND tags LIKE ?"
            params.append(f'%"{tag}"%')

        query += " ORDER BY updated_at DESC"
        rows = self._conn.execute(query, params).fetchall()
        return [self._row_to_context(r) for r in rows]

    def update_context(self, context_id: str, **fields) -> Optional[Context]:
        """Update mutable fields on a context."""
        allowed = {"name", "description", "sensitivity", "context_type", "source", "config", "status", "embedding_model", "embedding_dimension", "tags"}
        updates = {k: v for k, v in fields.items() if k in allowed and v is not None}
        if not updates:
            return self.get_context(context_id)

        now = datetime.now(timezone.utc).isoformat()
        updates["updated_at"] = now

        # Serialize config/tags if present
        if "config" in updates and isinstance(updates["config"], dict):
            updates["config"] = json.dumps(updates["config"])
        if "tags" in updates and isinstance(updates["tags"], list):
            updates["tags"] = json.dumps(updates["tags"])

        set_clause = ", ".join(f"{k} = ?" for k in updates)
        values = list(updates.values()) + [context_id]

        self._conn.execute(
            f"UPDATE contexts SET {set_clause} WHERE context_id = ?",
            values,
        )
        self._conn.commit()
        return self.get_context(context_id)

    # ------------------------------------------------------------------
    # Per-context extraction schema overrides
    # ------------------------------------------------------------------

    def get_extraction_schema(self, context_id: str) -> Optional[str]:
        """Get the custom extraction schema YAML for a context, or None for default."""
        ctx = self.get_context(context_id)
        if not ctx:
            return None
        return ctx.config.get("extraction_schema_yaml")

    def set_extraction_schema(self, context_id: str, schema_yaml: str) -> bool:
        """Set a custom extraction schema YAML on a context."""
        ctx = self.get_context(context_id)
        if not ctx:
            return False
        new_config = dict(ctx.config)
        new_config["extraction_schema_yaml"] = schema_yaml
        self.update_context(context_id, config=new_config)
        return True

    def clear_extraction_schema(self, context_id: str) -> bool:
        """Remove the custom extraction schema, reverting to the type default."""
        ctx = self.get_context(context_id)
        if not ctx:
            return False
        new_config = dict(ctx.config)
        new_config.pop("extraction_schema_yaml", None)
        self.update_context(context_id, config=new_config)
        return True

    def delete_context(self, context_id: str) -> bool:
        """Delete a context — cascades to its backing graph."""
        ctx = self.get_context(context_id)
        if not ctx:
            return False

        now = datetime.now(timezone.utc).isoformat()

        # 1. Delete backing graph (memory + files)
        if self._graph_registry and ctx.graph_namespace:
            try:
                self._graph_registry.delete_graph(ctx.graph_namespace, delete_files=True)
            except Exception as e:
                logging.getLogger(__name__).debug("Failed to delete backing graph: %s", e)

        # 2. Hard-delete from SQLite (graph files already gone, soft-delete
        #    leaves the UNIQUE graph_namespace occupied and blocks re-creation)
        cur = self._conn.execute(
            "DELETE FROM contexts WHERE context_id = ?",
            (context_id,),
        )
        self._conn.commit()
        return cur.rowcount > 0

    # ------------------------------------------------------------------
    # Content operations
    # ------------------------------------------------------------------

    def add_text(
        self,
        context_id: str,
        content: str,
        role: str = "user",
        label: Optional[str] = None,
        sensitivity: str = "public",
        tags: Optional[List[str]] = None,
    ) -> Optional[Dict[str, Any]]:
        """Add a text item to the context's backing graph as a node."""
        ctx = self.get_context(context_id)
        if not ctx:
            return None

        if not self._graph_registry:
            return None

        db = self._graph_registry.get_graph(ctx.graph_namespace)
        if not db:
            self._graph_registry.create_graph(ctx.graph_namespace)
            db = self._graph_registry.get_graph(ctx.graph_namespace)

        if not db:
            return None

        from ..core.graph_structures import GraphNode, GraphEdge
        import uuid
        node_id = str(uuid.uuid4())
        node = GraphNode(
            id=node_id,
            label="ContextItem",
            properties={
                "content": content,
                "role": role,
                "label": label or "",
                "sensitivity": sensitivity,
                "tags": json.dumps(tags or []),
                "created_at": datetime.now(timezone.utc).isoformat(),
            },
        )
        db.add_node(node, write_through=True)

        # Link to type node (KnowledgeBase etc.) or Context root
        type_node_id = f"{context_id}_knowledge"
        parent_id = type_node_id if db.get_node(type_node_id) else context_id
        db.add_edge(GraphEdge(
            id=str(uuid.uuid4()),
            source=parent_id,
            target=node_id,
            label="HAS_CONTENT",
            properties={"role": role},
        ))

        # Persist graph
        try:
            self._graph_registry.save_graph(ctx.graph_namespace, create_checkpoint=False)
        except Exception as e:
            logger.warning("Failed to persist context graph %s: %s", ctx.graph_namespace, e)

        # Update stats
        self._refresh_stats(context_id, db)

        return {
            "node_id": str(node.id),
            "content": content[:100],
            "role": role,
            "label": label,
        }

    # Node labels relevant to each context type.  When a context has a
    # declared type we only return nodes whose label is in this set so
    # the UI stays focused.  ``None`` means "show everything".
    # Connector-related node types shared across all context types
    _CONNECTOR_LABELS = {
        "Connector", "SyncRun", "DatabaseSchema", "Table", "Column", "Index", "ForeignKey",
        "Repository", "Issue", "PullRequest", "File", "Contributor",
        "JiraProject", "Sprint", "Component", "User",
        "APIEndpoint", "ResponseField",
        "SFObject", "SFField", "SFRelationship",
        "SAPModule", "SAPTable", "SAPField",
        "SNTable", "SNField",
    }

    _TYPE_RELEVANT_LABELS: Dict[str, Optional[set]] = {
        "knowledge_base": {"Document", "Passage", "TextChunk", "Chunk", "Fact", "Concept", "Topic", "Knowledge"} | _CONNECTOR_LABELS,
        "software_dev": {"CodeFile", "CodeModule", "Document", "Passage", "TextChunk", "Chunk", "Module", "Class",
                         "Function", "Component", "Service", "Schema", "API", "Endpoint",
                         "GitHubIssue", "GitHubPR", "Contributor", "RepoMeta",
                         "ArchDecision", "KnownIssue", "TestCase", "APIContract",
                         "Requirement", "UserStory", "Goal", "ChangeRecord"} | _CONNECTOR_LABELS,
        "rules": {"Document", "Passage", "TextChunk", "Chunk", "Rule", "Constraint", "Standard", "Policy", "Fact"} | _CONNECTOR_LABELS,
        "database": {"Table", "Column", "Schema", "Index", "View", "Document", "Passage", "TextChunk", "Chunk",
                      "Entity", "Relationship"} | _CONNECTOR_LABELS,
        "decision": {"Decision", "Document", "Passage", "TextChunk", "Chunk", "Fact", "ADR", "TradeOff"} | _CONNECTOR_LABELS,
        "web": {"Document", "Passage", "TextChunk", "Chunk", "WebPage", "Link", "Fact"} | _CONNECTOR_LABELS,
    }

    def get_items(self, context_id: str, category: Optional[str] = None,
                  show_all: bool = False, max_tokens: Optional[int] = None,
                  query: Optional[str] = None) -> List[Dict[str, Any]]:
        """Get content items from a context's backing graph.

        Args:
            context_id: The context to query.
            category: Optional category filter.
            show_all: If True, bypass context-type filtering.
            max_tokens: If set, return items up to this token budget (most relevant first).
            query: If set with max_tokens, rank items by relevance to this query.

        Returns:
            List of item dicts, excluding structural boundary nodes.
        """
        from .boundaries import BOUNDARY_NODE_LABELS

        ctx = self.get_context(context_id)
        if not ctx or not self._graph_registry:
            return []

        ns = ctx.graph_namespace
        db = self._graph_registry.get_graph(ns)
        if not db:
            logger.warning("get_items: graph '%s' not found for context '%s'", ns, context_id)
            return []

        # Determine which labels to show based on context type
        relevant_labels = None
        if not show_all:
            # Try schema-driven labels first
            try:
                from contextsynapse.project.schema_manager import get_schema_manager
                schema_labels = get_schema_manager().get_consumer_hint(
                    ns, "context_filter_labels", None
                )
                if schema_labels is not None:
                    relevant_labels = set(schema_labels) | self._CONNECTOR_LABELS
            except Exception:
                pass
            # Fall back to hardcoded per-context-type labels
            if relevant_labels is None:
                relevant_labels = self._TYPE_RELEVANT_LABELS.get(ctx.context_type)

        nodes = db.get_all_nodes()
        items = []
        for node in nodes:
            if isinstance(node, dict):
                props = node
                node_label = props.get("label", "")
            else:
                node_label = getattr(node, "label", "")
                props = getattr(node, "properties", {}) or {}
                props["node_id"] = str(getattr(node, "id", ""))
                props["label_type"] = node_label

            # Skip structural boundary nodes
            if node_label in BOUNDARY_NODE_LABELS:
                continue

            # Filter by context-type-relevant labels
            if relevant_labels and node_label not in relevant_labels:
                continue

            # Apply category filter
            if category and props.get("category") != category:
                continue

            items.append(props)

        # Token-budgeted retrieval with relevance ranking
        if max_tokens and items:
            if query:
                # Score by keyword relevance to query
                q_terms = query.lower().split()
                scored = []
                for item in items:
                    text = " ".join(str(v) for v in item.values()).lower()
                    score = sum(1 for t in q_terms if t in text) / max(len(q_terms), 1)
                    scored.append((item, score))
                scored.sort(key=lambda x: x[1], reverse=True)
                items = [s[0] for s in scored]

            # Early stopping at token budget
            result = []
            tokens_used = 0
            for item in items:
                content = item.get("content") or item.get("description") or item.get("statement") or ""
                item_tokens = max(1, len(str(content)) // 4)
                if tokens_used + item_tokens > max_tokens:
                    break
                result.append(item)
                tokens_used += item_tokens
            return result

        return items

    def refresh_stats(self, context_id: str) -> Optional[Context]:
        """Recount items and estimate tokens from the backing graph."""
        ctx = self.get_context(context_id)
        if not ctx or not self._graph_registry:
            return ctx

        ns = ctx.graph_namespace
        db = self._graph_registry.get_graph(ns)
        if not db:
            logger.warning("refresh_stats: graph '%s' not found for context '%s'", ns, context_id)
        if db:
            self._refresh_stats(context_id, db)

        return self.get_context(context_id)

    def _refresh_stats(self, context_id: str, db):
        """Internal: update item_count and estimated_tokens from graph."""
        from .boundaries import BOUNDARY_NODE_LABELS

        ctx = self.get_context(context_id)
        relevant_labels = self._TYPE_RELEVANT_LABELS.get(ctx.context_type) if ctx else None

        nodes = db.get_all_nodes()
        item_count = 0
        total_chars = 0

        for node in nodes:
            if isinstance(node, dict):
                node_label = node.get("label", "")
                content = node.get("content", "")
            else:
                node_label = getattr(node, "label", "")
                props = getattr(node, "properties", {}) or {}
                content = props.get("content", "")

            # Exclude structural boundary nodes from stats
            if node_label in BOUNDARY_NODE_LABELS:
                continue

            # Only count type-relevant nodes
            if relevant_labels and node_label not in relevant_labels:
                continue

            item_count += 1
            total_chars += len(str(content))

        estimated_tokens = int(total_chars / 4.0)  # ~4 chars per token
        now = datetime.now(timezone.utc).isoformat()

        self._conn.execute(
            "UPDATE contexts SET item_count = ?, estimated_tokens = ?, updated_at = ? "
            "WHERE context_id = ?",
            (item_count, estimated_tokens, now, context_id),
        )
        self._conn.commit()

    # ------------------------------------------------------------------
    # Migration
    # ------------------------------------------------------------------

    def migrate_session_contexts(self, session_manager) -> int:
        """One-time migration: wrap each session's graph namespace as a Context.

        Returns the number of contexts created.
        """
        created = 0
        for session in session_manager.list_sessions():
            existing = self.get_by_graph_namespace(session.graph_namespace)
            if not existing:
                try:
                    self.create_context(
                        name=session.name,
                        context_type="generated",
                        description=f"Auto-migrated from session '{session.name}'",
                        source=f"session:{session.session_id}",
                        owner_id=session.owner_agent_id,
                        _graph_namespace=session.graph_namespace,
                    )
                    created += 1
                except Exception as e:
                    logger.warning("Failed to migrate session '%s': %s", session.name, e)
        return created

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _row_to_context(self, row: sqlite3.Row) -> Context:
        return Context(
            context_id=row["context_id"],
            name=row["name"],
            description=row["description"],
            context_type=row["context_type"],
            graph_namespace=row["graph_namespace"],
            source=row["source"],
            sensitivity=row["sensitivity"],
            owner_id=row["owner_id"],
            status=row["status"],
            item_count=row["item_count"],
            estimated_tokens=row["estimated_tokens"],
            embedding_model=row["embedding_model"],
            embedding_dimension=row["embedding_dimension"],
            tags=json.loads(row["tags"]) if row["tags"] else [],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            config=json.loads(row["config"]) if row["config"] else {},
        )

    def close(self):
        if self._conn:
            self._conn.close()

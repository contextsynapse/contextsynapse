"""
Session Graph Builder
=====================
Orchestrates the unified session graph lifecycle: schema loading,
InteractionGraphifier initialization, and graph-aware context building.

Each session gets one SessionGraphBuilder. The builder:
  - Loads the session graph schema (editable YAML)
  - Initializes a SchemaValidator for constraint enforcement
  - Creates an InteractionGraphifier for the conversation layer
  - Builds ContextHub instances with graph-aware slot allocation

Usage:
    builder = SessionGraphBuilder(session, graph_registry)
    await builder.initialize()

    # Graphify an interaction (fire-and-forget)
    result = await builder.graphify_interaction("user", "Let's use Redis", "agent-1")

    # Build context for an LLM call
    hub = builder.build_context(agent_id="agent-1", system_prompt="You are helpful.")
    messages = hub.to_messages()
"""

from __future__ import annotations

import logging
import os
import tempfile
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class SessionGraphBuilder:
    """
    Orchestrates the unified session graph.

    One instance per session. Thread-safe writes are handled by the
    underlying InteractionGraphifier's asyncio.Lock.
    """

    def __init__(
        self,
        session,  # ContextSession
        graph_registry,  # GraphRegistry
        schema_path: Optional[str] = None,
    ):
        self.session = session
        self._registry = graph_registry
        self._schema_path = schema_path or self._default_schema_path()
        self._schema = None  # GraphSchema
        self._validator = None  # SchemaValidator
        self._graphifier = None  # InteractionGraphifier
        self._db = None  # AIContextDB
        self._initialized = False

    @staticmethod
    def _default_schema_path() -> str:
        """Return path to the shipped default session graph schema."""
        return os.path.join(
            os.path.dirname(__file__), "..", "config", "schemas", "session_graph.yaml",
        )

    # ------------------------------------------------------------------
    # Initialization
    # ------------------------------------------------------------------

    async def initialize(self) -> None:
        """
        Initialize the session graph builder.

        1. Load and parse schema YAML
        2. Get or create the graph namespace
        3. Create SchemaValidator
        4. Create InteractionGraphifier
        """
        if self._initialized:
            return

        try:
            from ..schema.schema_parser import SchemaParser
        except ImportError:
            SchemaParser = None
        try:
            from ..ingestion.schema_validator import SchemaValidator
        except ImportError:
            SchemaValidator = None
        from .interaction_graphifier import InteractionGraphifier

        # 1. Load schema
        if SchemaParser and os.path.exists(self._schema_path):
            parser = SchemaParser(self._schema_path)
            self._schema = parser.parse()
            logger.info(
                "Loaded session graph schema: %d node types, %d edge types",
                len(self._schema.node_types),
                len(self._schema.edge_types),
            )
        else:
            logger.warning("Session graph schema not found at %s", self._schema_path)

        # 2. Get or create graph namespace
        ns = self.session.graph_namespace
        self._db = self._registry.get_graph(ns, load_if_missing=True)
        if not self._db:
            self._registry.create_graph(ns)
            self._db = self._registry.get_graph(ns, load_if_missing=True)

        # 3. Create validator
        if self._schema and SchemaValidator:
            self._validator = SchemaValidator(self._schema)

        # 4. Create graphifier
        self._graphifier = InteractionGraphifier(
            db=self._db,
            schema=self._schema,
            validator=self._validator,
        )

        self._initialized = True
        logger.info("SessionGraphBuilder initialized for session %s (ns=%s)", self.session.session_id, ns)

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def graphifier(self):
        """The InteractionGraphifier for this session."""
        if not self._graphifier:
            raise RuntimeError("SessionGraphBuilder not initialized. Call initialize() first.")
        return self._graphifier

    @property
    def db(self):
        """The AIContextDB instance for this session's graph namespace."""
        if not self._db:
            raise RuntimeError("SessionGraphBuilder not initialized. Call initialize() first.")
        return self._db

    @property
    def schema(self):
        """The parsed GraphSchema, or None if no schema loaded."""
        return self._schema

    # ------------------------------------------------------------------
    # Interaction graphification
    # ------------------------------------------------------------------

    async def graphify_interaction(
        self,
        role: str,
        content: str,
        agent_id: str,
        conversation_id: Optional[str] = None,
    ):
        """
        Graphify an interaction turn. Fire-and-forget safe.

        Delegates to the InteractionGraphifier, then publishes a PubSub
        event so other subscribers (e.g. WebSocket clients) are notified.
        """
        if not self._initialized:
            await self.initialize()

        result = await self.graphifier.graphify(
            role=role,
            content=content,
            agent_id=agent_id,
            conversation_id=conversation_id,
        )

        # Publish PubSub event
        try:
            from .pubsub import pubsub_hub, ContextEvent

            await pubsub_hub.publish(ContextEvent(
                event_type="turn_graphified",
                session_id=self.session.session_id,
                agent_id=agent_id,
                payload={
                    "turn_node_id": result.turn_node_id,
                    "signal_count": len(result.signal_node_ids),
                    "reference_count": len(result.reference_edge_ids),
                    "skipped": result.skipped,
                },
            ))
        except Exception as e:
            logger.debug("PubSub publish failed (non-critical): %s", e)

        return result

    # ------------------------------------------------------------------
    # Context building
    # ------------------------------------------------------------------

    def build_context(
        self,
        agent_id: Optional[str] = None,
        system_prompt: Optional[str] = None,
        max_tokens: int = 8000,
        recent_turns: int = 20,
    ):
        """
        Build a graph-aware ContextHub from the unified session graph.

        Slot allocation:
          - decisions + actions: 15% of budget
          - recent turns: 40% of budget
          - referenced knowledge (via REFERENCES edges): 35% of budget
          - topic summary: 10% of budget

        The key advantage: instead of blindly grabbing recent chunks,
        we follow REFERENCES edges from recent conversation turns to
        pull in contextually relevant knowledge nodes.
        """
        from .hub import ContextHub, ContextRole

        hub = ContextHub(system_prompt=system_prompt, max_tokens=max_tokens)

        # Define slots with proportional budgets
        decisions_budget = int(max_tokens * 0.15)
        turns_budget = int(max_tokens * 0.40)
        knowledge_budget = int(max_tokens * 0.35)
        topics_budget = int(max_tokens * 0.10)

        hub.define_slot("decisions", max_tokens=decisions_budget, pinned=False, priority=8)
        hub.define_slot("turns", max_tokens=turns_budget, pinned=False, priority=7)
        hub.define_slot("knowledge", max_tokens=knowledge_budget, pinned=False, priority=6)
        hub.define_slot("topics", max_tokens=topics_budget, pinned=False, priority=5)

        db = self.db
        all_nodes = db.get_all_nodes()
        all_edges = db.get_all_edges()

        # -- Collect Turn nodes, sorted by turn_index descending --
        turns = [
            n for n in all_nodes
            if n.label == "Turn"
        ]
        turns.sort(key=lambda n: n.properties.get("turn_index", 0), reverse=True)
        recent = turns[:recent_turns]

        # -- Add recent turns to hub --
        for turn_node in reversed(recent):  # chronological order
            props = turn_node.properties
            role_str = props.get("role", "user")
            role_map = {
                "user": ContextRole.USER,
                "assistant": ContextRole.ASSISTANT,
                "system": ContextRole.SYSTEM,
                "tool": ContextRole.TOOL,
            }
            ctx_role = role_map.get(role_str, ContextRole.INTERACTION)
            hub.add_text(
                props.get("content", ""),
                role=ctx_role,
                label=f"Turn ({role_str})",
                source=f"turn:{turn_node.id}",
            )
            # Assign to turns slot
            hub.assign_to_slot(len(hub.items()) - 1, "turns")

        # -- Collect referenced knowledge nodes --
        recent_turn_ids = {n.id for n in recent}
        referenced_node_ids = set()
        for edge in all_edges:
            if edge.label == "REFERENCES" and edge.source in recent_turn_ids:
                referenced_node_ids.add(edge.target)

        # Add referenced knowledge
        for node in all_nodes:
            if node.id in referenced_node_ids:
                props = node.properties
                if node.label == "Entity":
                    text = f"[Entity] {props.get('entity_name', '')} ({props.get('entity_type', '')})"
                    if props.get("description"):
                        text += f": {props['description']}"
                elif node.label == "Document":
                    text = f"[Document] {props.get('title', props.get('source', ''))}"
                elif node.label == "TextChunk":
                    text = f"[Chunk] {props.get('content', '')[:500]}"
                else:
                    text = f"[{node.label}] {str(props)[:300]}"

                hub.add_text(
                    text,
                    role=ContextRole.RETRIEVED,
                    label=f"Referenced {node.label}",
                    source=f"knowledge:{node.id}",
                )
                hub.assign_to_slot(len(hub.items()) - 1, "knowledge")

        # -- Decisions and Actions --
        decisions_actions = [
            n for n in all_nodes
            if n.label in ("Decision", "Action")
            and n.properties.get("status", "") != "done"
        ]
        decisions_actions.sort(
            key=lambda n: n.properties.get("decided_at", n.properties.get("created_at", "")),
            reverse=True,
        )

        for node in decisions_actions[:10]:  # cap at 10
            props = node.properties
            if node.label == "Decision":
                text = f"[Decision] {props.get('summary', '')}"
            else:
                text = f"[Action] {props.get('description', '')} (status: {props.get('status', 'pending')})"

            hub.add_text(
                text,
                role=ContextRole.DECISION,
                label=node.label,
                source=f"{node.label.lower()}:{node.id}",
            )
            hub.assign_to_slot(len(hub.items()) - 1, "decisions")

        # -- Topics summary --
        topics = [n for n in all_nodes if n.label == "Topic"]
        topics.sort(key=lambda n: n.properties.get("mention_count", 0), reverse=True)
        if topics:
            topic_lines = [
                f"- {t.properties.get('name', '?')} (mentions: {t.properties.get('mention_count', 1)})"
                for t in topics[:15]
            ]
            hub.add_text(
                "## Active Topics\n" + "\n".join(topic_lines),
                role=ContextRole.BACKGROUND,
                label="Topics",
                source="topics:summary",
            )
            hub.assign_to_slot(len(hub.items()) - 1, "topics")

        return hub

    def build_context_from_hierarchy(
        self,
        context_id: str,
        question: Optional[str] = None,
        system_prompt: Optional[str] = None,
        max_tokens: int = 8000,
        max_passages: int = 10,
        max_turns: int = 10,
    ):
        """Build context by walking the graph hierarchy — O(hierarchy) not O(N).

        Walks:
          Context → KnowledgeBase → Documents → Passages/Chunks (knowledge)
          Context → UserStore → Turns (recent interactions)
          Context → CodeBase → Tasks (dev context)

        If `question` is provided, passages are ranked by relevance.
        """
        from .hub import ContextHub, ContextRole

        hub = ContextHub(system_prompt=system_prompt, max_tokens=max_tokens)
        hub.define_slot("knowledge", max_tokens=int(max_tokens * 0.50), pinned=False, priority=8)
        hub.define_slot("turns", max_tokens=int(max_tokens * 0.30), pinned=False, priority=7)
        hub.define_slot("decisions", max_tokens=int(max_tokens * 0.10), pinned=False, priority=6)
        hub.define_slot("topics", max_tokens=int(max_tokens * 0.10), pinned=False, priority=5)

        db = self.db

        # --- Knowledge: walk Context → KnowledgeBase → Document → Passages ---
        kb_id = f"{context_id}_knowledge"
        passages = []
        for doc_id, _edge in (db.csr_storage.get_neighbors(kb_id, edge_type="HAS_DOCUMENT")
                              if db.csr_storage and db.csr_storage.get_node(kb_id) else []):
            for p_id, _pe in db.csr_storage.get_neighbors(doc_id):
                if _pe.edge_type in ("HAS_PASSAGE", "CONTAINS"):
                    p_node = db.get_node(p_id)
                    if p_node:
                        passages.append(p_node)

        # Rank by keyword relevance if question provided
        if question and passages:
            q_terms = question.lower().split()
            for p in passages:
                props = p.properties or {}
                text = props.get("content", "") or props.get("statement", "")
                if not text and props.get("vector_ref"):
                    try:
                        from contextsynapse.search.text_resolver import resolve_text
                        text = resolve_text(p.id, props, "")
                    except Exception:
                        pass
                p._score = sum(1 for t in q_terms if t in (text or "").lower())
            passages.sort(key=lambda p: getattr(p, "_score", 0), reverse=True)

        for p in passages[:max_passages]:
            props = p.properties or {}
            text = props.get("content", "") or props.get("statement", "")
            if not text and props.get("vector_ref"):
                try:
                    from contextsynapse.search.text_resolver import resolve_text
                    text = resolve_text(p.id, props, "")
                except Exception:
                    pass
            if text:
                hub.add_text(text, role=ContextRole.RETRIEVED, label=p.label, source=f"passage:{p.id}")
                hub.assign_to_slot(len(hub.items()) - 1, "knowledge")

        # --- Recent turns: walk Context → UserStore → Turns ---
        user_id = f"{context_id}_user"
        turns = []
        if db.csr_storage and db.csr_storage.get_node(user_id):
            for t_id, _te in db.csr_storage.get_neighbors(user_id):
                t_node = db.get_node(t_id)
                if t_node and t_node.label == "Turn":
                    turns.append(t_node)
        # Also check label index for turns not yet linked to UserStore
        if db.csr_storage:
            for nid in db.csr_storage.node_types.get("Turn", set()):
                if not any(t.id == nid for t in turns):
                    t_node = db.get_node(nid)
                    if t_node:
                        turns.append(t_node)
        turns.sort(key=lambda n: (n.properties or {}).get("turn_index", 0), reverse=True)

        role_map = {"user": ContextRole.USER, "assistant": ContextRole.ASSISTANT}
        for t in turns[:max_turns]:
            props = t.properties or {}
            hub.add_text(
                props.get("content", ""),
                role=role_map.get(props.get("role"), ContextRole.INTERACTION),
                label=f"Turn ({props.get('role', 'user')})",
                source=f"turn:{t.id}",
            )
            hub.assign_to_slot(len(hub.items()) - 1, "turns")

        # --- Decisions/Actions from label index ---
        if db.csr_storage:
            for label in ("Decision", "Action"):
                for nid in db.csr_storage.node_types.get(label, set()):
                    n = db.get_node(nid)
                    if not n:
                        continue
                    props = n.properties or {}
                    if label == "Decision":
                        text = f"[Decision] {props.get('summary', '')}"
                    else:
                        text = f"[Action] {props.get('description', '')} (status: {props.get('status', 'pending')})"
                    hub.add_text(text, role=ContextRole.DECISION, label=label, source=f"{label.lower()}:{nid}")
                    hub.assign_to_slot(len(hub.items()) - 1, "decisions")

        return hub

    # ------------------------------------------------------------------
    # Schema management
    # ------------------------------------------------------------------

    def get_schema_dict(self) -> Dict[str, Any]:
        """Return the schema as a JSON-serializable dict."""
        if not self._schema:
            return {"error": "No schema loaded"}

        return {
            "namespace": self._schema.namespace,
            "version": self._schema.version,
            "description": self._schema.description,
            "node_types": {
                name: {
                    "description": nt.description,
                    "fields": {
                        fname: {
                            "type": f.field_type.value,
                            "required": f.required,
                        }
                        for fname, f in nt.fields.items()
                    },
                }
                for name, nt in self._schema.node_types.items()
            },
            "edge_types": {
                name: {
                    "from": et.from_nodes,
                    "to": et.to_nodes,
                    "relation": et.relation,
                }
                for name, et in self._schema.edge_types.items()
            },
        }

    async def update_schema(self, yaml_content: str):
        """
        Parse and apply a new schema YAML.

        Replaces the current schema and validator. Does NOT retroactively
        validate existing nodes — only new writes are validated.
        """
        try:
            from ..schema.schema_parser import SchemaParser
        except ImportError:
            raise RuntimeError("Schema module not available; cannot update schema")
        try:
            from ..ingestion.schema_validator import SchemaValidator
        except ImportError:
            SchemaValidator = None

        # Write to temp file for SchemaParser
        tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False)
        try:
            tmp.write(yaml_content)
            tmp.flush()
            tmp.close()

            parser = SchemaParser(tmp.name)
            new_schema = parser.parse()
        finally:
            try:
                os.unlink(tmp.name)
            except OSError:
                pass

        self._schema = new_schema
        self._validator = SchemaValidator(new_schema) if SchemaValidator else None

        # Update graphifier references
        if self._graphifier:
            self._graphifier._schema = new_schema
            self._graphifier._validator = self._validator

        # Optionally persist back to the schema file
        if self._schema_path:
            try:
                with open(self._schema_path, "w") as f:
                    f.write(yaml_content)
                logger.info("Updated session graph schema at %s", self._schema_path)
            except OSError as e:
                logger.warning("Could not persist schema update: %s", e)

        return new_schema

    # ------------------------------------------------------------------
    # Query helpers
    # ------------------------------------------------------------------

    def get_recent_turns(self, limit: int = 20) -> List[Dict[str, Any]]:
        """Get recent Turn nodes as dicts."""
        turns = [
            n for n in self.db.get_all_nodes()
            if n.label == "Turn"
        ]
        turns.sort(key=lambda n: n.properties.get("turn_index", 0), reverse=True)
        return [
            {"id": n.id, "label": n.label, **n.properties}
            for n in turns[:limit]
        ]

    def get_decisions(self, include_done: bool = False) -> List[Dict[str, Any]]:
        """Get Decision nodes as dicts."""
        nodes = [
            n for n in self.db.get_all_nodes()
            if n.label == "Decision"
            and (include_done or n.properties.get("status", "") != "done")
        ]
        nodes.sort(
            key=lambda n: n.properties.get("decided_at", ""),
            reverse=True,
        )
        return [{"id": n.id, "label": n.label, **n.properties} for n in nodes]

    def get_topics(self) -> List[Dict[str, Any]]:
        """Get Topic nodes as dicts, sorted by mention count."""
        topics = [n for n in self.db.get_all_nodes() if n.label == "Topic"]
        topics.sort(key=lambda n: n.properties.get("mention_count", 0), reverse=True)
        return [{"id": n.id, "label": n.label, **n.properties} for n in topics]

    def get_graph_stats(self) -> Dict[str, Any]:
        """Get summary stats of the session graph."""
        all_nodes = self.db.get_all_nodes()
        all_edges = self.db.get_all_edges()

        # Count by label
        node_counts: Dict[str, int] = {}
        for n in all_nodes:
            node_counts[n.label] = node_counts.get(n.label, 0) + 1

        edge_counts: Dict[str, int] = {}
        for e in all_edges:
            edge_counts[e.label] = edge_counts.get(e.label, 0) + 1

        return {
            "session_id": self.session.session_id,
            "graph_namespace": self.session.graph_namespace,
            "total_nodes": len(all_nodes),
            "total_edges": len(all_edges),
            "node_counts": node_counts,
            "edge_counts": edge_counts,
        }

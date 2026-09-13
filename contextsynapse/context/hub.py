"""
ContextHub
==========
A structured context builder for agentic LLM workflows.

Nodes, edges, query results, and free-form text can all be collected into
a single hub and then exported in the format that any downstream model
expects (OpenAI/Anthropic messages, raw prompt string, JSON dict, …).
"""

from __future__ import annotations

import copy
import json
import textwrap
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional, Union


class ContextRole(str, Enum):
    """Role of a context item – mirrors the standard LLM roles."""
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    # Semantic roles understood by the export layer
    BACKGROUND = "background"   # general knowledge / graph facts
    RETRIEVED = "retrieved"     # RAG-retrieved chunks / nodes
    INSTRUCTION = "instruction" # task-specific instructions
    EXAMPLE = "example"         # few-shot examples
    # Agent-generated context (output from LLM calls / reasoning)
    GENERATED = "generated"     # raw LLM output
    SYNTHESIS = "synthesis"     # summary / analysis derived from other items
    DECISION = "decision"       # conclusion or action decided by an agent
    # Phase 3: Extended context types
    DOCUMENT = "document"       # full source documents (vs RETRIEVED snippets)
    INTERACTION = "interaction" # multi-turn dialogue transcripts between agents
    HUMAN = "human"             # explicitly human-provided context
    TOOL = "tool"               # tool call results
    FEEDBACK = "feedback"       # human corrections, validations, RLHF signals


class ContextFormat(str, Enum):
    """Target export format."""
    MESSAGES = "messages"       # List[{"role": ..., "content": ...}]
    PROMPT = "prompt"           # Single concatenated string
    DICT = "dict"               # Serialisable dict (for custom pipelines)
    MARKDOWN = "markdown"       # Human-readable markdown


class Sensitivity(str, Enum):
    """Classification level for context items."""
    PUBLIC = "public"           # visible to any agent with session access
    INTERNAL = "internal"       # visible to agents with write+ access
    CONFIDENTIAL = "confidential"  # visible only to agents with explicit tag grant
    RESTRICTED = "restricted"   # visible only to admins


@dataclass
class ContextItem:
    """A single piece of context with metadata."""
    content: str
    role: ContextRole = ContextRole.USER
    label: Optional[str] = None          # human-readable label / section heading
    source: Optional[str] = None         # where the context came from
    metadata: Dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    agent_id: Optional[str] = None       # which agent wrote this item
    provenance_id: Optional[str] = None  # link to provenance record
    # ── Tagging & authorization ──────────────────────────────
    tags: List[str] = field(default_factory=list)           # e.g. ["financial", "q4"]
    sensitivity: str = "public"                              # public|internal|confidential|restricted
    pii_detected: bool = False                               # set by auto-tagger on ingest
    # ── Phase 3: Confidence, lifecycle, slots ─────────────
    confidence: float = 1.0                                  # [0.0, 1.0] trust score
    state: str = "active"                                    # active | stale | archived
    _slot: Optional[str] = None                              # assigned context slot name

    def to_dict(self) -> Dict[str, Any]:
        return {
            "role": self.role.value,
            "label": self.label,
            "source": self.source,
            "content": self.content,
            "metadata": self.metadata,
            "created_at": self.created_at,
            "agent_id": self.agent_id,
            "provenance_id": self.provenance_id,
            "tags": self.tags,
            "sensitivity": self.sensitivity,
            "pii_detected": self.pii_detected,
            "confidence": self.confidence,
            "state": self.state,
            "_slot": self._slot,
        }


@dataclass
class SlotConfig:
    """A named token bucket within the context hub."""
    name: str
    max_tokens: int
    roles: Optional[List[ContextRole]] = None  # auto-assign items with these roles
    pinned: bool = False                       # pinned slots survive any scoping
    priority: int = 5                          # export ordering (higher = earlier)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _resolve_thin_node(node: Any) -> dict:
    """If node has a _ref pointer, resolve full content from DuckDB."""
    props = getattr(node, "properties", None) or (node if isinstance(node, dict) else {})
    if isinstance(props, dict) and props.get("_ref", "").startswith("duckdb:"):
        try:
            from contextsynapse.storage.router import get_content_resolver
            return get_content_resolver().resolve(node)
        except Exception:
            pass
    return {}


def _node_to_text(node: Any) -> str:
    """Convert a GraphNode (or dict) to a compact text representation.

    If the node has a _ref pointer (thin graph node), auto-resolves
    full content from DuckDB before rendering.
    """
    _PRIORITY_KEYS = ("name", "title", "text", "content", "statement", "description", "status", "type")
    _SKIP_KEYS = {"uuid", "domain", "_ref", "_created_at", "_agent_id", "_origin",
                  "_verified", "_region", "source_type", "valid_from", "valid_to", "version"}

    # Auto-resolve thin nodes
    resolved = _resolve_thin_node(node)

    if isinstance(node, dict):
        props = {**node, **resolved}
        props = {k: v for k, v in props.items() if k not in _SKIP_KEYS}
        label = props.pop("label", None) or props.pop("node_type", None) or "Node"
        nid = props.pop("uuid", "") or props.pop("id", "")
        lines = [f"[{label}]" + (f" (id: {nid})" if nid else "")]
        sorted_keys = [k for k in _PRIORITY_KEYS if k in props]
        sorted_keys += [k for k in props if k not in _PRIORITY_KEYS]
        for k in sorted_keys:
            lines.append(f"  {k}: {props[k]}")
        return "\n".join(lines)

    # GraphNode object
    label = getattr(node, "label", "Node")
    nid = getattr(node, "id", "")
    props = {**(getattr(node, "properties", {}) or {}), **resolved}
    props = {k: v for k, v in props.items() if k not in _SKIP_KEYS}
    lines = [f"[{label}]" + (f" (id: {nid})" if nid else "")]
    sorted_keys = [k for k in _PRIORITY_KEYS if k in props]
    sorted_keys += [k for k in props if k not in _PRIORITY_KEYS]
    for k in sorted_keys:
        lines.append(f"  {k}: {props[k]}")
    return "\n".join(lines)


def _edge_to_text(edge: Any, node_names: Optional[Dict[str, str]] = None) -> str:
    """Convert a GraphEdge (or dict) to human-readable text.

    When *node_names* is provided (id → display name), the edge is rendered
    with readable names instead of raw UUIDs, e.g.:
        "Alice" -[WORKS_ON]-> "Project X"
    """
    names = node_names or {}

    if isinstance(edge, dict):
        etype = edge.get("label") or edge.get("edge_type") or "EDGE"
        src = str(edge.get("source") or edge.get("from_id", "?"))
        tgt = str(edge.get("target") or edge.get("to_id", "?"))
        props = {k: v for k, v in edge.items()
                 if k not in ("label", "edge_type", "source", "from_id", "target", "to_id", "uuid")}
    else:
        etype = getattr(edge, "label", getattr(edge, "edge_type", "EDGE"))
        src = str(getattr(edge, "source_id", getattr(edge, "from_id", "?")))
        tgt = str(getattr(edge, "target_id", getattr(edge, "to_id", "?")))
        props = {}

    src_display = names.get(src) or src[:12]
    tgt_display = names.get(tgt) or tgt[:12]
    line = f"{src_display} -[{etype}]-> {tgt_display}"
    if props:
        line += "  " + ", ".join(f"{k}={v}" for k, v in props.items())
    return line


# ---------------------------------------------------------------------------
# ContextHub
# ---------------------------------------------------------------------------

class ContextHub:
    """
    Build, collect, and export structured context for LLMs.

    Quick-start
    -----------
    >>> hub = ContextHub(system_prompt="You are a helpful graph analyst.")
    >>> hub.add_text("Company overview …", role="background")
    >>> hub.add_nodes(graph.get_all_nodes())
    >>> messages = hub.to_messages()   # ready for openai.chat.completions.create(messages=…)
    """

    def __init__(
        self,
        system_prompt: Optional[str] = None,
        max_tokens: int = 8000,
        token_estimate_ratio: float = 4.0,   # chars per token (rough)
    ):
        self._items: List[ContextItem] = []
        self.max_tokens = max_tokens
        self._token_ratio = token_estimate_ratio
        self._scoper = None  # Optional ContextScoper
        self._compression_config = None  # Optional CompressionConfig
        self._slots: Dict[str, SlotConfig] = {}  # name -> slot config
        self._db = None  # Optional graph ref for ContextMeta + lineage + contradiction detection
        self._memory = None  # Optional AgentMemory for memory subgraph
        self._memory_agent_id = None
        self._memory_query = None
        self._last_scored_items = []  # For explainability

        if system_prompt:
            self.add_text(system_prompt, role=ContextRole.SYSTEM, label="System")

    def set_scoping(self, config) -> "ContextHub":
        """
        Enable token-budget scoping on exports.

        Args:
            config: A ``ScopingConfig`` instance (from ``contextsynapse.context.scoping``).
        """
        from .scoping import ContextScoper
        self._scoper = ContextScoper(config)
        return self

    # ------------------------------------------------------------------
    # Fluent add-methods
    # ------------------------------------------------------------------

    def add_text(
        self,
        text: str,
        role: Union[ContextRole, str] = ContextRole.USER,
        label: Optional[str] = None,
        source: Optional[str] = None,
        metadata: Optional[Dict] = None,
        tags: Optional[List[str]] = None,
        sensitivity: str = "public",
    ) -> "ContextHub":
        """Add free-form text."""
        self._items.append(ContextItem(
            content=text.strip(),
            role=ContextRole(role) if isinstance(role, str) else role,
            label=label,
            source=source,
            metadata=metadata or {},
            tags=tags or [],
            sensitivity=sensitivity,
        ))
        return self

    def add_nodes(
        self,
        nodes: List[Any],
        role: Union[ContextRole, str] = ContextRole.RETRIEVED,
        label: Optional[str] = None,
        source: Optional[str] = None,
    ) -> "ContextHub":
        """Add a list of GraphNode objects or dicts as context."""
        if not nodes:
            return self
        lines = [f"## Graph Nodes ({len(nodes)} total)"]
        for node in nodes:
            lines.append(_node_to_text(node))
        self._items.append(ContextItem(
            content="\n".join(lines),
            role=ContextRole(role) if isinstance(role, str) else role,
            label=label or "Graph Nodes",
            source=source or "graph",
            metadata={"count": len(nodes)},
        ))
        return self

    def add_edges(
        self,
        edges: List[Any],
        role: Union[ContextRole, str] = ContextRole.RETRIEVED,
        label: Optional[str] = None,
        source: Optional[str] = None,
        node_names: Optional[Dict[str, str]] = None,
    ) -> "ContextHub":
        """Add a list of GraphEdge objects or dicts as context.

        Args:
            node_names: Optional mapping of node ID → display name for
                readable edge rendering.
        """
        if not edges:
            return self
        lines = [f"## Graph Relationships ({len(edges)} total)"]
        for edge in edges:
            lines.append(_edge_to_text(edge, node_names=node_names))
        self._items.append(ContextItem(
            content="\n".join(lines),
            role=ContextRole(role) if isinstance(role, str) else role,
            label=label or "Graph Relationships",
            source=source or "graph",
            metadata={"count": len(edges)},
        ))
        return self

    def add_query_result(
        self,
        result: Any,
        query: Optional[str] = None,
        role: Union[ContextRole, str] = ContextRole.RETRIEVED,
        label: Optional[str] = None,
    ) -> "ContextHub":
        """Add the result of an AIQL query (list, dict, or raw string)."""
        if result is None:
            return self
        if isinstance(result, list):
            # List of nodes / dicts
            if result and isinstance(result[0], dict):
                self.add_nodes(result, role=role, label=label or (f"Query: {query}" if query else "Query Result"))
            else:
                text = "\n".join(str(r) for r in result)
                self.add_text(text, role=role, label=label or "Query Result")
        elif isinstance(result, dict):
            nodes = result.get("nodes") or result.get("data", {}).get("nodes", [])
            if nodes:
                self.add_nodes(nodes, role=role, label=label or (f"Query: {query}" if query else "Query Result"))
            else:
                self.add_text(json.dumps(result, indent=2), role=role, label=label or "Query Result")
        else:
            self.add_text(str(result), role=role, label=label)
        return self

    def add_graph_summary(
        self, db: Any, namespace: str = "default", categories: Optional[List[str]] = None,
    ) -> "ContextHub":
        """
        Pull a summary (node/edge counts by type) from a live AIContextDB instance
        and add it as background context.

        Args:
            db: AIContextDB graph instance.
            namespace: Graph namespace name.
            categories: If provided, only include nodes matching these categories.
        """
        from .boundaries import BOUNDARY_NODE_LABELS

        try:
            all_nodes = db.get_all_nodes()
            type_counts: Dict[str, int] = {}
            filtered_count = 0
            for n in all_nodes:
                if isinstance(n, dict):
                    lbl = n.get("label", "?")
                    cat = n.get("properties", {}).get("category") or n.get("category")
                else:
                    lbl = getattr(n, "label", "?")
                    props = getattr(n, "properties", {}) or {}
                    cat = props.get("category")

                # Skip boundary nodes from summary
                if lbl in BOUNDARY_NODE_LABELS:
                    continue

                # Category filter
                if categories and cat not in categories:
                    continue

                filtered_count += 1
                type_counts[lbl] = type_counts.get(lbl, 0) + 1

            lines = [f"## Graph Summary (namespace: {namespace})"]
            if categories:
                lines.append(f"Categories: {', '.join(categories)}")
            lines.append(f"Total nodes: {filtered_count}")
            for t, c in sorted(type_counts.items()):
                lines.append(f"  {t}: {c}")
            self.add_text("\n".join(lines), role=ContextRole.BACKGROUND, label="Graph Summary", source="graph")
        except Exception as exc:
            self.add_text(f"(Graph summary unavailable: {exc})", role=ContextRole.BACKGROUND)
        return self

    def add_graph_category(
        self, db: Any, namespace: str, category: str, role: Union["ContextRole", str] = "background",
    ) -> "ContextHub":
        """Pull nodes from one specific category and add their content.

        Args:
            db: AIContextDB graph instance.
            namespace: Graph namespace name.
            category: Category to pull (e.g. "knowledge_base", "user_message").
            role: ContextRole for the added items.
        """
        from .boundaries import BOUNDARY_NODE_LABELS

        try:
            all_nodes = db.get_all_nodes()
            items_added = 0
            for n in all_nodes:
                if isinstance(n, dict):
                    lbl = n.get("label", "")
                    props = n.get("properties", {})
                else:
                    lbl = getattr(n, "label", "")
                    props = getattr(n, "properties", {}) or {}

                if lbl in BOUNDARY_NODE_LABELS:
                    continue

                if props.get("category") != category:
                    continue

                content = props.get("content", "")
                if not content:
                    continue

                ctx_role = ContextRole(role) if isinstance(role, str) else role
                node_label = props.get("label") or props.get("name") or f"{lbl} item"
                self.add_text(content, role=ctx_role, label=node_label, source=f"context:{namespace}")
                items_added += 1

            if items_added == 0:
                self.add_text(
                    f"(No items in category '{category}' for namespace '{namespace}')",
                    role=ContextRole.BACKGROUND,
                )
        except Exception as exc:
            self.add_text(f"(Category pull failed: {exc})", role=ContextRole.BACKGROUND)
        return self

    def contribute(
        self,
        content: str,
        agent_id: str,
        role: Union[ContextRole, str] = ContextRole.GENERATED,
        label: Optional[str] = None,
        source_ids: Optional[List[str]] = None,
        metadata: Optional[Dict] = None,
        tags: Optional[List[str]] = None,
        sensitivity: str = "internal",
        confidence: float = 0.8,
    ) -> "ContextHub":
        """
        Write agent-generated output back into the context.

        This is the primary method agents use to share their work with other
        agents.  The ``source_ids`` field records which context items or graph
        nodes this output was derived from (lineage).

        Args:
            content:    The agent's output text (summary, analysis, decision, …)
            agent_id:   Who produced this
            role:       generated | synthesis | decision
            label:      Human-readable heading
            source_ids: IDs of items/nodes this was derived from
            metadata:   Extra info (model used, confidence, etc.)
            tags:       Classification tags for this item
            sensitivity: public|internal|confidential|restricted (default: internal)
            confidence: Trust score [0.0, 1.0]. When source_ids are provided,
                        auto-computed as min(parent_confidences) * 0.95 if lower.
        """
        meta = metadata or {}
        if source_ids:
            meta["derived_from"] = source_ids
            # Auto-compute derived confidence from parents
            parent_confs = []
            for item in self._items:
                pid = item.provenance_id or item.label
                if pid and pid in source_ids:
                    parent_confs.append(item.confidence)
            if parent_confs:
                derived_conf = min(parent_confs) * 0.95
                confidence = min(confidence, derived_conf)
        # Gracefully handle unknown role strings (e.g., "finding", "observation")
        if isinstance(role, str):
            try:
                resolved_role = ContextRole(role)
            except ValueError:
                resolved_role = ContextRole.GENERATED
        else:
            resolved_role = role
        self._items.append(ContextItem(
            content=content.strip(),
            role=resolved_role,
            label=label or f"Agent output ({agent_id[:8]})",
            source="agent",
            metadata=meta,
            agent_id=agent_id,
            tags=tags or [],
            sensitivity=sensitivity,
            confidence=confidence,
        ))
        return self

    def add_instruction(self, instruction: str) -> "ContextHub":
        """Add a task instruction (maps to USER role in most LLMs)."""
        return self.add_text(instruction, role=ContextRole.INSTRUCTION, label="Instruction")

    def add_example(self, example: str, label: Optional[str] = None) -> "ContextHub":
        """Add a few-shot example."""
        return self.add_text(example, role=ContextRole.EXAMPLE, label=label or "Example")

    @classmethod
    def rag_query(
        cls,
        question: str,
        retrieved_nodes: list,
        llm,
        model_name: str = None,
    ) -> Dict[str, Any]:
        """Build a RAG prompt from retrieved nodes and generate an answer.

        Args:
            question: The user's question.
            retrieved_nodes: List of node dicts with 'properties' or raw props.
            llm: AIContextDBUniversalLLM instance.
            model_name: LLM model to use (defaults to first available).

        Returns:
            ``{"answer": str, "sources": [...], "hub": ContextHub}``
        """
        hub = cls(system_prompt=(
            "Answer the question based ONLY on the provided context. "
            "Cite source names. If context is insufficient, say so."
        ))
        sources = []
        for node in retrieved_nodes:
            props = getattr(node, "properties", node) if not isinstance(node, dict) else node.get("properties", node)
            content = props.get("content") or props.get("statement") or props.get("name", "")
            label = props.get("name") or props.get("label", "Source")
            hub.add_text(str(content), role=ContextRole.RETRIEVED, label=label)
            sources.append({"label": label, "node_id": str(props.get("node_id", ""))})

        hub.add_text(question, role=ContextRole.INSTRUCTION, label="Question")

        model = model_name or (llm.list_models()[0] if llm.list_models() else None)
        if not model:
            return {"answer": hub.to_prompt(), "sources": sources, "hub": hub}

        result = llm.generate(model, hub.to_prompt())
        return {
            "answer": result.get("response", "Could not generate answer."),
            "sources": sources,
            "hub": hub,
        }

    # ------------------------------------------------------------------
    # Phase 3: Extended context type methods
    # ------------------------------------------------------------------

    def add_document(
        self,
        content: str,
        label: Optional[str] = None,
        source: Optional[str] = None,
        metadata: Optional[Dict] = None,
        tags: Optional[List[str]] = None,
        sensitivity: str = "public",
    ) -> "ContextHub":
        """Add a full source document (distinct from retrieved snippets)."""
        return self.add_text(
            content, role=ContextRole.DOCUMENT,
            label=label or "Document", source=source,
            metadata=metadata, tags=tags, sensitivity=sensitivity,
        )

    def add_interaction(
        self,
        content: str,
        label: Optional[str] = None,
        source: Optional[str] = None,
        metadata: Optional[Dict] = None,
    ) -> "ContextHub":
        """Add a multi-turn dialogue transcript."""
        return self.add_text(
            content, role=ContextRole.INTERACTION,
            label=label or "Interaction", source=source, metadata=metadata,
        )

    def add_feedback(
        self,
        content: str,
        agent_id: Optional[str] = None,
        label: Optional[str] = None,
        metadata: Optional[Dict] = None,
    ) -> "ContextHub":
        """Add human feedback / correction. Confidence pinned to 1.0."""
        self._items.append(ContextItem(
            content=content.strip(),
            role=ContextRole.FEEDBACK,
            label=label or "Feedback",
            source="human",
            metadata=metadata or {},
            agent_id=agent_id,
            confidence=1.0,
        ))
        return self

    def add_tool_result(
        self,
        content: str,
        tool_name: str,
        label: Optional[str] = None,
        metadata: Optional[Dict] = None,
        confidence: float = 0.9,
    ) -> "ContextHub":
        """Add a tool call result with optional confidence."""
        meta = metadata or {}
        meta["tool_name"] = tool_name
        self._items.append(ContextItem(
            content=content.strip(),
            role=ContextRole.TOOL,
            label=label or f"Tool: {tool_name}",
            source="tool",
            metadata=meta,
            confidence=confidence,
        ))
        return self

    # ------------------------------------------------------------------
    # Phase 3: Confidence & lifecycle management
    # ------------------------------------------------------------------

    def set_confidence(self, index: int, confidence: float) -> "ContextHub":
        """Set confidence on a specific item by index."""
        self._items[index].confidence = max(0.0, min(1.0, confidence))
        return self

    def mark_stale(self, index_or_label: Union[int, str]) -> "ContextHub":
        """Mark an item stale by index or label. Triggers propagate_staleness()."""
        if isinstance(index_or_label, int):
            self._items[index_or_label].state = "stale"
        else:
            for item in self._items:
                if item.label == index_or_label:
                    item.state = "stale"
        self.propagate_staleness()
        return self

    def propagate_staleness(self) -> int:
        """Mark derived items as stale when their sources are stale.

        Returns the count of newly stale items.
        """
        # Build set of identifiers for stale items
        stale_ids: set = set()
        for i, item in enumerate(self._items):
            if item.state == "stale":
                if item.provenance_id:
                    stale_ids.add(item.provenance_id)
                if item.label:
                    stale_ids.add(item.label)
                stale_ids.add(str(i))

        newly_stale = 0
        changed = True
        while changed:
            changed = False
            for i, item in enumerate(self._items):
                if item.state != "active":
                    continue
                derived = item.metadata.get("derived_from", [])
                if not derived:
                    continue
                if any(src in stale_ids for src in derived):
                    item.state = "stale"
                    newly_stale += 1
                    changed = True
                    if item.provenance_id:
                        stale_ids.add(item.provenance_id)
                    if item.label:
                        stale_ids.add(item.label)
                    stale_ids.add(str(i))

        return newly_stale

    def refresh(self, index: int) -> "ContextHub":
        """Restore an item to active state."""
        self._items[index].state = "active"
        return self

    def archive(self, index: int) -> "ContextHub":
        """Archive an item — excluded from all exports."""
        self._items[index].state = "archived"
        return self

    # ------------------------------------------------------------------
    # Phase 3: Context Slots
    # ------------------------------------------------------------------

    def define_slot(
        self,
        name: str,
        max_tokens: int,
        roles: Optional[List[ContextRole]] = None,
        pinned: bool = False,
        priority: int = 5,
    ) -> "ContextHub":
        """Define a named token bucket."""
        self._slots[name] = SlotConfig(
            name=name, max_tokens=max_tokens,
            roles=roles, pinned=pinned, priority=priority,
        )
        return self

    def assign_to_slot(self, index: int, slot_name: str) -> "ContextHub":
        """Manually assign an item to a named slot."""
        if slot_name not in self._slots:
            raise ValueError(f"Slot '{slot_name}' not defined")
        self._items[index]._slot = slot_name
        return self

    def set_slot(
        self,
        name: str,
        content: str,
        role: Union[ContextRole, str] = ContextRole.USER,
        label: Optional[str] = None,
        **kwargs,
    ) -> "ContextHub":
        """Overwrite a slot's content (removes old items in this slot, adds new one)."""
        if name not in self._slots:
            raise ValueError(f"Slot '{name}' not defined")
        self._items = [i for i in self._items if i._slot != name]
        item = ContextItem(
            content=content.strip(),
            role=ContextRole(role) if isinstance(role, str) else role,
            label=label or name,
            _slot=name,
            **kwargs,
        )
        self._items.append(item)
        return self

    def _auto_assign_slots(self) -> None:
        """Match unassigned items to slots by role."""
        for item in self._items:
            if item._slot is not None:
                continue
            for slot in self._slots.values():
                if slot.roles and item.role in slot.roles:
                    item._slot = slot.name
                    break

    # ------------------------------------------------------------------
    # Phase 3: Compression
    # ------------------------------------------------------------------

    def set_compression(self, config) -> "ContextHub":
        """Enable compression tiers on exports."""
        self._compression_config = config
        return self

    def _get_rendered_content(self, item: ContextItem) -> str:
        """Return content based on the item's compression tier."""
        tier = item.metadata.get("_compression_tier", 1)
        if tier <= 1:
            return item.content
        if tier == 2:
            cached = item.metadata.get("_summary")
            if cached:
                return cached
            if self._compression_config is not None:
                from .compression import generate_summary
                summary = generate_summary(item.content, self._compression_config)
                item.metadata["_summary"] = summary
                return summary
            return item.content[:300] + "..."
        if tier == 3:
            lbl = item.label or item.role.value
            tokens = max(1, int(len(item.content) / self._token_ratio))
            return f"[{lbl}] ~{tokens} tokens, confidence: {item.confidence:.2f}"
        return ""

    # ------------------------------------------------------------------
    # Inspection
    # ------------------------------------------------------------------

    def __len__(self) -> int:
        return len(self._items)

    def estimate_tokens(self) -> int:
        total_chars = sum(len(item.content) for item in self._items)
        return int(total_chars / self._token_ratio)

    def is_within_limit(self) -> bool:
        return self.estimate_tokens() <= self.max_tokens

    def clear(self) -> "ContextHub":
        """Remove all context items."""
        self._items.clear()
        return self

    def items(self) -> List[ContextItem]:
        return list(self._items)

    def filter(
        self,
        role: Optional[Union[ContextRole, str]] = None,
        source: Optional[str] = None,
        agent_id: Optional[str] = None,
        time_after: Optional[str] = None,
        time_before: Optional[str] = None,
        label: Optional[str] = None,
        tags: Optional[List[str]] = None,
        sensitivity: Optional[str] = None,
        max_sensitivity: Optional[str] = None,
    ) -> List[ContextItem]:
        """Return items matching ALL supplied criteria.

        Args:
            tags: If provided, item must contain at least one of these tags.
            sensitivity: Exact sensitivity match.
            max_sensitivity: Return items at or below this level
                (public < internal < confidential < restricted).
        """
        _SENS_ORDER = {"public": 0, "internal": 1, "confidential": 2, "restricted": 3}

        result = list(self._items)
        if role is not None:
            r = ContextRole(role) if isinstance(role, str) else role
            result = [i for i in result if i.role == r]
        if source is not None:
            result = [i for i in result if i.source == source]
        if agent_id is not None:
            result = [i for i in result if i.agent_id == agent_id]
        if time_after is not None:
            result = [i for i in result if i.created_at >= time_after]
        if time_before is not None:
            result = [i for i in result if i.created_at <= time_before]
        if label is not None:
            lbl = label.lower()
            result = [i for i in result if i.label and lbl in i.label.lower()]
        if tags is not None:
            tag_set = set(tags)
            result = [i for i in result if tag_set & set(i.tags)]
        if sensitivity is not None:
            result = [i for i in result if i.sensitivity == sensitivity]
        if max_sensitivity is not None:
            ceiling = _SENS_ORDER.get(max_sensitivity, 0)
            result = [i for i in result if _SENS_ORDER.get(i.sensitivity, 0) <= ceiling]
        return result

    def filter_for_agent(
        self,
        access_level: str,
        allowed_tags: Optional[List[str]] = None,
        pii_mode: str = "block",
    ) -> List[ContextItem]:
        """Return only items this agent is authorized to see.

        Policy:
        - ``read``  → public items only (+ items matching allowed_tags)
        - ``write`` → public + internal (+ allowed_tags)
        - ``admin`` → everything

        Args:
            pii_mode: How to handle PII-containing items that the agent
                      *is* authorized to see:
                - ``"block"``  — drop items with ``pii_detected=True`` unless
                  agent has ``"pii"`` in allowed_tags (current default).
                - ``"mask"``   — keep the item but replace PII patterns with
                  ``***`` using ``PIIDetector.mask_pii()``.
                - ``"redact"`` — keep the item structure but replace content
                  with ``"[REDACTED — contains PII]"``.
                - ``"allow"``  — return items unmodified (for agents with
                  explicit PII access).
        """
        _LEVEL_CEILING = {"read": "public", "write": "internal", "admin": "restricted"}
        ceiling = _LEVEL_CEILING.get(access_level, "public")

        base = self.filter(max_sensitivity=ceiling)

        # Add higher-sensitivity items that match allowed_tags
        if allowed_tags:
            tag_items = self.filter(tags=allowed_tags)
            base_ids = {id(i) for i in base}
            for item in tag_items:
                if id(item) not in base_ids:
                    base.append(item)

        # Apply PII mode
        has_pii_access = allowed_tags and "pii" in allowed_tags
        if pii_mode == "allow" or has_pii_access:
            return base

        if pii_mode == "block":
            return [i for i in base if not i.pii_detected]

        if pii_mode in ("mask", "redact"):
            from ..security.data_security import PIIDetector
            result = []
            for item in base:
                if not item.pii_detected:
                    result.append(item)
                else:
                    masked = copy.copy(item)
                    if pii_mode == "mask":
                        masked.content = PIIDetector.mask_pii(item.content)
                    else:  # redact
                        masked.content = "[REDACTED — contains PII]"
                    result.append(masked)
            return result

        return base

    # ------------------------------------------------------------------
    # Scoping & unified pipeline
    # ------------------------------------------------------------------

    def _apply_scoping(self, budget: Optional[int] = None) -> List[ContextItem]:
        """Apply token-budget scoping if a scoper is configured or budget is given."""
        if budget is not None and self._scoper is not None:
            return self._scoper.select_within_budget(self._items, max_tokens=budget)
        if budget is not None:
            from .scoping import ContextScoper
            return ContextScoper().select_within_budget(self._items, max_tokens=budget)
        if self._scoper is not None:
            return self._scoper.select_within_budget(self._items)
        return self._items

    def get_last_scored_items(self):
        """Return scored items from last assembly, with breakdowns."""
        return self._last_scored_items

    def _prepare_items(self, budget: Optional[int] = None) -> List[ContextItem]:
        """Unified export pipeline: filter → slots → score → compress → budget.

        When no slots or compression are configured, falls back to _apply_scoping.
        """
        # C1: Prepend ContextMeta if graph is attached
        if self._db and not any(getattr(i, 'label', '') == "ContextMeta" for i in self._items):
            try:
                meta_node = self._db.csr_adapter.get_node("_context_meta")
                if meta_node:
                    mp = meta_node.properties or {}
                    meta_text = (
                        f"This context is from: {mp.get('name', 'unknown')}. "
                        f"It contains: {mp.get('schema_summary', 'various nodes')}. "
                        f"Purpose: {mp.get('purpose', 'knowledge_base')}. "
                        f"Nodes: {mp.get('node_count', '?')}, Edges: {mp.get('edge_count', '?')}."
                    )
                    self._items.insert(0, ContextItem(
                        content=meta_text, role=ContextRole.BACKGROUND,
                        label="ContextMeta", source="context_meta",
                    ))
            except Exception:
                pass

        # C2: Pull from memory subgraph if available
        if self._memory and self._memory_agent_id:
            try:
                query = self._memory_query or ""
                memories = self._memory.recall(self._memory_agent_id, query=query, limit=10)
                for m in memories:
                    if not any(i.content == m["content"] for i in self._items):
                        self._items.append(ContextItem(
                            content=m["content"], role=ContextRole.BACKGROUND,
                            label=f"Memory ({m.get('tags', [''])[0] if m.get('tags') else 'fact'})",
                            source="agent_memory",
                            confidence=m.get("effective_confidence", m.get("confidence", 1.0)),
                            metadata={"memory_id": m["id"]},
                        ))
            except Exception:
                pass

        # C5: Contradiction detection
        if self._db:
            try:
                node_ids = [i.metadata.get("node_id") for i in self._items if hasattr(i, 'metadata') and i.metadata and i.metadata.get("node_id")]
                if len(node_ids) >= 2:
                    node_id_set = set(node_ids)
                    edges = self._db.csr_adapter.get_all_edges() if hasattr(self._db.csr_adapter, 'get_all_edges') else []
                    for edge in edges:
                        elabel = getattr(edge, 'edge_type', getattr(edge, 'label', ''))
                        src = getattr(edge, 'source_id', getattr(edge, 'source', ''))
                        tgt = getattr(edge, 'target_id', getattr(edge, 'target', ''))
                        if elabel == "CONTRADICTS" and src in node_id_set and tgt in node_id_set:
                            src_item = next((i for i in self._items if i.metadata and i.metadata.get("node_id") == src), None)
                            tgt_item = next((i for i in self._items if i.metadata and i.metadata.get("node_id") == tgt), None)
                            if src_item and tgt_item:
                                warning = f"Conflict: '{src_item.content[:60]}' contradicts '{tgt_item.content[:60]}'"
                                self._items.append(ContextItem(
                                    content=warning, role=ContextRole.SYSTEM,
                                    label="Contradiction Warning", source="contradiction_detection",
                                    tags=["conflict"],
                                ))
            except Exception:
                pass

        # Fast path: no Phase 3 features active
        has_phase3 = self._slots or self._compression_config or any(
            i.state != "active" for i in self._items
        )
        if not has_phase3:
            result = self._apply_scoping(budget)
            # Store scored items for explainability (C6)
            if self._scoper:
                try:
                    self._last_scored_items = self._scoper.score_items(self._items)
                except Exception:
                    pass
            return result

        # 1. Filter out archived items
        active = [i for i in self._items if i.state != "archived"]

        # 2. Auto-assign items to slots
        if self._slots:
            for item in active:
                if item._slot is None:
                    for slot in self._slots.values():
                        if slot.roles and item.role in slot.roles:
                            item._slot = slot.name
                            break

        # 3. Separate pinned-slot items
        pinned: List[ContextItem] = []
        remaining: List[ContextItem] = []
        for item in active:
            if item._slot and self._slots.get(item._slot, SlotConfig("", 0)).pinned:
                pinned.append(item)
            else:
                remaining.append(item)

        # 4. Determine budget
        effective_budget = budget or (self._scoper.config.max_tokens if self._scoper else self.max_tokens)
        pinned_tokens = sum(max(1, int(len(i.content) / self._token_ratio)) for i in pinned)
        remaining_budget = max(0, effective_budget - pinned_tokens)

        # 5. Score remaining items
        from .scoping import ContextScoper
        scoper = self._scoper or ContextScoper()
        scored = scoper.score_items(remaining)
        self._last_scored_items = scored  # Store for explainability (C6)

        # 6. Apply compression tiers if configured
        if self._compression_config is not None:
            from .compression import assign_compression_tiers, CompressionTier
            tier_map = assign_compression_tiers(scored, self._compression_config)
            for si in scored:
                tier = tier_map.get(si.index, CompressionTier.FULL)
                si.item.metadata["_compression_tier"] = tier
                # Recalculate token estimate for compressed content
                rendered = self._get_rendered_content(si.item)
                si.token_estimate = max(1, int(len(rendered) / self._token_ratio))

        # 7. Per-slot budget enforcement
        slot_selected: Dict[str, set] = {name: set() for name in self._slots}
        slot_used: Dict[str, int] = {name: 0 for name in self._slots}

        # Sort by score descending for greedy selection
        scored.sort(key=lambda s: s.score, reverse=True)

        selected_indices: set = set()
        used_tokens = 0

        for si in scored:
            # Skip dropped items
            if si.item.metadata.get("_compression_tier", 1) >= 4:
                continue

            slot_name = si.item._slot
            if slot_name and slot_name in self._slots:
                slot_cfg = self._slots[slot_name]
                if slot_used[slot_name] + si.token_estimate <= slot_cfg.max_tokens:
                    if used_tokens + si.token_estimate <= remaining_budget:
                        selected_indices.add(si.index)
                        slot_used[slot_name] += si.token_estimate
                        used_tokens += si.token_estimate
            else:
                if used_tokens + si.token_estimate <= remaining_budget:
                    selected_indices.add(si.index)
                    used_tokens += si.token_estimate

        # 8. Reconstruct in original order
        selected_remaining = [remaining[i] for i in sorted(selected_indices)]

        # Merge pinned + selected, preserving original order from active list
        result_set = set(id(i) for i in pinned) | set(id(i) for i in selected_remaining)
        return [i for i in active if id(i) in result_set]

    # ------------------------------------------------------------------
    # Export
    # ------------------------------------------------------------------

    def _role_to_llm_role(self, role: ContextRole) -> str:
        """Map semantic roles to the standard system/user/assistant triad."""
        mapping = {
            ContextRole.SYSTEM: "system",
            ContextRole.USER: "user",
            ContextRole.ASSISTANT: "assistant",
            ContextRole.BACKGROUND: "user",
            ContextRole.RETRIEVED: "user",
            ContextRole.INSTRUCTION: "user",
            ContextRole.EXAMPLE: "user",
            # Agent-generated outputs map to assistant (they came from an LLM)
            ContextRole.GENERATED: "assistant",
            ContextRole.SYNTHESIS: "assistant",
            ContextRole.DECISION: "assistant",
            # Phase 3: Extended context types → user side
            ContextRole.DOCUMENT: "user",
            ContextRole.INTERACTION: "user",
            ContextRole.HUMAN: "user",
            ContextRole.TOOL: "user",
            ContextRole.FEEDBACK: "user",
        }
        return mapping.get(role, "user")

    def to_messages(self, budget: Optional[int] = None) -> List[Dict[str, str]]:
        """
        Export as a list of OpenAI / Anthropic-style messages.

        System items become {"role": "system", "content": "…"}.
        Everything else is merged into a single "user" message to avoid
        alternating-role constraints, unless you have explicit assistant turns.

        Args:
            budget: Optional token budget. When provided (or when a scoper is
                    configured), items are pruned to fit within the budget.
        """
        items = self._prepare_items(budget)
        messages: List[Dict[str, str]] = []
        user_parts: List[str] = []

        for item in items:
            content = self._get_rendered_content(item)
            if not content:
                continue
            llm_role = self._role_to_llm_role(item.role)
            if llm_role == "system":
                if user_parts:
                    messages.append({"role": "user", "content": "\n\n".join(user_parts)})
                    user_parts = []
                messages.append({"role": "system", "content": content})
            elif llm_role == "assistant":
                if user_parts:
                    messages.append({"role": "user", "content": "\n\n".join(user_parts)})
                    user_parts = []
                messages.append({"role": "assistant", "content": content})
            else:
                heading = f"### {item.label}\n" if item.label else ""
                user_parts.append(f"{heading}{content}")

        if user_parts:
            messages.append({"role": "user", "content": "\n\n".join(user_parts)})

        return messages

    def to_prompt(self, separator: str = "\n\n---\n\n", budget: Optional[int] = None,
                  agent_id: Optional[str] = None) -> str:
        """Export as a single concatenated prompt string.

        Args:
            budget: Optional token budget for scoping.
            agent_id: If provided, records delivery to the quality tracker.
        """
        items = self._prepare_items(budget)
        parts = []
        node_ids = []
        node_labels = []
        for item in items:
            content = self._get_rendered_content(item)
            if not content:
                continue
            heading = f"### {item.label}\n" if item.label else ""
            parts.append(f"{heading}{content}")
            if item.source == "graph":
                node_labels.append(item.label or "")
                # Extract node count from metadata
                count = item.metadata.get("count", 0) if item.metadata else 0
                for _ in range(min(count, 50)):
                    node_ids.append(item.label or "")

        result = separator.join(parts)

        # Record delivery for quality tracking
        if agent_id:
            try:
                from .quality_tracker import get_quality_tracker
                tracker = get_quality_tracker()
                token_est = max(1, len(result) // 4)
                tracker.record_delivery(
                    agent_id, node_ids=node_ids, token_count=token_est,
                    node_labels=list(set(node_labels)),
                    total_graph_nodes=self.max_tokens,
                )
            except Exception:
                pass

        return result

    def to_markdown(self) -> str:
        """Export as formatted Markdown (useful for display / debugging)."""
        sections = []
        for item in self._items:
            heading = item.label or item.role.value.title()
            meta = f"_role: {item.role.value}, source: {item.source or 'manual'}_"
            sections.append(f"## {heading}\n{meta}\n\n{item.content}")
        return "\n\n---\n\n".join(sections)

    def to_dict(self) -> Dict[str, Any]:
        """Export as a serialisable dict."""
        return {
            "items": [i.to_dict() for i in self._items],
            "estimated_tokens": self.estimate_tokens(),
            "max_tokens": self.max_tokens,
            "within_limit": self.is_within_limit(),
        }

    def export(self, fmt: Union[ContextFormat, str] = ContextFormat.MESSAGES) -> Any:
        """Generic export — choose format by enum or string name."""
        fmt = ContextFormat(fmt) if isinstance(fmt, str) else fmt
        if fmt == ContextFormat.MESSAGES:
            return self.to_messages()
        elif fmt == ContextFormat.PROMPT:
            return self.to_prompt()
        elif fmt == ContextFormat.MARKDOWN:
            return self.to_markdown()
        elif fmt == ContextFormat.DICT:
            return self.to_dict()
        raise ValueError(f"Unknown format: {fmt}")

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def save(self, path: str) -> None:
        """Persist the hub to a JSON file."""
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, indent=2, ensure_ascii=False)

    @classmethod
    def load(cls, path: str) -> "ContextHub":
        """Load a previously saved hub."""
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        hub = cls(max_tokens=data.get("max_tokens", 8000))
        for item_data in data.get("items", []):
            hub._items.append(ContextItem(
                content=item_data["content"],
                role=ContextRole(item_data.get("role", "user")),
                label=item_data.get("label"),
                source=item_data.get("source"),
                metadata=item_data.get("metadata", {}),
                created_at=item_data.get("created_at", ""),
                agent_id=item_data.get("agent_id"),
                provenance_id=item_data.get("provenance_id"),
                tags=item_data.get("tags", []),
                sensitivity=item_data.get("sensitivity", "public"),
                pii_detected=item_data.get("pii_detected", False),
                confidence=item_data.get("confidence", 1.0),
                state=item_data.get("state", "active"),
                _slot=item_data.get("_slot"),
            ))
        return hub

    @classmethod
    def from_session_graph(
        cls,
        db,
        system_prompt: Optional[str] = None,
        max_tokens: int = 8000,
        recent_turns: int = 20,
        agent_id: Optional[str] = None,
    ) -> "ContextHub":
        """
        Build a ContextHub from a unified session graph.

        Follows REFERENCES edges from recent Turn nodes to pull in
        contextually relevant knowledge nodes rather than blindly
        grabbing the most recent chunks.

        Slot allocation:
          - decisions + actions: 15%
          - recent turns: 40%
          - referenced knowledge: 35%
          - topics: 10%

        Args:
            db: AIContextDB instance for the session namespace.
            system_prompt: System prompt to pin at the top.
            max_tokens: Total token budget.
            recent_turns: Number of recent Turn nodes to include.
            agent_id: Optional agent ID for agent-scoped context.
        """
        from .session_graph import SessionGraphBuilder as _SGB

        # Delegate to SessionGraphBuilder's build_context logic
        # which uses the same ContextHub but with graph-aware assembly.
        # This classmethod is a convenience for direct use without a builder.
        hub = cls(system_prompt=system_prompt, max_tokens=max_tokens)

        decisions_budget = int(max_tokens * 0.15)
        turns_budget = int(max_tokens * 0.40)
        knowledge_budget = int(max_tokens * 0.35)
        topics_budget = int(max_tokens * 0.10)

        hub.define_slot("decisions", max_tokens=decisions_budget, pinned=False, priority=8)
        hub.define_slot("turns", max_tokens=turns_budget, pinned=False, priority=7)
        hub.define_slot("knowledge", max_tokens=knowledge_budget, pinned=False, priority=6)
        hub.define_slot("topics", max_tokens=topics_budget, pinned=False, priority=5)

        all_nodes = db.get_all_nodes()
        all_edges = db.get_all_edges()

        # Collect and sort Turn nodes
        turns = [n for n in all_nodes if n.label == "Turn"]
        turns.sort(key=lambda n: n.properties.get("turn_index", 0), reverse=True)
        recent = turns[:recent_turns]

        # Add turns in chronological order
        for turn_node in reversed(recent):
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
            hub.assign_to_slot(len(hub.items()) - 1, "turns")

        # Follow REFERENCES edges from recent turns to knowledge nodes
        recent_ids = {n.id for n in recent}
        ref_ids = set()
        for edge in all_edges:
            if edge.label == "REFERENCES" and edge.source in recent_ids:
                ref_ids.add(edge.target)

        for node in all_nodes:
            if node.id in ref_ids:
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
                hub.add_text(text, role=ContextRole.RETRIEVED, label=f"Referenced {node.label}",
                             source=f"knowledge:{node.id}")
                hub.assign_to_slot(len(hub.items()) - 1, "knowledge")

        # Decisions and Actions
        da_nodes = [
            n for n in all_nodes
            if n.label in ("Decision", "Action") and n.properties.get("status", "") != "done"
        ]
        da_nodes.sort(
            key=lambda n: n.properties.get("decided_at", n.properties.get("created_at", "")),
            reverse=True,
        )
        for node in da_nodes[:10]:
            props = node.properties
            if node.label == "Decision":
                text = f"[Decision] {props.get('summary', '')}"
            else:
                text = f"[Action] {props.get('description', '')} (status: {props.get('status', 'pending')})"
            hub.add_text(text, role=ContextRole.DECISION, label=node.label,
                         source=f"{node.label.lower()}:{node.id}")
            hub.assign_to_slot(len(hub.items()) - 1, "decisions")

        # Topics summary
        topics = [n for n in all_nodes if n.label == "Topic"]
        topics.sort(key=lambda n: n.properties.get("mention_count", 0), reverse=True)
        if topics:
            lines = [
                f"- {t.properties.get('name', '?')} (mentions: {t.properties.get('mention_count', 1)})"
                for t in topics[:15]
            ]
            hub.add_text("## Active Topics\n" + "\n".join(lines),
                         role=ContextRole.BACKGROUND, label="Topics", source="topics:summary")
            hub.assign_to_slot(len(hub.items()) - 1, "topics")

        return hub

    def __repr__(self) -> str:
        return (
            f"ContextHub(items={len(self._items)}, "
            f"~{self.estimate_tokens()} tokens / {self.max_tokens} limit)"
        )

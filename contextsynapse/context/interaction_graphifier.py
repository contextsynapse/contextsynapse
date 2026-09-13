"""
Interaction Graphifier
======================
Rule-based converter that turns conversation interactions into graph
nodes and edges within a session's unified graph.

No LLM required — uses pattern matching to extract signals (questions,
decisions, actions, topics) and links them to existing knowledge-layer
entities via cross-layer REFERENCES edges.

Designed to run async (fire-and-forget) after each response is sent,
so it never blocks response delivery.

Usage:
    graphifier = InteractionGraphifier(db)
    result = await graphifier.graphify(
        role="user",
        content="We decided to use PostgreSQL for the auth service",
        agent_id="agent-1",
    )
    # result.turn_node_id   -> the Turn node
    # result.signal_node_ids -> [Decision node for "use PostgreSQL..."]
    # result.reference_edge_ids -> [REFERENCES edge to "PostgreSQL" entity if it exists]
"""

from __future__ import annotations

import asyncio
import logging
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Significance filter constants
# ---------------------------------------------------------------------------

_ACKNOWLEDGMENTS = {
    "ok", "okay", "yes", "no", "sure", "thanks", "thank you", "got it",
    "understood", "roger", "ack", "yep", "nope", "yup", "k", "kk",
    "alright", "right", "cool", "fine", "done", "noted",
}

_MIN_SIGNIFICANT_LENGTH = 20

# ---------------------------------------------------------------------------
# Signal extraction patterns
# ---------------------------------------------------------------------------

_DECISION_PATTERNS = [
    re.compile(r"\b(?:decided to|let'?s go with|we'?ll use|going with|chose|chosen|picking)\b", re.I),
    re.compile(r"\b(?:the plan is to|we agreed|final decision|settled on)\b", re.I),
]

_ACTION_PATTERNS = [
    re.compile(r"\b(?:need to|TODO|will do|should|must|have to|going to|plan to)\b", re.I),
    re.compile(r"\b(?:action item|next step|assigned to|deadline|follow.up)\b", re.I),
]

# Sentence-ending question mark (not inside quotes)
_QUESTION_RE = re.compile(r'([^.!?\n]*\?)')

# Capitalized multi-word phrases (potential topics), 2-4 words
_TOPIC_RE = re.compile(r'\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,3})\b')


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------

@dataclass
class GraphifyResult:
    """Result of graphifying a single interaction."""
    turn_node_id: str
    signal_node_ids: List[str] = field(default_factory=list)
    reference_edge_ids: List[str] = field(default_factory=list)
    skipped: bool = False


# ---------------------------------------------------------------------------
# Main class
# ---------------------------------------------------------------------------

class InteractionGraphifier:
    """
    Rule-based converter: interaction -> graph nodes/edges.

    One instance per session. Uses an asyncio.Lock to serialize writes
    within the session, ensuring consistent NEXT_TURN chains even when
    multiple agents write concurrently.
    """

    def __init__(
        self,
        db,  # AIContextDB instance for the session namespace
        schema=None,  # Optional GraphSchema
        validator=None,  # Optional SchemaValidator
        concurrent_window_seconds: float = 5.0,
    ):
        self._db = db
        self._schema = schema
        self._validator = validator
        self._concurrent_window = concurrent_window_seconds

        # Per-agent tracking for NEXT_TURN chains
        self._agent_last_turn: Dict[str, str] = {}  # agent_id -> last Turn node ID
        self._sequence_counter: int = 0
        self._lock = asyncio.Lock()

        # Entity name cache (refreshed periodically)
        self._entity_cache: Dict[str, str] = {}  # entity_name -> node_id
        self._entity_cache_counter: int = 0
        self._ENTITY_CACHE_REFRESH_INTERVAL = 10  # refresh every N turns

        # AgentPresence cache
        self._agent_presence_cache: Dict[str, str] = {}  # agent_id -> node_id

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def graphify(
        self,
        role: str,
        content: str,
        agent_id: str,
        conversation_id: Optional[str] = None,
        timestamp: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> GraphifyResult:
        """
        Process one interaction turn into graph nodes and edges.

        Steps:
          1. Significance filter
          2. Create Turn node
          3. Link SPOKEN_BY -> AgentPresence
          4. Link NEXT_TURN from previous turn (per-agent chain)
          5. Extract signals: questions, decisions, actions, topics
          6. Create signal nodes + edges
          7. Cross-layer REFERENCES: scan content for existing entity names
          8. CONCURRENT_WITH: link to turns from other agents within time window
        """
        from ..core.graph_structures import GraphNode, GraphEdge

        ts = timestamp or datetime.now(timezone.utc).isoformat()

        # 1. Significance filter
        is_sig, significance = self._is_significant(content)
        if not is_sig:
            # Still create the Turn node (for completeness) but skip signals
            async with self._lock:
                self._sequence_counter += 1
                turn_id = self._create_turn_node(
                    role, content, agent_id, ts,
                    self._sequence_counter, conversation_id, significance,
                )
                agent_presence_id = await self._ensure_agent_presence(agent_id)
                self._add_edge("SPOKEN_BY", turn_id, agent_presence_id)
                self._link_next_turn(agent_id, turn_id)
                self._link_concurrent_turns(ts, agent_id, turn_id)
            return GraphifyResult(turn_node_id=turn_id, skipped=True)

        async with self._lock:
            self._sequence_counter += 1
            seq = self._sequence_counter

            # 2. Create Turn node
            turn_id = self._create_turn_node(
                role, content, agent_id, ts, seq, conversation_id, significance,
            )

            # 3. SPOKEN_BY -> AgentPresence
            agent_presence_id = await self._ensure_agent_presence(agent_id)
            self._add_edge("SPOKEN_BY", turn_id, agent_presence_id)

            # 4. NEXT_TURN chain (per-agent)
            self._link_next_turn(agent_id, turn_id)

            # 5-6. Extract signals and create nodes
            signal_ids = self._extract_and_create_signals(
                content, role, agent_id, ts, turn_id,
            )

            # 7. Cross-layer REFERENCES
            ref_edge_ids = self._create_entity_references(content, turn_id)

            # 8. CONCURRENT_WITH
            self._link_concurrent_turns(ts, agent_id, turn_id)

        return GraphifyResult(
            turn_node_id=turn_id,
            signal_node_ids=signal_ids,
            reference_edge_ids=ref_edge_ids,
        )

    # ------------------------------------------------------------------
    # Significance filter
    # ------------------------------------------------------------------

    def _is_significant(self, content: str) -> Tuple[bool, float]:
        """
        Determine if a message is significant enough for signal extraction.

        Returns (is_significant, score).
        All messages get a Turn node, but only significant ones get
        signal extraction (Questions, Decisions, Actions, Topics).
        """
        stripped = content.strip()

        # Very short messages
        if len(stripped) < _MIN_SIGNIFICANT_LENGTH:
            # Check if it's a pure acknowledgment
            if stripped.lower().rstrip(".,!") in _ACKNOWLEDGMENTS:
                return False, 0.1
            # Short but not an ack — could still be meaningful
            return False, 0.2

        # Pure acknowledgments (even if padded)
        words = stripped.lower().split()
        if len(words) <= 3 and all(w.rstrip(".,!") in _ACKNOWLEDGMENTS for w in words):
            return False, 0.1

        # Has signals → high significance
        score = 0.5
        if "?" in content:
            score += 0.2
        if any(p.search(content) for p in _DECISION_PATTERNS):
            score += 0.2
        if any(p.search(content) for p in _ACTION_PATTERNS):
            score += 0.1

        return True, min(score, 1.0)

    # ------------------------------------------------------------------
    # Signal extraction
    # ------------------------------------------------------------------

    def _extract_signals(self, content: str, role: str) -> Dict[str, List[str]]:
        """
        Rule-based signal extraction from message content.

        Returns dict with keys: questions, decisions, actions, topics.
        Each value is a list of extracted text snippets.
        """
        signals: Dict[str, List[str]] = {
            "questions": [],
            "decisions": [],
            "actions": [],
            "topics": [],
        }

        # Questions: sentences ending with ?
        for match in _QUESTION_RE.finditer(content):
            q = match.group(1).strip()
            if len(q) > 5:  # skip tiny fragments like "?"
                signals["questions"].append(q)

        # Decisions: sentences containing decision patterns
        sentences = re.split(r'[.!?\n]+', content)
        for sentence in sentences:
            sentence = sentence.strip()
            if not sentence:
                continue
            if any(p.search(sentence) for p in _DECISION_PATTERNS):
                signals["decisions"].append(sentence)
            elif any(p.search(sentence) for p in _ACTION_PATTERNS):
                signals["actions"].append(sentence)

        # Topics: capitalized multi-word phrases
        seen_topics: Set[str] = set()
        for match in _TOPIC_RE.finditer(content):
            topic = match.group(1)
            topic_lower = topic.lower()
            if topic_lower not in seen_topics and len(topic) > 3:
                seen_topics.add(topic_lower)
                signals["topics"].append(topic)

        return signals

    # ------------------------------------------------------------------
    # Graph node/edge creation helpers
    # ------------------------------------------------------------------

    def _create_turn_node(
        self,
        role: str,
        content: str,
        agent_id: str,
        timestamp: str,
        turn_index: int,
        conversation_id: Optional[str],
        significance: float,
    ) -> str:
        """Create a Turn node and return its ID."""
        from ..core.graph_structures import GraphNode

        node_id = str(uuid.uuid4())
        props = {
            "role": role,
            "content": content,
            "agent_id": agent_id,
            "timestamp": timestamp,
            "turn_index": turn_index,
            "significance": significance,
        }
        if conversation_id:
            props["conversation_id"] = conversation_id

        self._db.add_node(GraphNode(
            id=node_id,
            label="Turn",
            properties=props,
        ))
        return node_id

    def _add_edge(self, label: str, source_id: str, target_id: str, props: Dict = None) -> str:
        """Create an edge and return its ID."""
        from ..core.graph_structures import GraphEdge

        edge_id = str(uuid.uuid4())
        self._db.add_edge(GraphEdge(
            id=edge_id,
            source=source_id,
            target=target_id,
            label=label,
            properties=props or {},
        ))
        return edge_id

    async def _ensure_agent_presence(self, agent_id: str) -> str:
        """Get or create an AgentPresence node for this agent."""
        from ..core.graph_structures import GraphNode

        if agent_id in self._agent_presence_cache:
            return self._agent_presence_cache[agent_id]

        # Use label index instead of full scan — O(1) vs O(N)
        if hasattr(self._db, 'csr_storage') and self._db.csr_storage:
            for nid in self._db.csr_storage.node_types.get("AgentPresence", set()):
                n = self._db.get_node(nid)
                if n and (n.properties or {}).get("agent_id") == agent_id:
                    self._agent_presence_cache[agent_id] = nid
                    return nid
        else:
            for node in self._db.get_all_nodes():
                if node.label == "AgentPresence" and node.properties.get("agent_id") == agent_id:
                    self._agent_presence_cache[agent_id] = node.id
                    return node.id

        # Create new
        node_id = str(uuid.uuid4())
        self._db.add_node(GraphNode(
            id=node_id,
            label="AgentPresence",
            properties={
                "agent_id": agent_id,
                "agent_name": agent_id,  # Can be enriched later
                "joined_at": datetime.now(timezone.utc).isoformat(),
                "platform": "unknown",
            },
        ))
        self._agent_presence_cache[agent_id] = node_id
        return node_id

    def _link_next_turn(self, agent_id: str, turn_id: str):
        """Link this turn to the previous turn from the same agent."""
        prev_id = self._agent_last_turn.get(agent_id)
        if prev_id:
            self._add_edge("NEXT_TURN", prev_id, turn_id)
        self._agent_last_turn[agent_id] = turn_id

    def _link_concurrent_turns(self, timestamp: str, agent_id: str, turn_id: str):
        """
        Link to turns from other agents within the concurrent time window.
        """
        try:
            turn_time = datetime.fromisoformat(timestamp)
        except (ValueError, TypeError):
            return

        for other_agent, other_turn_id in self._agent_last_turn.items():
            if other_agent == agent_id:
                continue
            if other_turn_id == turn_id:
                continue
            # Check the other turn's timestamp
            other_node = self._db.get_node(other_turn_id)
            if not other_node:
                continue
            other_ts = other_node.properties.get("timestamp", "")
            try:
                other_time = datetime.fromisoformat(other_ts)
                diff = abs((turn_time - other_time).total_seconds())
                if diff <= self._concurrent_window:
                    self._add_edge("CONCURRENT_WITH", turn_id, other_turn_id)
            except (ValueError, TypeError):
                continue

    def _extract_and_create_signals(
        self,
        content: str,
        role: str,
        agent_id: str,
        timestamp: str,
        turn_id: str,
    ) -> List[str]:
        """Extract signals from content and create corresponding graph nodes."""
        from ..core.graph_structures import GraphNode

        signals = self._extract_signals(content, role)
        created_ids: List[str] = []

        # Questions
        for q_text in signals["questions"]:
            q_id = str(uuid.uuid4())
            self._db.add_node(GraphNode(
                id=q_id,
                label="Question",
                properties={
                    "text": q_text,
                    "asked_at": timestamp,
                    "agent_id": agent_id,
                    "status": "open",
                },
            ))
            self._add_edge("RAISES", turn_id, q_id)
            created_ids.append(q_id)

        # Decisions
        for d_text in signals["decisions"]:
            d_id = str(uuid.uuid4())
            self._db.add_node(GraphNode(
                id=d_id,
                label="Decision",
                properties={
                    "summary": d_text,
                    "decided_at": timestamp,
                    "agent_id": agent_id,
                    "confidence": 0.8,
                },
            ))
            self._add_edge("DECIDES", turn_id, d_id)
            created_ids.append(d_id)

        # Actions
        for a_text in signals["actions"]:
            a_id = str(uuid.uuid4())
            self._db.add_node(GraphNode(
                id=a_id,
                label="Action",
                properties={
                    "description": a_text,
                    "assigned_to": agent_id,
                    "created_at": timestamp,
                    "status": "pending",
                },
            ))
            self._add_edge("ASSIGNS", turn_id, a_id)
            created_ids.append(a_id)

        # Topics — deduplicate against existing topics in the graph
        for t_name in signals["topics"]:
            existing_topic_id = self._find_existing_topic(t_name)
            if existing_topic_id:
                # Increment mention count
                node = self._db.get_node(existing_topic_id)
                if node:
                    node.properties["mention_count"] = node.properties.get("mention_count", 1) + 1
                self._add_edge("RAISES", turn_id, existing_topic_id)
            else:
                t_id = str(uuid.uuid4())
                self._db.add_node(GraphNode(
                    id=t_id,
                    label="Topic",
                    properties={
                        "name": t_name,
                        "first_mentioned_at": timestamp,
                        "mention_count": 1,
                    },
                ))
                self._add_edge("RAISES", turn_id, t_id)
                created_ids.append(t_id)

        return created_ids

    def _find_existing_topic(self, name: str) -> Optional[str]:
        """Find an existing Topic node with the same name (case-insensitive).
        Uses label index for O(T) where T = topic count, not O(N) full scan.
        """
        name_lower = name.lower()
        if hasattr(self._db, 'csr_storage') and self._db.csr_storage:
            for nid in self._db.csr_storage.node_types.get("Topic", set()):
                n = self._db.get_node(nid)
                if n and (n.properties or {}).get("name", "").lower() == name_lower:
                    return nid
        else:
            for node in self._db.get_all_nodes():
                if node.label == "Topic" and node.properties.get("name", "").lower() == name_lower:
                    return node.id
        return None

    # ------------------------------------------------------------------
    # Cross-layer entity references
    # ------------------------------------------------------------------

    def _refresh_entity_cache(self):
        """Refresh entity name cache using label index — O(E) not O(N)."""
        self._entity_cache.clear()
        if hasattr(self._db, 'csr_storage') and self._db.csr_storage:
            for nid in self._db.csr_storage.node_types.get("Entity", set()):
                n = self._db.get_node(nid)
                if n:
                    name = (n.properties or {}).get("entity_name", "") or (n.properties or {}).get("name", "")
                    if name and len(name) > 3:
                        self._entity_cache[name] = nid
        else:
            for node in self._db.get_all_nodes():
                if node.label == "Entity":
                    name = node.properties.get("entity_name", "") or node.properties.get("name", "")
                    if name and len(name) > 3:
                        self._entity_cache[name] = node.id
        self._entity_cache_counter = 0

    def _create_entity_references(self, content: str, turn_id: str) -> List[str]:
        """
        Scan content for existing entity names and create REFERENCES edges.

        Rules:
          - Exact case-sensitive match
          - Entity name must be > 3 characters
          - Word-boundary matching to avoid false positives
        """
        # Periodically refresh the cache
        self._entity_cache_counter += 1
        if self._entity_cache_counter >= self._ENTITY_CACHE_REFRESH_INTERVAL or not self._entity_cache:
            self._refresh_entity_cache()

        edge_ids: List[str] = []
        matched_entities: Set[str] = set()  # avoid duplicate edges to same entity

        for entity_name, entity_id in self._entity_cache.items():
            if entity_id in matched_entities:
                continue
            # Word-boundary match, case-sensitive
            try:
                pattern = re.compile(r'\b' + re.escape(entity_name) + r'\b')
                if pattern.search(content):
                    eid = self._add_edge("REFERENCES", turn_id, entity_id, {
                        "match_type": "entity_name",
                        "matched_text": entity_name,
                    })
                    edge_ids.append(eid)
                    matched_entities.add(entity_id)
            except re.error:
                continue

        return edge_ids

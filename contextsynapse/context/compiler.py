"""Signal Compiler — universal signal router with cross-layer propagation.

Routes every incoming signal to ALL affected contexts:
  1. Direct match via entity KB (fast, microseconds)
  2. Cross-layer propagation via dependency edges (fast, graph traversal)
  3. Extraction pattern matching from all layers (regex, fast)

No LLM calls. Pure pattern matching + graph traversal.

Performance target: 10,000 articles/hour on a single core.
"""

from __future__ import annotations

import json
import logging
import re
import time
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Result data classes
# ---------------------------------------------------------------------------


@dataclass
class SignalMatch:
    """A single entity match for an incoming signal."""

    entity: str               # graph namespace
    score: float              # 0-1 relevance
    match_type: str           # "direct", "person", "brand", "keyword", "propagated"
    matched_terms: List[str]  # which terms from the text matched
    layer: str = ""           # which layer's knowledge matched
    propagation_path: List[str] = field(default_factory=list)  # if propagated

    def to_dict(self) -> Dict[str, Any]:
        result = {
            "entity": self.entity,
            "score": self.score,
            "match_type": self.match_type,
            "matched_terms": self.matched_terms,
        }
        if self.layer:
            result["layer"] = self.layer
        if self.propagation_path:
            result["propagation_path"] = self.propagation_path
        return result


# ---------------------------------------------------------------------------
# Signal Compiler
# ---------------------------------------------------------------------------


class SignalCompiler:
    """Universal signal router with cross-layer propagation.

    Builds an in-memory reverse index from all entity KBs and all
    context layers, then routes incoming text to matching entity
    contexts using pure string matching + optional graph propagation.
    Zero LLM cost.

    The compiler is domain-agnostic — it reads entity nodes from graphs
    and extraction rules from context layers.

    Usage::

        from contextsynapse.context.layers import ContextLayer
        from contextsynapse.context.propagation import SignalPropagationEngine

        layers = [ContextLayer.from_yaml("layers/finance.yaml"), ...]
        propagation = SignalPropagationEngine(registry)
        propagation.build_dependency_graph()

        compiler = SignalCompiler(
            registry=registry,
            layers=layers,
            propagation_engine=propagation,
        )
        matches = compiler.route("Reliance Jio launches new 5G plan")
        # [SignalMatch(entity="reliance_industries", score=0.9, ...)]

    Performance:
        Typical routing: < 1ms per article on a single core.
        Index build: O(entities x aliases), typically < 100ms for 1000 entities.
    """

    # Default threshold for route_and_ingest
    INGEST_THRESHOLD = 0.3

    # Score weights by match type
    SCORE_WEIGHTS = {
        "company": 1.0,
        "ticker": 0.95,
        "person": 0.9,
        "brand": 0.85,
        "alias": 0.8,
        "subsidiary": 0.8,
        "dependency": 0.6,
        "keyword": 0.5,
        "propagated": 0.4,
    }

    def __init__(
        self,
        registry,
        layers: Optional[List] = None,
        propagation_engine=None,
    ):
        """
        Args:
            registry: GraphRegistry for accessing entity graphs.
            layers: Optional list of ContextLayer instances providing
                    extraction rules and entity knowledge.
            propagation_engine: Optional SignalPropagationEngine for
                                cross-layer signal propagation.
        """
        self.registry = registry
        self.layers = layers or []
        self.propagation_engine = propagation_engine

        # Reverse index: normalized_term -> [(entity_namespace, match_type, base_score, layer_id)]
        self._index: Dict[str, List[Tuple[str, str, float, str]]] = {}
        # Metadata
        self._entity_count: int = 0
        self._term_count: int = 0
        self._last_refresh: Optional[str] = None

        # Build on init
        self.build_index()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def route(
        self,
        text: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> List[SignalMatch]:
        """Route text to matching entity contexts.

        Step 1: Direct entity KB matching (aliases, people, brands)
        Step 2: For each direct match, optionally propagate to others
        Step 3: Apply extraction patterns from all layers to classify
        Step 4: Return all matches sorted by score

        Args:
            text: The text to route (article body, headline, event).
            metadata: Optional metadata dict (available for future scoring).

        Returns:
            List of SignalMatch sorted by score descending.
        """
        if not text or not self._index:
            return []

        lower_text = text.lower()

        # Step 1: Direct matching against the reverse index
        # Accumulate: entity -> {score, match_type, terms, layer}
        entity_hits: Dict[str, Dict[str, Any]] = {}

        for term, entries in self._index.items():
            if term in lower_text:
                for entity_ns, match_type, base_score, layer_id in entries:
                    if entity_ns not in entity_hits:
                        entity_hits[entity_ns] = {
                            "score": 0.0,
                            "match_type": match_type,
                            "matched_terms": [],
                            "layer": layer_id,
                        }
                    hit = entity_hits[entity_ns]
                    # Use max score (don't sum to avoid >1.0)
                    if base_score > hit["score"]:
                        hit["score"] = base_score
                        hit["match_type"] = match_type
                        hit["layer"] = layer_id
                    hit["matched_terms"].append(term)

        # Build direct results
        results: List[SignalMatch] = []
        for entity_ns, hit in entity_hits.items():
            results.append(SignalMatch(
                entity=entity_ns,
                score=min(hit["score"], 1.0),
                match_type=hit["match_type"],
                matched_terms=hit["matched_terms"],
                layer=hit.get("layer", ""),
            ))

        # Step 2: Cross-layer propagation for top matches
        if self.propagation_engine and results:
            propagated = self._propagate_matches(results, text)
            # Add propagated matches that aren't already in results
            existing_entities = {r.entity for r in results}
            for pm in propagated:
                if pm.entity not in existing_entities:
                    results.append(pm)
                    existing_entities.add(pm.entity)

        # Sort by score
        results.sort(key=lambda m: m.score, reverse=True)
        return results

    def route_and_ingest(
        self,
        text: str,
        metadata: Optional[Dict[str, Any]] = None,
        source_type: str = "article",
        threshold: Optional[float] = None,
    ) -> List[str]:
        """Route text and ingest into matching entity graphs.

        Calls route(), then for each match above threshold, creates a
        node in that entity's graph.  Uses layer extraction rules to
        classify the node type.

        Args:
            text: Text to route and ingest.
            metadata: Optional metadata to attach to created nodes.
            source_type: Default source type label.
            threshold: Minimum score for ingestion (default INGEST_THRESHOLD).

        Returns:
            List of entity namespaces that received the signal.
        """
        from contextsynapse.core.graph_structures import GraphNode

        if threshold is None:
            threshold = self.INGEST_THRESHOLD

        matches = self.route(text, metadata=metadata)
        if not matches:
            return []

        ingested_to = []
        now = datetime.now(timezone.utc).isoformat()

        # Classify the signal using extraction rules from all layers
        classification = self.classify_signal(text)
        node_type = classification.get("node_type", source_type.title())
        sentiment = classification.get("sentiment", "")

        for match in matches:
            if match.score < threshold:
                continue

            graph = self.registry.get_graph(match.entity, load_if_missing=True)
            if graph is None:
                continue

            # Create a signal node in the entity's graph
            node_id = f"signal_{uuid.uuid4().hex[:12]}"
            props = {
                "text": text[:2000],
                "source_type": source_type,
                "score": match.score,
                "match_type": match.match_type,
                "matched_terms": match.matched_terms,
                "ingested_at": now,
            }
            if match.layer:
                props["source_layer"] = match.layer
            if sentiment:
                props["sentiment"] = sentiment
            if classification.get("layer"):
                props["classified_by_layer"] = classification["layer"]
            if match.propagation_path:
                props["propagation_path"] = match.propagation_path
            if metadata:
                for k, v in metadata.items():
                    if k not in props:
                        props[k] = v

            graph.add_node(GraphNode(
                id=node_id,
                label=node_type,
                properties=props,
            ))

            ingested_to.append(match.entity)

        if ingested_to:
            logger.debug(
                "Signal ingested to %d entities: %s",
                len(ingested_to),
                ", ".join(ingested_to[:5]),
            )

        return ingested_to

    def classify_signal(self, text: str) -> Dict[str, Any]:
        """Classify a signal using extraction patterns from all layers.

        Runs all extraction rules from all registered context layers
        against the text and returns the best matching classification.

        Returns:
            Dict with {node_type, layer, pattern_name, sentiment}.
            Empty values if no pattern matches.
        """
        best: Optional[Dict[str, Any]] = None

        for layer in self.layers:
            signals = layer.extract_signals(text)
            for sig in signals:
                # First match wins (could be enhanced with scoring)
                if best is None:
                    best = {
                        "node_type": sig.get("node_type", ""),
                        "layer": sig.get("layer", layer.layer_id),
                        "pattern_name": sig.get("name", ""),
                        "sentiment": sig.get("sentiment_hint", ""),
                    }
                    break
            if best is not None:
                break

        return best or {"node_type": "", "layer": "", "pattern_name": "", "sentiment": ""}

    def build_index(self) -> None:
        """Build reverse index from ALL entity KBs and context layers.

        Scans:
          1. All graph namespaces for Entity, AliasSet, Person, Brand,
             and dependency nodes.
          2. All context layers' known_entities for additional aliases
             and entity knowledge.
        """
        t0 = time.monotonic()
        index: Dict[str, List[Tuple[str, str, float, str]]] = defaultdict(list)
        entity_count = 0

        # --- Index from graph data ---
        graphs = self.registry.list_graphs()
        for graph_info in graphs:
            namespace = graph_info.get("name", "")
            if not namespace or namespace.startswith("_"):
                continue

            graph = self.registry.get_graph(namespace, load_if_missing=False)
            if graph is None:
                continue

            aliases_added = self._index_graph(graph, namespace, index)
            if aliases_added > 0:
                entity_count += 1

        # --- Index from context layers ---
        for layer in self.layers:
            for entity_key, entity_data in layer.known_entities.items():
                # Determine namespace for this entity
                namespace = _slugify(entity_key)
                added = 0

                # Index entity name
                added += self._add_term(index, entity_key, namespace, "company", layer.layer_id)

                # Index aliases
                aliases = entity_data.get("aliases", [])
                if isinstance(aliases, str):
                    aliases = [aliases]
                for alias in aliases:
                    if alias:
                        added += self._add_term(index, str(alias), namespace, "alias", layer.layer_id)

                # Index people
                for person in entity_data.get("people", []):
                    name = person if isinstance(person, str) else person.get("name", "")
                    if name:
                        added += self._add_term(index, name, namespace, "person", layer.layer_id)

                # Index brands
                for brand in entity_data.get("brands", []):
                    name = brand if isinstance(brand, str) else brand.get("name", "")
                    if name:
                        added += self._add_term(index, name, namespace, "brand", layer.layer_id)

                if added > 0:
                    entity_count += 1

        self._index = dict(index)
        self._entity_count = entity_count
        self._term_count = len(self._index)
        self._last_refresh = datetime.now(timezone.utc).isoformat()

        elapsed = (time.monotonic() - t0) * 1000
        logger.info(
            "Signal compiler index built: %d terms across %d entities in %.1fms "
            "(layers: %d)",
            self._term_count,
            self._entity_count,
            elapsed,
            len(self.layers),
        )

    def refresh_index(self) -> None:
        """Rebuild the reverse index from current graph state + layers.

        Call after new entity KBs are populated (e.g. after seed.apply()).
        """
        self.build_index()

    def get_stats(self) -> Dict[str, Any]:
        """Return index statistics."""
        avg_aliases = (
            self._term_count / self._entity_count
            if self._entity_count > 0
            else 0.0
        )
        return {
            "total_terms": self._term_count,
            "total_entities": self._entity_count,
            "avg_aliases_per_entity": round(avg_aliases, 1),
            "last_refresh": self._last_refresh,
            "layer_count": len(self.layers),
            "layer_ids": [l.layer_id for l in self.layers],
            "has_propagation_engine": self.propagation_engine is not None,
        }

    # ------------------------------------------------------------------
    # Propagation
    # ------------------------------------------------------------------

    def _propagate_matches(
        self,
        direct_matches: List[SignalMatch],
        text: str,
    ) -> List[SignalMatch]:
        """For top direct matches, propagate signal through the dependency graph.

        Only propagates from the highest-scoring direct match to avoid
        an explosion of results.
        """
        if not self.propagation_engine:
            return []

        propagated_results: List[SignalMatch] = []
        # Propagate from top match only
        top = direct_matches[0]

        try:
            signals = self.propagation_engine.propagate(
                source_entity=top.entity,
                signal_value=top.score,
                signal_description=text[:200],
                max_hops=2,
                min_strength=0.15,
            )
        except Exception as exc:
            logger.debug("Propagation failed for %s: %s", top.entity, exc)
            return []

        for sig in signals:
            entity_slug = _slugify(sig.entity)
            propagated_results.append(SignalMatch(
                entity=entity_slug,
                score=min(sig.signal_strength * self.SCORE_WEIGHTS["propagated"] / 0.4, 1.0),
                match_type="propagated",
                matched_terms=[top.entity],
                layer="",
                propagation_path=sig.path,
            ))

        return propagated_results

    # ------------------------------------------------------------------
    # Index building
    # ------------------------------------------------------------------

    def _index_graph(
        self,
        graph,
        namespace: str,
        index: Dict[str, List[Tuple[str, str, float, str]]],
    ) -> int:
        """Index all relevant nodes from a single graph.

        Returns the number of terms added.
        """
        terms_added = 0

        # Index Entity root nodes
        try:
            for node in graph.get_all_nodes(label="Entity"):
                name = node.properties.get("name", "") if node.properties else ""
                if name:
                    terms_added += self._add_term(index, name, namespace, "company")
        except Exception:
            pass

        # Index AliasSet nodes
        try:
            for node in graph.get_all_nodes(label="AliasSet"):
                props = node.properties or {}
                aliases = props.get("aliases", [])
                if isinstance(aliases, str):
                    try:
                        aliases = json.loads(aliases)
                    except (json.JSONDecodeError, TypeError):
                        aliases = [aliases]
                for alias in aliases:
                    if alias:
                        terms_added += self._add_term(index, str(alias), namespace, "alias")
        except Exception:
            pass

        # Index Person nodes
        try:
            for node in graph.get_all_nodes(label="Person"):
                name = node.properties.get("name", "") if node.properties else ""
                if name:
                    terms_added += self._add_term(index, name, namespace, "person")
        except Exception:
            pass

        # Index Brand nodes
        try:
            for node in graph.get_all_nodes(label="Brand"):
                name = node.properties.get("name", "") if node.properties else ""
                if name:
                    terms_added += self._add_term(index, name, namespace, "brand")
        except Exception:
            pass

        # Index dependency nodes
        dep_labels = {
            "SupplyChainInput": "dependency",
            "Regulator": "dependency",
            "Competitor": "dependency",
            "MacroDependency": "dependency",
            "SharedEntity": "dependency",
        }
        for label, match_type in dep_labels.items():
            try:
                for node in graph.get_all_nodes(label=label):
                    name = node.properties.get("name", "") if node.properties else ""
                    if name:
                        terms_added += self._add_term(index, name, namespace, match_type)
            except Exception:
                pass

        return terms_added

    def _add_term(
        self,
        index: Dict[str, List[Tuple[str, str, float, str]]],
        term: str,
        namespace: str,
        match_type: str,
        layer_id: str = "",
    ) -> int:
        """Add a term to the reverse index.

        Normalizes the term to lowercase and adds it with its score weight.
        Also adds individual words for multi-word terms (at lower score).

        Returns number of index entries added.
        """
        normalized = term.lower().strip()
        if not normalized or len(normalized) < 2:
            return 0

        base_score = self.SCORE_WEIGHTS.get(match_type, 0.5)
        added = 0

        # Add the full term
        index[normalized].append((namespace, match_type, base_score, layer_id))
        added += 1

        # For multi-word terms, add individual words at lower score
        words = normalized.split()
        if len(words) > 1:
            for word in words:
                word = word.strip()
                if len(word) > 3:
                    word_score = base_score * 0.6
                    index[word].append((namespace, "keyword", word_score, layer_id))
                    added += 1

        return added


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _slugify(text: str) -> str:
    """Convert text to a URL/ID-safe slug."""
    slug = text.lower().strip()
    slug = re.sub(r"[^a-z0-9]+", "_", slug)
    slug = slug.strip("_")
    return slug or "entity"

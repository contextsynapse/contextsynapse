"""ProjectContext — thin namespace wrapper for SDLC context namespaces.

A project is a single AIContextDB namespace with SDLC node types applied.
This class measures coverage and surfaces stale nodes. It does not track
tasks, enforce DoD, or generate work.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from ..core.hybrid_graph_storage import AIContextDB
from ..core.graph_structures import GraphNode
from .sdlc_schema import (
    SDLC_LAYERS,
    layer_for_node_type,
    is_sdlc_node_type,
)

# A layer is "fully covered" once it has this many nodes.
_FULL_AT = 5


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


_PROP_MAX_LEN = 500


def _truncate_props(props: dict, max_len: int = _PROP_MAX_LEN) -> dict:
    """Return a copy of props with long string values truncated."""
    return {
        k: (v[:max_len] + "…" if isinstance(v, str) and len(v) > max_len else v)
        for k, v in props.items()
    }


class ProjectContext:
    """Namespace wrapper for an SDLC project context.

    Create with ``ProjectContext.create(name)``.
    Load existing with ``ProjectContext.load(name)``.
    """

    def __init__(self, db: AIContextDB) -> None:
        self.db = db
        self.name: str = db.name

    # ── Factory ──────────────────────────────────────────────────────────────

    @classmethod
    def create(cls, name: str, base_path: str = "contextcore_data") -> "ProjectContext":
        """Create (or open) a project namespace and ensure a root Project node exists."""
        db = cls._get_or_create_db(name, base_path)
        pc = cls(db)
        root_id = f"project:{name}"
        if db.get_node(root_id) is None:
            db.add_node(GraphNode(
                id=root_id,
                label="Project",
                properties={"name": name, "version": 1, "_created_at": _now()},
            ))
        # Register in the projects index so listing works regardless of storage backend
        cls._register(name, base_path)
        return pc

    @staticmethod
    def _get_or_create_db(name: str, base_path: str) -> "AIContextDB":
        """Return the cached AIContextDB for this name, or create and cache a new one.

        Using a process-level cache means multiple calls to create() / load() with
        the same name return the SAME underlying storage object.  This is essential
        when the backend is in-memory CSR (no Redis): writes made through one
        ProjectContext are immediately visible through another.  When Redis is used
        both instances share Redis anyway, but the cache is still harmless.
        """
        from ..core.registry import graph_registry
        if name in graph_registry.graphs:
            return graph_registry.graphs[name]
        db = AIContextDB(name=name, config={"base_path": base_path})
        graph_registry.graphs[name] = db
        return db

    @staticmethod
    def _register(name: str, base_path: str) -> None:
        """Add name to the projects index at {base_path}/_projects.json (atomic write)."""
        import json
        import os
        import tempfile
        from pathlib import Path
        index_path = Path(base_path) / "_projects.json"
        index_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            names = json.loads(index_path.read_text()) if index_path.exists() else []
        except Exception:
            names = []
        if name not in names:
            names.append(name)
            tmp_fd, tmp_path = tempfile.mkstemp(dir=str(index_path.parent), suffix=".tmp")
            try:
                with os.fdopen(tmp_fd, "w") as f:
                    f.write(json.dumps(names))
                os.replace(tmp_path, str(index_path))
            except Exception:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
                raise

    @staticmethod
    def list_names(base_path: str = "contextcore_data") -> list:
        """Return all registered project names."""
        import json
        from pathlib import Path
        index_path = Path(base_path) / "_projects.json"
        try:
            return json.loads(index_path.read_text()) if index_path.exists() else []
        except Exception:
            return []

    @classmethod
    def load(cls, name: str, base_path: str = "contextcore_data") -> "ProjectContext":
        """Load an existing project namespace. Raises KeyError if it does not exist."""
        if name not in cls.list_names(base_path=base_path):
            raise KeyError(f"Project '{name}' not found. Use create_project to create it.")
        db = cls._get_or_create_db(name, base_path)
        return cls(db)

    # ── Coverage ─────────────────────────────────────────────────────────────

    def _get_layers(self) -> Dict[str, dict]:
        """Return layers from schema if bound, else SDLC_LAYERS default."""
        try:
            from .schema_manager import get_schema_manager
            schema_layers = get_schema_manager().get_layers(self.name)
            if schema_layers is not None:
                return schema_layers
        except Exception:
            pass
        return SDLC_LAYERS

    def _layer_for(self, node_label: str) -> Optional[str]:
        """Return layer name for a node label, checking schema first."""
        try:
            from .schema_manager import get_schema_manager
            schema = get_schema_manager().for_namespace(self.name)
            if schema is not None:
                return schema.layer_for(node_label)
        except Exception:
            pass
        return layer_for_node_type(node_label)

    def coverage_score(self) -> Dict[str, Any]:
        """Return per-layer coverage scores and overall score.

        Coverage per layer = min(node_count / _FULL_AT, 1.0).
        The evolution layer returns None when no evolution nodes exist (greenfield).

        Returns:
            {intent, design, build, verify, evolution (float or None),
             overall (float), project_type ("greenfield" or "brownfield")}
        """
        layers = self._get_layers()
        all_nodes = self.db.get_all_nodes()

        counts: Dict[str, int] = {layer: 0 for layer in layers}
        for node in all_nodes:
            layer = self._layer_for(node.label)
            if layer and layer in counts:
                counts[layer] += 1

        has_evolution = counts.get("evolution", 0) > 0
        project_type = "brownfield" if has_evolution else "greenfield"

        def _score(count: int) -> float:
            return min(count / _FULL_AT, 1.0)

        # Score all non-optional layers
        required_layers = [l for l, d in layers.items()
                          if not d.get("optional", False) and l != "team"]
        scores: Dict[str, Any] = {
            layer: _score(counts.get(layer, 0))
            for layer in required_layers
        }
        # Optional layers: None if empty
        for layer, layer_def in layers.items():
            if layer_def.get("optional", False) and layer not in scores:
                c = counts.get(layer, 0)
                scores[layer] = _score(c) if c > 0 else None

        weighted = sum(
            scores[layer] * layers[layer].get("weight", 0.0)
            for layer in required_layers
            if isinstance(scores.get(layer), (int, float))
        )
        # Add optional layers with values
        for layer, layer_def in layers.items():
            if layer_def.get("optional", False) and scores.get(layer) is not None:
                weighted += scores[layer] * layer_def.get("weight", 0.0)

        scores["overall"] = round(weighted, 4)
        scores["project_type"] = project_type
        return scores

    # ── Stale report ─────────────────────────────────────────────────────────

    def stale_report(self) -> List[Dict[str, Any]]:
        """Return all stale SDLC nodes in this namespace.

        Returns:
            List of {node_id, label, stale_reason, stale_since, linked_to}
        """
        results = []
        for node in self.db.get_all_nodes():
            if not is_sdlc_node_type(node.label):
                continue
            if not node.properties.get("_stale"):
                continue
            results.append({
                "node_id": node.id,
                "label": node.label,
                "stale_reason": node.properties.get("_stale_reason", ""),
                "stale_since": node.properties.get("_stale_since", ""),
                "linked_to": node.properties.get("_stale_source", ""),
            })
        return results

    # ── Agent brief ──────────────────────────────────────────────────────────

    def agent_brief(self, topic: str = "", phase: str = "") -> Dict[str, Any]:
        """Assemble a scoped context package for an agent about to work.

        Args:
            topic: keyword filter — case-insensitive substring match across all
                   string property values
            phase: SDLC layer name to restrict to (intent/design/build/verify/
                   evolution) — empty means all layers

        Returns:
            {topic, phase, nodes: [{node_id, label, properties}], stale_count}
        """
        all_nodes = self.db.get_all_nodes()
        sdlc_nodes = [n for n in all_nodes if is_sdlc_node_type(n.label)]

        if phase:
            phase_norm = phase.lower()
            layer_types = set(SDLC_LAYERS.get(phase_norm, {}).get("node_types", []))
            sdlc_nodes = [n for n in sdlc_nodes if n.label in layer_types]

        if topic:
            topic_lower = topic.lower()
            sdlc_nodes = [
                n for n in sdlc_nodes
                if topic_lower in " ".join(
                    str(v) for v in n.properties.values() if isinstance(v, str)
                ).lower()
            ]

        stale_count = sum(
            1 for n in all_nodes
            if n.properties.get("_stale") and is_sdlc_node_type(n.label)
        )

        # Build node list with feedback flags
        node_list = []
        flagged_count = 0
        for n in sdlc_nodes:
            entry = {"node_id": n.id, "label": n.label, "properties": _truncate_props(n.properties)}
            # Add feedback warnings from rate_context signals
            usefulness = n.properties.get("_usefulness_score")
            last_signal = n.properties.get("_usefulness_last_signal", "")
            if usefulness is not None:
                try:
                    score = float(usefulness)
                    if score < 0.3 or last_signal in ("outdated", "misleading"):
                        entry["_warning"] = f"⚠️ {last_signal or 'low-quality'}"
                        if n.properties.get("_usefulness_comment"):
                            entry["_warning"] += f" — {n.properties['_usefulness_comment']}"
                        flagged_count += 1
                except (TypeError, ValueError):
                    pass
            # Upstream warnings — propagated from dependent nodes
            upstream = n.properties.get("_upstream_warning")
            if upstream and "_warning" not in entry:
                entry["_warning"] = f"⚠️ {upstream}"
                flagged_count += 1
            node_list.append(entry)

        # Detect SDLC conflicts: multiple ArchDecisions governing same module
        conflicts = self._detect_sdlc_conflicts()

        return {
            "topic": topic,
            "phase": phase,
            "nodes": node_list,
            "stale_count": stale_count,
            "flagged_count": flagged_count,
            "conflicts": conflicts,
        }

    def _detect_sdlc_conflicts(self) -> List[Dict[str, Any]]:
        """Detect contradictions in the SDLC graph.

        Checks:
        - Multiple ArchDecisions governing the same CodeModule
        - Multiple Requirements with contradicting content for same target
        """
        all_edges = self.db.get_all_edges()
        conflicts = []

        # Group GOVERNS edges by target (CodeModule)
        from collections import defaultdict
        governs_by_target: Dict[str, List[str]] = defaultdict(list)
        for edge in all_edges:
            if edge.label == "GOVERNS":
                governs_by_target[edge.target].append(edge.source)

        # Flag modules governed by 2+ decisions (potential conflict)
        for target_id, source_ids in governs_by_target.items():
            if len(source_ids) < 2:
                continue
            # Check if the decisions actually contradict
            decisions = []
            for sid in source_ids:
                node = self.db.get_node(sid)
                if node and node.label == "ArchDecision":
                    decisions.append({
                        "node_id": sid,
                        "decision": node.properties.get("decision", "")[:150],
                    })
            if len(decisions) >= 2:
                conflicts.append({
                    "type": "multiple_governance",
                    "target": target_id,
                    "decisions": decisions,
                    "hint": f"{len(decisions)} ArchDecisions govern {target_id} — verify they don't contradict",
                })

        return conflicts[:10]  # cap

    # ── Spec vs Code Gap Analysis ────────────────────────────────────────

    def spec_code_diff(self) -> Dict[str, Any]:
        """Compare spec (intent/design) against implementation (build/verify).

        Finds:
        - Requirements with no IMPLEMENTS edge (unimplemented)
        - Requirements with no SATISFIES edge (untested)
        - CodeModules with no IMPLEMENTS edge (untracked code)
        - ArchDecisions with no GOVERNS edge (unenforced decisions)

        Returns:
            {unimplemented: [...], untested: [...], untracked: [...],
             unenforced: [...], coverage_pct: float}
        """
        all_nodes = self.db.get_all_nodes()
        all_edges = self.db.get_all_edges()

        # Build edge lookup: (source_label, target_label, edge_label) → set of target_ids
        # e.g., IMPLEMENTS edges: CodeModule → Requirement
        implemented_reqs = set()    # requirements that have an IMPLEMENTS edge
        tested_reqs = set()         # requirements that have a SATISFIES edge
        tracked_code = set()        # code modules that have an IMPLEMENTS edge
        governed_code = set()       # code modules governed by an ArchDecision

        for edge in all_edges:
            if edge.label == "IMPLEMENTS":
                implemented_reqs.add(edge.target)  # target = Requirement
                tracked_code.add(edge.source)       # source = CodeModule
            elif edge.label == "SATISFIES":
                tested_reqs.add(edge.target)        # target = Requirement
            elif edge.label == "GOVERNS":
                governed_code.add(edge.target)      # target = CodeModule

        requirements = [n for n in all_nodes if n.label == "Requirement"]
        code_modules = [n for n in all_nodes if n.label == "CodeModule"]
        arch_decisions = [n for n in all_nodes if n.label == "ArchDecision"]

        unimplemented = [
            {"node_id": n.id, "content": _truncate_props(n.properties).get("content", "")[:200]}
            for n in requirements if n.id not in implemented_reqs
        ]
        untested = [
            {"node_id": n.id, "content": _truncate_props(n.properties).get("content", "")[:200]}
            for n in requirements if n.id not in tested_reqs
        ]
        untracked = [
            {"node_id": n.id, "summary": _truncate_props(n.properties).get("summary", "")[:200]}
            for n in code_modules if n.id not in tracked_code
        ]
        unenforced = [
            {"node_id": n.id, "decision": _truncate_props(n.properties).get("decision", "")[:200]}
            for n in arch_decisions
            if not any(e.source == n.id and e.label == "GOVERNS" for e in all_edges)
        ]

        total_reqs = len(requirements)
        implemented_count = total_reqs - len(unimplemented)
        coverage_pct = (implemented_count / total_reqs) if total_reqs > 0 else 1.0

        return {
            "unimplemented": unimplemented,
            "untested": untested,
            "untracked": untracked,
            "unenforced": unenforced,
            "total_requirements": total_reqs,
            "implemented_count": implemented_count,
            "tested_count": total_reqs - len(untested),
            "coverage_pct": round(coverage_pct, 2),
        }

    # ── Search ───────────────────────────────────────────────────────────────

    def search(
        self,
        query: str,
        layer: str = "",
        limit: int = 20,
    ) -> List[Dict[str, Any]]:
        """Search SDLC nodes by hybrid retrieval (BM25 + vector + graph hops).

        Uses the graph-enhanced search engine with Reciprocal Rank Fusion
        when available, falling back to brute-force term matching when the
        BM25 index is empty (e.g. nodes ingested before indexing was wired).

        Args:
            query: space-separated keywords (case-insensitive)
            layer: SDLC layer to restrict to (intent/design/build/verify/
                   evolution) — empty means all layers
            limit: max results to return (default 20)

        Returns:
            List of {node_id, label, layer, score, snippet} ordered by score desc.
        """
        if not query or not query.strip():
            return []

        # Validate layer filter
        layer_types: Optional[set] = None
        if layer:
            layer_norm = layer.lower()
            if layer_norm not in SDLC_LAYERS:
                raise ValueError(
                    f"Unknown SDLC layer '{layer}'. "
                    f"Valid layers: {', '.join(SDLC_LAYERS)}"
                )
            layer_types = set(SDLC_LAYERS[layer_norm]["node_types"])

        # Try hybrid search (BM25 + vector + RRF + graph hops + reranking)
        try:
            from ..search.graph_search import graph_search
            gs_result = graph_search(
                self.db, query, graph_name=self.name, k=limit * 2,
            )
        except Exception:
            gs_result = None

        terms = [t.lower() for t in query.split() if t]

        if gs_result and gs_result.nodes:
            return self._format_graph_results(gs_result, layer_types, terms, limit)

        # Fallback: brute-force term scan (cold start / empty index)
        return self._fallback_term_search(terms, layer_types, limit)

    def _format_graph_results(
        self,
        gs_result,
        layer_types: Optional[set],
        terms: List[str],
        limit: int,
    ) -> List[Dict[str, Any]]:
        """Convert GraphSearchResult nodes to SDLC search format."""
        results = []
        for sn in gs_result.nodes:
            if not is_sdlc_node_type(sn.label):
                continue
            if layer_types is not None and sn.label not in layer_types:
                continue

            snippet = ""
            for k, v in sn.props.items():
                if k.startswith("_") or not isinstance(v, str):
                    continue
                if any(t in v.lower() for t in terms):
                    snippet = v[:120]
                    break

            results.append({
                "node_id": sn.node_id,
                "label": sn.label,
                "layer": layer_for_node_type(sn.label),
                "score": sn.score,
                "snippet": snippet,
            })
            if len(results) >= limit:
                break
        return results

    def _fallback_term_search(
        self,
        terms: List[str],
        layer_types: Optional[set],
        limit: int,
    ) -> List[Dict[str, Any]]:
        """Brute-force term scan for when BM25 index is empty."""
        results = []
        for node in self.db.get_all_nodes():
            if not is_sdlc_node_type(node.label):
                continue
            if layer_types is not None and node.label not in layer_types:
                continue

            text = " ".join(
                str(v).lower()
                for k, v in node.properties.items()
                if isinstance(v, str) and not k.startswith("_")
            )
            if not text:
                continue

            score = sum(1 for t in terms if t in text)
            if score == 0:
                continue

            snippet = ""
            for k, v in node.properties.items():
                if k.startswith("_") or not isinstance(v, str):
                    continue
                if any(t in v.lower() for t in terms):
                    snippet = v[:120]
                    break

            results.append({
                "node_id": node.id,
                "label": node.label,
                "layer": layer_for_node_type(node.label),
                "score": score,
                "snippet": snippet,
            })

        results.sort(key=lambda r: r["score"], reverse=True)
        return results[:limit]

"""Composite Context — aggregates multiple atomic contexts into a single signal.

Three context levels:
  ATOMIC:    Single data source, one pipeline, one graph namespace
             e.g., "rbi_monetary_policy", "crude_oil_price"

  COMPOSITE: Aggregates multiple atomic contexts with weighted scoring
             e.g., "govt_policy" = rbi + sebi + budget + trade + tax

  META:      Aggregates composites + atomics at the highest level
             e.g., "macro" = govt_policy + india_economy + global_macro + forex

When a new sub-pipeline is added to a composite:
  1. An atomic context is created (or reused) for the sub-signal
  2. The atomic is linked to the composite via FEEDS_INTO edge
  3. The composite auto-recomputes its score from all children
  4. The meta auto-recomputes from all composites
  5. All dependent entities get the propagated signal

Usage:
    cm = CompositeContextManager(registry)

    # Create composite
    cm.create_composite("govt_policy", description="Government Policy Signals")

    # Add sub-pipelines
    cm.add_child("govt_policy", "rbi_monetary", weight=0.30,
                 pipeline={"source_type": "rss", "sources": ["rbi.org.in/rss"], "topics": ["RBI", "repo rate"]})
    cm.add_child("govt_policy", "sebi_regulation", weight=0.20,
                 pipeline={"source_type": "rss", "sources": ["sebi.gov.in"], "topics": ["SEBI"]})

    # Link composite to meta
    cm.link_to_parent("govt_policy", "macro", weight=0.25)

    # Compute composite score
    score = cm.compute_composite_score("govt_policy")
    # → aggregates all children's latest sentiment into one score
"""
import logging
import uuid
from datetime import datetime, timezone
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class ContextChild:
    """A child context (atomic) within a composite."""
    name: str                    # graph namespace
    weight: float = 1.0          # contribution weight (0-1)
    description: str = ""
    pipeline_config: Dict = field(default_factory=dict)  # RSS/API config
    latest_score: float = 0.0    # last computed signal value
    latest_timestamp: str = ""
    node_count: int = 0


@dataclass
class CompositeScore:
    """Aggregated score from a composite context."""
    context_name: str
    score: float                 # weighted average of children
    direction: str               # bullish/bearish/neutral
    children_scores: Dict[str, float]  # per-child scores
    children_weights: Dict[str, float]
    contributing_children: int
    timestamp: str = ""


def _make_node(node_id, label, properties):
    from dataclasses import dataclass as _dc, field as _f
    @_dc
    class _Node:
        id: str
        label: str
        properties: Dict[str, Any] = _f(default_factory=dict)
    return _Node(id=node_id, label=label, properties=properties)


def _safe_add_node(db, node):
    try:
        db.add_node(node, write_through=True)
    except TypeError:
        try:
            db.add_node(node)
        except TypeError:
            db.add_node(node.id, node.label, node.properties)


def _get_all_nodes(db):
    nodes = list(getattr(db, "node_index", {}).values())
    if nodes:
        return nodes
    adapter = getattr(db, "csr_adapter", None)
    if adapter and hasattr(adapter, "get_all_nodes"):
        return adapter.get_all_nodes()
    return []


class CompositeContextManager:
    """Manages composite (aggregated) and atomic (leaf) contexts."""

    def __init__(self, registry, context_manager=None):
        self._registry = registry
        self._cm = context_manager

    # ── Composite CRUD ────────────────────────────────────────────

    def create_composite(
        self,
        name: str,
        description: str = "",
        context_type: str = "composite",
        parent: str = None,
        tags: List[str] = None,
    ) -> Dict[str, Any]:
        """Create a composite context (aggregator).

        This creates a graph namespace that holds CompositeDefinition +
        ChildLink nodes. The composite doesn't ingest data directly —
        its children do. The composite just aggregates their scores.
        """
        ns = name.lower().replace(" ", "_")
        db = self._registry.get_graph(ns, load_if_missing=True)
        if db is None:
            db = self._registry.create_graph(ns)

        # Store composite definition
        comp_id = f"composite_{ns}"
        props = {
            "name": name,
            "namespace": ns,
            "type": context_type,
            "description": description,
            "parent": parent or "",
            "tags": ",".join(tags or []),
            "children_count": 0,
            "latest_score": 0.0,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        node = _make_node(comp_id, "CompositeDefinition", props)
        _safe_add_node(db, node)

        logger.info("[COMPOSITE] Created composite context: %s", name)
        return {"name": name, "namespace": ns, "type": context_type, **props}

    def add_child(
        self,
        composite_name: str,
        child_name: str,
        weight: float = 1.0,
        description: str = "",
        pipeline: Dict = None,
        pipelines: List[Dict] = None,
        interval: str = "30m",
    ) -> Dict[str, Any]:
        """Add an atomic child context to a composite.

        If the child doesn't exist, creates it with the given pipeline config(s).
        Links child → composite via FEEDS_INTO relationship.

        An atomic context can have MULTIPLE pipelines — same topic, different data sources:
          pipelines=[
            {"source_type": "rss", "sources": ["rbi.org.in/rss"], "topics": ["RBI"], "interval": "1h"},
            {"source_type": "rss", "sources": ["economictimes.com/rss/rbi"], "topics": ["RBI"], "interval": "30m"},
            {"source_type": "crawl", "sources": ["https://rbi.org.in/circulars"], "topics": ["circular"], "interval": "6h"},
          ]

        Args:
            composite_name: parent composite (e.g., "govt_policy")
            child_name: atomic child (e.g., "rbi_monetary")
            weight: how much this child contributes to composite score (0-1)
            description: what this child tracks
            pipeline: single pipeline config (backward compat) {source_type, sources, topics}
            pipelines: list of pipeline configs (preferred — multiple feeds per atomic)
            interval: default interval (used if individual pipelines don't specify)
        """
        # Normalize: single pipeline → list
        all_pipelines = list(pipelines or [])
        if pipeline and pipeline not in all_pipelines:
            all_pipelines.append(pipeline)
        composite_ns = composite_name.lower().replace(" ", "_")
        child_ns = child_name.lower().replace(" ", "_")

        # 1. Ensure child graph namespace exists
        child_db = self._registry.get_graph(child_ns, load_if_missing=True)
        if child_db is None:
            child_db = self._registry.create_graph(child_ns)

        # 2. Store child definition in child's graph
        child_id = f"atomic_{child_ns}"
        child_props = {
            "name": child_name,
            "namespace": child_ns,
            "type": "atomic",
            "description": description,
            "parent_composite": composite_ns,
            "weight_in_parent": weight,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        _safe_add_node(child_db, _make_node(child_id, "AtomicDefinition", child_props))

        # 3. Store child link in composite's graph
        comp_db = self._registry.get_graph(composite_ns, load_if_missing=True)
        if comp_db:
            link_id = f"child_{child_ns}"
            link_props = {
                "child_name": child_name,
                "child_namespace": child_ns,
                "weight": weight,
                "description": description,
                "pipeline_config": str(pipeline or {}),
                "interval": interval,
                "latest_score": 0.0,
                "linked_at": datetime.now(timezone.utc).isoformat(),
            }
            _safe_add_node(comp_db, _make_node(link_id, "ChildLink", link_props))

            # Update children count in composite definition
            for n in _get_all_nodes(comp_db):
                if n.label == "CompositeDefinition":
                    n.properties["children_count"] = len([
                        x for x in _get_all_nodes(comp_db) if x.label == "ChildLink"
                    ])
                    break

        # 4. Create pipelines for the child context
        if all_pipelines and self._cm:
            try:
                existing = None
                for ctx in self._cm.list_contexts(status="active"):
                    if ctx.name == child_name:
                        existing = ctx
                        break

                # Build pipeline configs from all provided pipelines
                pipeline_configs = []
                for i, p in enumerate(all_pipelines):
                    p_interval = p.get("interval", interval)
                    p_name = f"{child_ns}_{p.get('source_type', 'feed')}_{i}"
                    pipeline_configs.append({
                        "name": p_name,
                        "source_type": p.get("source_type", "rss"),
                        "interval": p_interval,
                        "strategy": p.get("strategy", "news_article"),
                        "sources": p.get("sources", []),
                        "filter": {
                            "topics": p.get("topics", []),
                            "exclude": p.get("exclude", []),
                        },
                    })

                # Find the fastest interval for the scheduler
                fastest = min(self._interval_to_minutes(p.get("interval", interval)) for p in all_pipelines)

                if existing is None:
                    self._cm.create_context(
                        name=child_name,
                        description=description or f"Atomic context: {child_name}",
                        context_type="knowledge_base",
                        source="collection_plan",
                        tags=[composite_ns, "atomic", "sub_pipeline"],
                        config={
                            "pipelines": pipeline_configs,
                            "schedule": {
                                "trigger_type": "scheduled",
                                "status": "active",
                                "interval_minutes": fastest,
                            },
                        },
                    )
                    logger.info("[COMPOSITE] Created child context with %d pipelines: %s → %s",
                                len(pipeline_configs), child_name, composite_name)
                else:
                    # Merge new pipelines into existing context
                    existing_config = dict(existing.config or {})
                    existing_pipes = existing_config.get("pipelines", [])
                    existing_names = {p.get("name") for p in existing_pipes}
                    for pc in pipeline_configs:
                        if pc["name"] not in existing_names:
                            existing_pipes.append(pc)
                    existing_config["pipelines"] = existing_pipes
                    self._cm.update_context(existing.context_id, config=existing_config)
                    logger.info("[COMPOSITE] Added %d pipelines to existing child: %s",
                                len(pipeline_configs), child_name)

                # Trigger immediate first run
                try:
                    for ctx in self._cm.list_contexts(status="active"):
                        if ctx.name == child_name:
                            self._cm.run_pipelines(ctx.context_id)
                            break
                except Exception:
                    pass

            except Exception as e:
                logger.warning("[COMPOSITE] Pipeline creation failed for %s: %s", child_name, e)

        logger.info("[COMPOSITE] Added child %s → %s (weight=%.2f)", child_name, composite_name, weight)
        return {
            "child": child_name,
            "composite": composite_name,
            "weight": weight,
            "pipeline_created": bool(pipeline),
        }

    def add_pipeline_to_child(
        self,
        child_name: str,
        pipeline: Dict,
    ) -> Dict[str, Any]:
        """Add a new pipeline to an existing atomic context.

        An atomic context can have multiple pipelines. This adds another one.

        Args:
            child_name: existing atomic context (e.g., "rbi_monetary")
            pipeline: {source_type, sources, topics, interval, strategy}

        Usage:
            # rbi_monetary already has RSS pipeline
            # Now add YouTube pipeline for RBI governor speeches
            cm.add_pipeline_to_child("rbi_monetary", {
                "source_type": "youtube",
                "sources": ["RBI official channel"],
                "topics": ["governor speech", "monetary policy"],
                "interval": "daily",
            })
        """
        child_ns = child_name.lower().replace(" ", "_")
        p_interval = pipeline.get("interval", "daily")
        p_name = f"{child_ns}_{pipeline.get('source_type', 'feed')}_{uuid.uuid4().hex[:4]}"

        new_pipe_config = {
            "name": p_name,
            "source_type": pipeline.get("source_type", "rss"),
            "interval": p_interval,
            "strategy": pipeline.get("strategy", "news_article"),
            "sources": pipeline.get("sources", []),
            "filter": {
                "topics": pipeline.get("topics", []),
                "exclude": pipeline.get("exclude", []),
            },
        }

        if not self._cm:
            return {"error": "context_manager not available"}

        # Find existing context and merge the new pipeline
        try:
            for ctx in self._cm.list_contexts(status="active"):
                if ctx.name == child_name:
                    existing_config = dict(ctx.config or {})
                    existing_pipes = existing_config.get("pipelines", [])
                    existing_pipes.append(new_pipe_config)
                    existing_config["pipelines"] = existing_pipes
                    self._cm.update_context(ctx.context_id, config=existing_config)

                    # Trigger immediate run of the new pipeline
                    try:
                        self._cm.run_pipelines(ctx.context_id)
                    except Exception:
                        pass

                    logger.info("[COMPOSITE] Added pipeline %s to %s (total: %d)",
                                p_name, child_name, len(existing_pipes))
                    return {
                        "status": "added",
                        "child": child_name,
                        "pipeline_name": p_name,
                        "source_type": pipeline.get("source_type", "rss"),
                        "total_pipelines": len(existing_pipes),
                    }

            return {"error": f"Context '{child_name}' not found"}
        except Exception as e:
            return {"error": str(e)}

    def remove_child(self, composite_name: str, child_name: str) -> bool:
        """Remove a child from a composite."""
        composite_ns = composite_name.lower().replace(" ", "_")
        child_ns = child_name.lower().replace(" ", "_")
        comp_db = self._registry.get_graph(composite_ns, load_if_missing=True)
        if not comp_db:
            return False

        link_id = f"child_{child_ns}"
        try:
            adapter = getattr(comp_db, "csr_adapter", comp_db)
            if hasattr(adapter, "delete_node"):
                adapter.delete_node(link_id)
            return True
        except Exception:
            return False

    def link_to_parent(self, child_composite: str, parent_meta: str, weight: float = 0.5):
        """Link a composite to a meta context (e.g., govt_policy → macro)."""
        return self.add_child(parent_meta, child_composite, weight=weight,
                              description=f"Composite: {child_composite}")

    # ── Score Computation ─────────────────────────────────────────

    def get_children(self, composite_name: str) -> List[ContextChild]:
        """Get all children of a composite context."""
        composite_ns = composite_name.lower().replace(" ", "_")
        comp_db = self._registry.get_graph(composite_ns, load_if_missing=True)
        if not comp_db:
            return []

        children = []
        for n in _get_all_nodes(comp_db):
            if n.label == "ChildLink":
                props = n.properties if hasattr(n, "properties") else {}
                child_ns = props.get("child_namespace", "")

                # Get latest score from child's graph
                latest_score = 0.0
                node_count = 0
                if child_ns:
                    child_db = self._registry.get_graph(child_ns, load_if_missing=True)
                    if child_db:
                        child_nodes = _get_all_nodes(child_db)
                        node_count = len(child_nodes)
                        # Compute sentiment from Fact nodes
                        pos = sum(1 for cn in child_nodes
                                  if cn.label == "Fact" and
                                  (cn.properties if hasattr(cn, "properties") else {}).get("_sentiment") == "positive")
                        neg = sum(1 for cn in child_nodes
                                  if cn.label == "Fact" and
                                  (cn.properties if hasattr(cn, "properties") else {}).get("_sentiment") == "negative")
                        total = pos + neg
                        if total > 0:
                            latest_score = (pos - neg) / total

                children.append(ContextChild(
                    name=props.get("child_name", child_ns),
                    weight=float(props.get("weight", 1.0)),
                    description=props.get("description", ""),
                    pipeline_config={},
                    latest_score=latest_score,
                    latest_timestamp=props.get("linked_at", ""),
                    node_count=node_count,
                ))

        return children

    def compute_composite_score(self, composite_name: str) -> CompositeScore:
        """Compute the weighted aggregate score for a composite context."""
        children = self.get_children(composite_name)
        if not children:
            return CompositeScore(
                context_name=composite_name, score=0.0, direction="neutral",
                children_scores={}, children_weights={},
                contributing_children=0,
                timestamp=datetime.now(timezone.utc).isoformat(),
            )

        total_weight = sum(c.weight for c in children)
        if total_weight == 0:
            total_weight = 1

        weighted_sum = sum(c.latest_score * c.weight for c in children)
        score = weighted_sum / total_weight
        score = max(-1, min(1, score))

        direction = "bullish" if score > 0.05 else ("bearish" if score < -0.05 else "neutral")

        return CompositeScore(
            context_name=composite_name,
            score=round(score, 4),
            direction=direction,
            children_scores={c.name: round(c.latest_score, 4) for c in children},
            children_weights={c.name: round(c.weight, 2) for c in children},
            contributing_children=sum(1 for c in children if c.node_count > 0),
            timestamp=datetime.now(timezone.utc).isoformat(),
        )

    # ── Query ─────────────────────────────────────────────────────

    def list_composites(self) -> List[Dict]:
        """List all composite contexts across the registry."""
        composites = []
        for ns in self._registry.list_graphs():
            db = self._registry.get_graph(ns, load_if_missing=True)
            if not db:
                continue
            for n in _get_all_nodes(db):
                if n.label == "CompositeDefinition":
                    props = n.properties if hasattr(n, "properties") else {}
                    composites.append({
                        "name": props.get("name", ns),
                        "namespace": ns,
                        "type": props.get("type", "composite"),
                        "description": props.get("description", ""),
                        "parent": props.get("parent", ""),
                        "children_count": int(props.get("children_count", 0)),
                        "latest_score": float(props.get("latest_score", 0)),
                    })
        return composites

    def get_context_hierarchy(self, root_name: str) -> Dict:
        """Get the full hierarchy tree from a root context."""
        children = self.get_children(root_name)
        tree = {
            "name": root_name,
            "type": "composite" if children else "atomic",
            "children": [],
        }
        for child in children:
            # Recursively get sub-children
            sub_tree = self.get_context_hierarchy(child.name)
            sub_tree["weight"] = child.weight
            sub_tree["score"] = child.latest_score
            sub_tree["node_count"] = child.node_count
            tree["children"].append(sub_tree)
        return tree

    # ── Helpers ───────────────────────────────────────────────────

    @staticmethod
    def _interval_to_minutes(interval: str) -> int:
        """Convert interval string to minutes."""
        if not interval:
            return 30
        interval = interval.strip().lower()
        if interval.endswith("m"):
            return int(interval[:-1])
        if interval.endswith("h"):
            return int(interval[:-1]) * 60
        if interval == "daily":
            return 1440
        if interval == "weekly":
            return 10080
        return 30

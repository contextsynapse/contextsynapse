"""Context Seed Engine — seeds entity contexts using multiple context layers.

Unlike single-domain seeding, this engine:
  1. Asks ALL registered layers if they know about the entity
  2. Merges knowledge from every layer that responds
  3. Builds a unified schema from all contributing layers
  4. Sets up cross-layer dependency edges
  5. Recommends sensors from all relevant layers

Example: seeding "Reliance"
  - finance layer: stock data, earnings schema, price sensor
  - macro layer: GDP dependency, RBI regulation
  - supply_chain layer: crude oil input, ethylene supply
  - psychology layer: retail herding patterns
  - weather layer: monsoon impact on retail segment

  All combined into ONE entity context with edges to shared entities.
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Result data class
# ---------------------------------------------------------------------------


@dataclass
class SeedResult:
    """Complete result of seeding a new entity context via multiple layers."""

    entity: str
    contributing_layers: List[str]         # which layers enriched this entity
    discovery: dict                        # merged discovery from all layers + LLM
    schema: dict                           # merged node types and edge types
    dependency_map: dict                   # all dependencies across layers
    pipeline_recommendations: list         # suggested data feeds with reasons
    sensor_suggestions: list               # sensors to activate with reasons
    cross_layer_connections: list           # edges to shared/meta entities
    estimated_signal_sources: int
    llm_used: bool
    timestamp: str

    def summary(self) -> str:
        """Human-readable summary of the seed result."""
        lines = [
            f"Entity: {self.entity}",
            f"Layers: {', '.join(self.contributing_layers) or 'none'}",
            f"LLM used: {self.llm_used}",
            f"Schema: {len(self.schema.get('node_types', []))} node types, "
            f"{len(self.schema.get('edge_types', []))} edge types",
            f"Dependencies: {sum(len(v) for v in self.dependency_map.values() if isinstance(v, list))} total",
            f"Pipelines: {len(self.pipeline_recommendations)}",
            f"Sensors: {len(self.sensor_suggestions)}",
            f"Cross-layer connections: {len(self.cross_layer_connections)}",
            f"Estimated signal sources: {self.estimated_signal_sources}",
        ]
        return "\n".join(lines)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------------------
# Context Seed Engine
# ---------------------------------------------------------------------------


class ContextSeedEngine:
    """Seeds entity contexts using ALL available context layers.

    The engine asks every registered layer whether it knows about the
    entity, collects and merges their knowledge, optionally enriches
    via LLM, and produces a unified context seed that includes schema,
    dependency map, pipeline recommendations, and sensor suggestions
    from ALL contributing layers.

    Usage::

        from contextsynapse.context.layers import ContextLayer
        from contextsynapse.context.seed import ContextSeedEngine

        layers = [
            ContextLayer.from_yaml("layers/finance.yaml"),
            ContextLayer.from_yaml("layers/macro.yaml"),
            ContextLayer.from_yaml("layers/supply_chain.yaml"),
        ]
        engine = ContextSeedEngine(registry=my_registry, layers=layers)
        result = engine.seed("Reliance Industries", sector="Conglomerate")
        engine.apply(result)
    """

    def __init__(
        self,
        registry,
        layers: Optional[List] = None,
        llm_client=None,
    ):
        """
        Args:
            registry: GraphRegistry (or RedisGraphRegistry) for graph access.
            layers: List of ContextLayer instances.  If None, attempts to
                    load all registered layers via plugin discovery.
            llm_client: Optional LLM client with ``generate_json()`` method.
                        If None, uses layer data only (no LLM cost).
        """
        self.registry = registry
        self.llm_client = llm_client

        if layers is not None:
            self.layers = list(layers)
        else:
            self.layers = self._discover_layers()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def seed(
        self,
        entity_name: str,
        entity_type: str = "company",
        ticker: Optional[str] = None,
        sector: Optional[str] = None,
        exchange: str = "NSE",
        extra_context: str = "",
    ) -> SeedResult:
        """Seed an entity context using all available layers.

        Steps:
          1. Ask each layer: do you know about this entity?
          2. For layers that respond, collect their entity data
          3. If LLM available, run discovery with merged prompt
          4. Generate schema from all contributing layers
          5. Build dependency map from discovery + layer cross-edges
          6. Generate pipeline recommendations (each with WHY)
          7. Generate sensor suggestions (each with REASON)
          8. Identify cross-layer connections

        Args:
            entity_name: Human-readable entity name.
            entity_type: Type of entity (default "company").
            ticker: Optional identifier.
            sector: Optional sector override.  Auto-classified if omitted.
            exchange: Market identifier (default "NSE").
            extra_context: Additional text context for LLM discovery.

        Returns:
            SeedResult with all seeding artifacts.
        """
        now = datetime.now(timezone.utc).isoformat()

        # Step 1+2: Ask all layers and collect knowledge
        contributing_layers: List[str] = []
        merged_entity_data: Dict[str, Any] = {}

        for layer in self.layers:
            if layer.enriches(entity_name):
                contributing_layers.append(layer.layer_id)
                layer_data = layer.get_entity_data(entity_name)
                if layer_data:
                    # Merge: later layers extend, don't overwrite lists
                    _merge_dicts(merged_entity_data, layer_data, layer.layer_id)

        # Auto-classify sector from layers if not provided
        if not sector:
            for layer in self.layers:
                if layer.layer_id in contributing_layers:
                    classified = layer.classify(entity_name)
                    if classified and classified != "uncategorized":
                        sector = classified
                        break
            if not sector:
                sector = merged_entity_data.get("sector", "unknown")

        entity_info = {
            "entity_name": entity_name,
            "entity_type": entity_type,
            "ticker": ticker or merged_entity_data.get("ticker", ""),
            "sector": sector,
            "exchange": exchange,
            "extra_context": extra_context,
        }

        # Step 3: LLM discovery (optional) merged with layer data
        discovery, llm_used = self._discover(entity_info, merged_entity_data)

        # Step 4: Merge schema from all contributing layers
        schema = self._merge_schema(contributing_layers)

        # Step 5: Build dependency map from discovery + layer cross-edges
        dependency_map = self._build_dependency_map(discovery, contributing_layers)

        # Step 6: Pipeline recommendations from all layers
        pipeline_recs = self._generate_pipeline_recommendations(
            entity_name, dependency_map, sector, contributing_layers
        )

        # Step 7: Sensor suggestions from all contributing layers
        sensor_suggestions = self._generate_sensor_suggestions(
            entity_name, sector, contributing_layers
        )

        # Step 8: Cross-layer connections
        cross_layer_connections = self._find_cross_layer_connections(
            entity_name, entity_type, contributing_layers
        )

        # Estimate total source count
        estimated_sources = (
            len(pipeline_recs)
            + sum(1 for s in sensor_suggestions if s.get("activate"))
            + len(cross_layer_connections)
        )

        return SeedResult(
            entity=entity_name,
            contributing_layers=contributing_layers,
            discovery=discovery,
            schema=schema,
            dependency_map=dependency_map,
            pipeline_recommendations=pipeline_recs,
            sensor_suggestions=sensor_suggestions,
            cross_layer_connections=cross_layer_connections,
            estimated_signal_sources=estimated_sources,
            llm_used=llm_used,
            timestamp=now,
        )

    def apply(self, seed_result: SeedResult, context_manager=None) -> Dict[str, Any]:
        """Apply seed result to the graph.

        Creates:
          - Entity root node
          - KB nodes (people, brands, aliases)
          - Dependency nodes + edges
          - Schema definition node
          - Cross-layer edges to shared entities
          - SeedManifest node

        Args:
            seed_result: The SeedResult from seed().
            context_manager: Optional ContextManager for formal context creation.

        Returns:
            Dict with {namespace, nodes_created, edges_created}.
        """
        from contextsynapse.core.graph_structures import GraphEdge, GraphNode

        entity_slug = _slugify(seed_result.entity)
        namespace = entity_slug

        # Get or create graph
        graph = self.registry.get_graph(namespace, load_if_missing=True)
        if graph is None:
            graph = self.registry.create_graph(namespace)

        nodes_created = 0
        edges_created = 0

        # --- Entity root node ---
        entity_root_id = f"{entity_slug}_root"
        graph.add_node(GraphNode(
            id=entity_root_id,
            label="Entity",
            properties={
                "name": seed_result.entity,
                "contributing_layers": seed_result.contributing_layers,
                "sector": seed_result.discovery.get("sector", ""),
                "seeded_at": seed_result.timestamp,
            },
        ))
        nodes_created += 1

        # --- People ---
        for person in seed_result.discovery.get("people", []):
            name = person if isinstance(person, str) else person.get("name", str(person))
            title = "" if isinstance(person, str) else person.get("title", "")
            pid = f"{entity_slug}_person_{_slugify(name)}"
            graph.add_node(GraphNode(
                id=pid,
                label="Person",
                properties={"name": name, "title": title, "entity": seed_result.entity},
            ))
            graph.add_edge(GraphEdge(
                id=f"e_{entity_root_id}_{pid}",
                source=entity_root_id,
                target=pid,
                label="HAS_PERSON",
            ))
            nodes_created += 1
            edges_created += 1

        # --- Brands / subsidiaries ---
        for brand in seed_result.discovery.get("brands", []):
            name = brand if isinstance(brand, str) else brand.get("name", str(brand))
            bid = f"{entity_slug}_brand_{_slugify(name)}"
            graph.add_node(GraphNode(
                id=bid,
                label="Brand",
                properties={"name": name, "entity": seed_result.entity},
            ))
            graph.add_edge(GraphEdge(
                id=f"e_{entity_root_id}_{bid}",
                source=entity_root_id,
                target=bid,
                label="HAS_BRAND",
            ))
            nodes_created += 1
            edges_created += 1

        # --- Aliases ---
        aliases = seed_result.discovery.get("aliases", [])
        if aliases:
            alias_id = f"{entity_slug}_aliases"
            graph.add_node(GraphNode(
                id=alias_id,
                label="AliasSet",
                properties={"aliases": aliases, "entity": seed_result.entity},
            ))
            graph.add_edge(GraphEdge(
                id=f"e_{entity_root_id}_{alias_id}",
                source=entity_root_id,
                target=alias_id,
                label="HAS_ALIASES",
            ))
            nodes_created += 1
            edges_created += 1

        # --- Dependency map nodes ---
        dep_label_map = {
            "supply_chain": ("SupplyChainInput", "DEPENDS_ON"),
            "regulatory": ("Regulator", "REGULATED_BY"),
            "competitive": ("Competitor", "COMPETES_WITH"),
            "macro": ("MacroDependency", "AFFECTED_BY"),
        }
        for category, (node_label, edge_label) in dep_label_map.items():
            for dep in seed_result.dependency_map.get(category, []):
                dep_name = dep if isinstance(dep, str) else dep.get("name", str(dep))
                dep_layer = "" if isinstance(dep, str) else dep.get("layer", "")
                dep_id = f"{entity_slug}_{category}_{_slugify(dep_name)}"
                dep_props = {
                    "name": dep_name,
                    "category": category,
                    "entity": seed_result.entity,
                }
                if dep_layer:
                    dep_props["source_layer"] = dep_layer
                if isinstance(dep, dict):
                    dep_props.update({k: v for k, v in dep.items() if k not in ("name", "layer")})

                graph.add_node(GraphNode(
                    id=dep_id,
                    label=node_label,
                    properties=dep_props,
                ))
                graph.add_edge(GraphEdge(
                    id=f"e_{entity_root_id}_{dep_id}",
                    source=entity_root_id,
                    target=dep_id,
                    label=edge_label,
                ))
                nodes_created += 1
                edges_created += 1

        # --- Cross-layer connection nodes ---
        for conn in seed_result.cross_layer_connections:
            shared_name = conn.get("shared_entity", "")
            if not shared_name:
                continue
            shared_id = f"shared_{_slugify(shared_name)}"
            # Create shared entity node (idempotent — add_node can upsert)
            graph.add_node(GraphNode(
                id=shared_id,
                label="SharedEntity",
                properties={
                    "name": shared_name,
                    "entity_type": conn.get("entity_type", ""),
                    "linked_layers": conn.get("layers", []),
                },
            ))
            edge_type = conn.get("edge_type", "CONNECTED_TO")
            graph.add_edge(GraphEdge(
                id=f"e_{entity_root_id}_{shared_id}",
                source=entity_root_id,
                target=shared_id,
                label=edge_type,
                properties={
                    "propagation_weight": conn.get("propagation_weight", 0.7),
                },
            ))
            nodes_created += 1
            edges_created += 1

        # --- Schema node ---
        schema_id = f"{entity_slug}_schema"
        graph.add_node(GraphNode(
            id=schema_id,
            label="SchemaDefinition",
            properties={
                "entity": seed_result.entity,
                "contributing_layers": seed_result.contributing_layers,
                "schema": json.dumps(seed_result.schema),
                "created_at": seed_result.timestamp,
            },
        ))
        nodes_created += 1

        # --- SeedManifest node ---
        manifest_id = f"{entity_slug}_seed_manifest"
        graph.add_node(GraphNode(
            id=manifest_id,
            label="SeedManifest",
            properties={
                "entity": seed_result.entity,
                "contributing_layers": seed_result.contributing_layers,
                "llm_used": seed_result.llm_used,
                "estimated_signal_sources": seed_result.estimated_signal_sources,
                "pipeline_count": len(seed_result.pipeline_recommendations),
                "sensor_count": len(seed_result.sensor_suggestions),
                "cross_layer_count": len(seed_result.cross_layer_connections),
                "dependency_count": sum(
                    len(v) for v in seed_result.dependency_map.values()
                    if isinstance(v, list)
                ),
                "seeded_at": seed_result.timestamp,
                "seed_result_json": json.dumps(seed_result.to_dict()),
            },
        ))
        nodes_created += 1

        # Optionally create a formal Context
        if context_manager is not None:
            try:
                layer_tags = list(seed_result.contributing_layers) + ["auto-seeded"]
                context_manager.create_context(
                    name=seed_result.entity,
                    description=(
                        f"Auto-seeded context for {seed_result.entity} "
                        f"(layers: {', '.join(seed_result.contributing_layers)})"
                    ),
                    context_type="knowledge_base",
                    source="seed:multi_layer",
                    sensitivity="internal",
                    tags=layer_tags,
                    graph_namespace=namespace,
                )
            except Exception as exc:
                logger.warning(
                    "Could not create formal context for '%s': %s",
                    seed_result.entity, exc,
                )

        logger.info(
            "Applied seed for '%s' [layers: %s]: %d nodes, %d edges in namespace '%s'",
            seed_result.entity,
            ", ".join(seed_result.contributing_layers),
            nodes_created,
            edges_created,
            namespace,
        )

        return {
            "namespace": namespace,
            "nodes_created": nodes_created,
            "edges_created": edges_created,
        }

    def seed_shared_entity(
        self, entity_name: str, entity_type: str
    ) -> Dict[str, Any]:
        """Seed a shared/meta entity that acts as a bridge across layers.

        Shared entities (e.g. "crude_oil", "interest_rates", "monsoon")
        exist in a dedicated ``_shared`` namespace and are referenced by
        multiple entity contexts via cross-layer edges.

        Args:
            entity_name: Name of the shared entity.
            entity_type: Type label (e.g. "commodity", "policy", "weather").

        Returns:
            Dict with {namespace, node_id, contributing_layers}.
        """
        from contextsynapse.core.graph_structures import GraphNode

        namespace = "_shared"
        graph = self.registry.get_graph(namespace, load_if_missing=True)
        if graph is None:
            graph = self.registry.create_graph(namespace)

        node_id = f"shared_{_slugify(entity_name)}"

        # Determine which layers know about this shared entity
        contributing = [
            layer.layer_id for layer in self.layers
            if layer.enriches(entity_name)
        ]

        graph.add_node(GraphNode(
            id=node_id,
            label="SharedEntity",
            properties={
                "name": entity_name,
                "entity_type": entity_type,
                "contributing_layers": contributing,
                "seeded_at": datetime.now(timezone.utc).isoformat(),
            },
        ))

        logger.info(
            "Seeded shared entity '%s' (%s) in _shared namespace, layers: %s",
            entity_name, entity_type, ", ".join(contributing) or "none",
        )

        return {
            "namespace": namespace,
            "node_id": node_id,
            "contributing_layers": contributing,
        }

    def get_seeded_entities(self) -> List[Dict[str, Any]]:
        """List all seeded entities with their contributing layers.

        Scans the registry for graphs that contain a SeedManifest node.
        """
        results = []
        graphs = self.registry.list_graphs()
        for graph_info in graphs:
            namespace = graph_info.get("name", "")
            if not namespace or namespace.startswith("_"):
                continue

            graph = self.registry.get_graph(namespace, load_if_missing=False)
            if graph is None:
                continue

            try:
                manifests = graph.get_all_nodes(label="SeedManifest")
            except Exception:
                continue

            for node in manifests:
                props = node.properties or {}
                results.append({
                    "entity": props.get("entity", namespace),
                    "namespace": namespace,
                    "contributing_layers": props.get("contributing_layers", []),
                    "llm_used": props.get("llm_used", False),
                    "estimated_signal_sources": props.get("estimated_signal_sources", 0),
                    "seeded_at": props.get("seeded_at", ""),
                })

        return results

    # ------------------------------------------------------------------
    # Internal: discovery
    # ------------------------------------------------------------------

    def _discover(
        self,
        entity_info: Dict[str, Any],
        merged_layer_data: Dict[str, Any],
    ) -> tuple:
        """Attempt LLM discovery merged with layer data.

        Returns:
            (discovery_dict, llm_used_bool)
        """
        # Try LLM discovery if client available and any layer has a seed prompt
        if self.llm_client:
            seed_prompts = [
                layer.seed_prompt_template
                for layer in self.layers
                if layer.seed_prompt_template
            ]
            if seed_prompts:
                try:
                    llm_result = self._llm_discover(entity_info, seed_prompts)
                    if llm_result and isinstance(llm_result, dict):
                        # Merge LLM result with layer data (layer data takes priority for lists)
                        merged = dict(llm_result)
                        _merge_dicts(merged, merged_layer_data, source="layer_data")
                        logger.info(
                            "LLM discovery succeeded for '%s' (%d keys)",
                            entity_info["entity_name"],
                            len(merged),
                        )
                        return merged, True
                except Exception as exc:
                    logger.warning(
                        "LLM discovery failed for '%s': %s — using layer data",
                        entity_info["entity_name"],
                        exc,
                    )

        # Fallback: use merged layer data only
        if merged_layer_data:
            logger.info(
                "Using layer data for '%s' (%d keys)",
                entity_info["entity_name"],
                len(merged_layer_data),
            )
            return merged_layer_data, False

        # Minimal discovery
        logger.info(
            "No discovery data for '%s', using entity info only",
            entity_info["entity_name"],
        )
        return {
            "name": entity_info["entity_name"],
            "entity_type": entity_info.get("entity_type", "unknown"),
            "sector": entity_info.get("sector", "unknown"),
            "ticker": entity_info.get("ticker", ""),
            "people": [],
            "brands": [],
            "aliases": [],
            "supply_chain_inputs": [],
            "regulators": [],
            "competitors": [],
            "macro_dependencies": [],
        }, False

    def _llm_discover(
        self,
        entity_info: Dict[str, Any],
        seed_prompts: List[str],
    ) -> Dict[str, Any]:
        """Call LLM with merged seed prompts from all layers."""
        # Combine prompts from all layers
        combined_prompt = "\n\n".join(seed_prompts)
        for key, value in entity_info.items():
            combined_prompt = combined_prompt.replace("{{ " + key + " }}", str(value))
            combined_prompt = combined_prompt.replace("{{" + key + "}}", str(value))
            combined_prompt = combined_prompt.replace("{" + key + "}", str(value))

        system = (
            "You are a multi-domain knowledge expert. "
            "Return a JSON object with discovery data for the entity. "
            "Include: people (key people with name+title), brands (subsidiaries/brands), "
            "aliases (alternative names/tickers), supply_chain_inputs (upstream dependencies), "
            "regulators (regulatory bodies), competitors (direct competitors), "
            "macro_dependencies (macro factors), sector (industry sector), "
            "key_entities (other relevant entities)."
        )

        return self.llm_client.generate_json(combined_prompt, system=system)

    # ------------------------------------------------------------------
    # Internal: schema merging
    # ------------------------------------------------------------------

    def _merge_schema(self, contributing_layer_ids: List[str]) -> Dict[str, Any]:
        """Merge node and edge schemas from all contributing layers."""
        node_types: List[Dict[str, Any]] = []
        edge_types: List[Dict[str, Any]] = []
        seen_node_types: set = set()
        seen_edge_types: set = set()

        for layer in self.layers:
            if layer.layer_id not in contributing_layer_ids:
                continue

            for ns in layer.node_schemas:
                if ns.type_name not in seen_node_types:
                    node_types.append(ns.to_dict())
                    seen_node_types.add(ns.type_name)

            for es in layer.edge_schemas:
                key = (es.edge_type, tuple(es.from_types), tuple(es.to_types))
                if key not in seen_edge_types:
                    edge_types.append(es.to_dict())
                    seen_edge_types.add(key)

        return {
            "node_types": node_types,
            "edge_types": edge_types,
            "contributing_layers": list(contributing_layer_ids),
        }

    # ------------------------------------------------------------------
    # Internal: dependency map
    # ------------------------------------------------------------------

    def _build_dependency_map(
        self,
        discovery: Dict[str, Any],
        contributing_layer_ids: List[str],
    ) -> Dict[str, List]:
        """Extract dependency categories from discovery data + layer cross-edges."""
        dep_map: Dict[str, List] = {
            "supply_chain": [],
            "regulatory": [],
            "competitive": [],
            "macro": [],
        }

        # From discovery data (same logic as before)
        key_mapping = {
            "supply_chain": ("supply_chain_inputs", "supply_chain", "inputs", "upstream"),
            "regulatory": ("regulators", "regulatory_bodies", "regulatory"),
            "competitive": ("competitors", "competition", "competitive"),
            "macro": ("macro_dependencies", "macro_factors", "macro"),
        }

        for category, keys in key_mapping.items():
            for key in keys:
                items = discovery.get(key, [])
                if items:
                    dep_map[category] = _normalize_list(items)
                    break

        # Enrich with cross-layer edge definitions
        for layer in self.layers:
            if layer.layer_id not in contributing_layer_ids:
                continue
            for cle in layer.cross_layer_edges:
                dep_entry = {
                    "name": cle.from_entity_type,
                    "edge_type": cle.edge_type,
                    "propagation_weight": cle.propagation_weight,
                    "description": cle.description,
                    "layer": layer.layer_id,
                }
                # Categorize the cross-layer edge
                if cle.edge_type in ("DEPENDS_ON", "SUPPLIES_TO"):
                    dep_map["supply_chain"].append(dep_entry)
                elif cle.edge_type in ("REGULATED_BY",):
                    dep_map["regulatory"].append(dep_entry)
                elif cle.edge_type in ("COMPETES_WITH",):
                    dep_map["competitive"].append(dep_entry)
                else:
                    dep_map["macro"].append(dep_entry)

        return dep_map

    # ------------------------------------------------------------------
    # Internal: pipeline recommendations
    # ------------------------------------------------------------------

    def _generate_pipeline_recommendations(
        self,
        entity_name: str,
        dependency_map: Dict[str, List],
        sector: str,
        contributing_layer_ids: List[str],
    ) -> List[Dict[str, Any]]:
        """Generate pipeline recommendations from dependencies."""
        recommendations = []

        category_config = {
            "supply_chain": {
                "source_type": "news",
                "interval": "6h",
                "priority": "high",
                "why_template": "Supply chain disruption in {dep} directly impacts {entity}",
            },
            "regulatory": {
                "source_type": "regulatory_filing",
                "interval": "daily",
                "priority": "high",
                "why_template": "Regulatory actions by {dep} affect compliance posture of {entity}",
            },
            "competitive": {
                "source_type": "news",
                "interval": "12h",
                "priority": "medium",
                "why_template": "Competitive moves by {dep} affect market position of {entity}",
            },
            "macro": {
                "source_type": "data_feed",
                "interval": "daily",
                "priority": "medium",
                "why_template": "{dep} is a macro factor influencing {entity} performance",
            },
        }

        for category, deps in dependency_map.items():
            config = category_config.get(category, {
                "source_type": "news",
                "interval": "daily",
                "priority": "low",
                "why_template": "{dep} is related to {entity}",
            })

            for dep in deps:
                dep_name = dep if isinstance(dep, str) else dep.get("name", str(dep))
                dep_layer = "" if isinstance(dep, str) else dep.get("layer", "")
                search_terms = [dep_name, entity_name]

                recommendations.append({
                    "name": f"{_slugify(dep_name)}_{category}_pipeline",
                    "source_type": config["source_type"],
                    "interval": config["interval"],
                    "search_terms": search_terms,
                    "priority": config["priority"],
                    "why": config["why_template"].format(dep=dep_name, entity=entity_name),
                    "category": category,
                    "source_layer": dep_layer,
                })

        return recommendations

    # ------------------------------------------------------------------
    # Internal: sensor suggestions
    # ------------------------------------------------------------------

    def _generate_sensor_suggestions(
        self,
        entity_name: str,
        sector: str,
        contributing_layer_ids: List[str],
    ) -> List[Dict[str, Any]]:
        """Collect sensor suggestions from all contributing layers."""
        suggestions = []

        for layer in self.layers:
            if layer.layer_id not in contributing_layer_ids:
                continue

            relevant_sensors = layer.get_sensors_for(entity_name, sector=sector)
            for sensor in relevant_sensors:
                suggestions.append({
                    "sensor": sensor.name,
                    "activate": True,
                    "reason": (
                        f"{sensor.description} — relevant to {entity_name} "
                        f"(from {layer.layer_id} layer)"
                    ),
                    "interval": sensor.interval,
                    "type": sensor.sensor_type,
                    "layer": layer.layer_id,
                    "weight": sensor.weight,
                })

            # Also include sensors from the layer that were NOT relevant,
            # with activate=False for transparency
            all_sensor_names = {s.name for s in layer.sensors}
            relevant_names = {s.name for s in relevant_sensors}
            for sensor in layer.sensors:
                if sensor.name not in relevant_names:
                    suggestions.append({
                        "sensor": sensor.name,
                        "activate": False,
                        "reason": (
                            f"{sensor.name} has low relevance to {entity_name} "
                            f"— skip to save cost (from {layer.layer_id} layer)"
                        ),
                        "interval": sensor.interval,
                        "type": sensor.sensor_type,
                        "layer": layer.layer_id,
                        "weight": sensor.weight,
                    })

        return suggestions

    # ------------------------------------------------------------------
    # Internal: cross-layer connections
    # ------------------------------------------------------------------

    def _find_cross_layer_connections(
        self,
        entity_name: str,
        entity_type: str,
        contributing_layer_ids: List[str],
    ) -> List[Dict[str, Any]]:
        """Identify cross-layer connections for this entity.

        Looks at all cross_layer_edges from contributing layers and finds
        shared entities that this entity should connect to.
        """
        connections = []
        seen_shared: set = set()

        for layer in self.layers:
            if layer.layer_id not in contributing_layer_ids:
                continue

            for cle in layer.cross_layer_edges:
                # If entity type matches the edge's to_entity_type,
                # the from_entity_type is a shared entity to connect to
                if cle.to_entity_type == entity_type or cle.to_entity_type in ("*", "any"):
                    shared_key = f"{cle.from_entity_type}:{cle.edge_type}"
                    if shared_key not in seen_shared:
                        connections.append({
                            "shared_entity": cle.from_entity_type,
                            "entity_type": cle.from_entity_type,
                            "edge_type": cle.edge_type,
                            "propagation_weight": cle.propagation_weight,
                            "description": cle.description,
                            "layers": [layer.layer_id],
                        })
                        seen_shared.add(shared_key)

                # If entity type matches the edge's from_entity_type,
                # the to_entity_type is downstream
                if cle.from_entity_type == entity_type or cle.from_entity_type in ("*", "any"):
                    shared_key = f"{cle.to_entity_type}:{cle.edge_type}"
                    if shared_key not in seen_shared:
                        connections.append({
                            "shared_entity": cle.to_entity_type,
                            "entity_type": cle.to_entity_type,
                            "edge_type": cle.edge_type,
                            "propagation_weight": cle.propagation_weight,
                            "description": cle.description,
                            "layers": [layer.layer_id],
                        })
                        seen_shared.add(shared_key)

        return connections

    # ------------------------------------------------------------------
    # Internal: layer discovery
    # ------------------------------------------------------------------

    def _discover_layers(self) -> List:
        """Attempt to load layers via plugin discovery (best-effort)."""
        try:
            from contextsynapse.plugins.loader import discover_plugins
            plugins = discover_plugins()
            # Look for plugins that expose context layers
            layers = []
            for plugin in plugins:
                if hasattr(plugin, "get_context_layers"):
                    layers.extend(plugin.get_context_layers())
            return layers
        except Exception as exc:
            logger.debug("No layers discovered via plugins: %s", exc)
            return []


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _slugify(text: str) -> str:
    """Convert text to a URL/ID-safe slug."""
    slug = text.lower().strip()
    slug = re.sub(r"[^a-z0-9]+", "_", slug)
    slug = slug.strip("_")
    return slug or "entity"


def _normalize_list(items) -> List:
    """Ensure items is a list of dicts or strings."""
    if not isinstance(items, list):
        return [items] if items else []
    result = []
    for item in items:
        if isinstance(item, (str, dict)):
            result.append(item)
        else:
            result.append(str(item))
    return result


def _merge_dicts(
    target: Dict[str, Any],
    source: Dict[str, Any],
    source_label: str = "",
) -> None:
    """Merge source into target, extending lists instead of overwriting."""
    for key, value in source.items():
        if key in target:
            existing = target[key]
            if isinstance(existing, list) and isinstance(value, list):
                # Extend lists, avoiding exact duplicates
                existing_set = {
                    json.dumps(v, sort_keys=True) if isinstance(v, dict) else str(v)
                    for v in existing
                }
                for v in value:
                    v_key = json.dumps(v, sort_keys=True) if isinstance(v, dict) else str(v)
                    if v_key not in existing_set:
                        existing.append(v)
            elif isinstance(existing, dict) and isinstance(value, dict):
                _merge_dicts(existing, value, source_label)
            # For scalar values, keep the existing value (first layer wins)
        else:
            target[key] = value

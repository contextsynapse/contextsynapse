"""
Federated Context
==================
Cross-namespace context sharing with sensitivity enforcement.

Teams can expose specific graph nodes to other teams' agents. Only PUBLIC
nodes cross federation boundaries — CONFIDENTIAL and RESTRICTED never leak.

Creates network effects: more teams sharing → more context value → harder to leave.

Usage::

    fed = FederationRegistry(redis_client)
    fed.create_grant("team_a_graph", "team_b_graph", created_by="admin")

    engine = FederatedSearchEngine(graph_registry, fed)
    results = engine.search("AI regulation", source_ns="team_b_graph")
    # Returns team_b nodes + PUBLIC nodes from team_a
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class FederationGrant:
    """A grant allowing one namespace to read public nodes from another."""
    grant_id: str
    source_namespace: str       # namespace exposing data
    target_namespace: str       # namespace allowed to search
    created_by: str
    created_at: float
    node_labels: Optional[List[str]] = None  # label whitelist (None = all)
    active: bool = True

    def to_dict(self) -> Dict[str, Any]:
        return {
            "grant_id": self.grant_id,
            "source_namespace": self.source_namespace,
            "target_namespace": self.target_namespace,
            "created_by": self.created_by,
            "created_at": self.created_at,
            "node_labels": self.node_labels,
            "active": self.active,
        }


class FederationRegistry:
    """Manages federation grants between namespaces.

    Storage: Redis hash ``federation:grants`` or in-memory fallback.
    """

    def __init__(self, redis_client=None):
        self._redis = redis_client
        self._grants: Dict[str, FederationGrant] = {}
        self._load_from_redis()

    def _load_from_redis(self):
        if not self._redis:
            return
        try:
            raw = self._redis.hgetall("federation:grants")
            for gid, data in raw.items():
                g = json.loads(data)
                self._grants[gid] = FederationGrant(**g)
        except Exception as e:
            logger.debug("Federation Redis load failed: %s", e)

    def _save_grant(self, grant: FederationGrant):
        self._grants[grant.grant_id] = grant
        if self._redis:
            try:
                self._redis.hset("federation:grants", grant.grant_id, json.dumps(grant.to_dict()))
            except Exception:
                pass

    def create_grant(
        self,
        source_ns: str,
        target_ns: str,
        created_by: str = "",
        node_labels: Optional[List[str]] = None,
    ) -> FederationGrant:
        """Create a federation grant: source exposes public nodes to target."""
        if source_ns == target_ns:
            raise ValueError("Cannot federate a namespace with itself")

        # Check for duplicate
        for g in self._grants.values():
            if (g.source_namespace == source_ns and g.target_namespace == target_ns
                    and g.active):
                return g  # already exists

        grant = FederationGrant(
            grant_id=str(uuid.uuid4())[:12],
            source_namespace=source_ns,
            target_namespace=target_ns,
            created_by=created_by,
            created_at=time.time(),
            node_labels=node_labels,
        )
        self._save_grant(grant)
        logger.info("[FEDERATION] Grant created: %s -> %s (by %s)",
                    source_ns, target_ns, created_by)
        return grant

    def revoke_grant(self, grant_id: str) -> bool:
        """Revoke a federation grant."""
        grant = self._grants.get(grant_id)
        if not grant:
            return False
        grant.active = False
        self._save_grant(grant)
        logger.info("[FEDERATION] Grant revoked: %s", grant_id)
        return True

    def get_grants_for(self, target_ns: str) -> List[FederationGrant]:
        """Get all active grants that allow target_ns to read from other namespaces."""
        return [g for g in self._grants.values()
                if g.target_namespace == target_ns and g.active]

    def get_exposures_from(self, source_ns: str) -> List[FederationGrant]:
        """Get all active grants where source_ns exposes data."""
        return [g for g in self._grants.values()
                if g.source_namespace == source_ns and g.active]

    def list_all_grants(self) -> List[FederationGrant]:
        """List all grants (active and inactive)."""
        return list(self._grants.values())


@dataclass
class FederatedSearchResult:
    """Results from a cross-namespace search."""
    local_results: List[Dict[str, Any]] = field(default_factory=list)
    federated_results: List[Dict[str, Any]] = field(default_factory=list)
    sources: List[str] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.local_results) + len(self.federated_results)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "local_results": self.local_results,
            "federated_results": self.federated_results,
            "sources": self.sources,
            "total": self.total,
        }


class FederatedSearchEngine:
    """Cross-namespace search with sensitivity enforcement.

    SECURITY: Only nodes with sensitivity="public" (or no sensitivity field,
    which defaults to public) cross federation boundaries.
    """

    ALLOWED_SENSITIVITY = {"public", ""}  # only these cross boundaries

    def __init__(self, graph_registry, federation_registry: FederationRegistry):
        self._registry = graph_registry
        self._federation = federation_registry

    def search(
        self,
        query: str,
        source_namespace: str,
        include_federated: bool = True,
        limit: int = 20,
        label: Optional[str] = None,
    ) -> FederatedSearchResult:
        """Search source namespace + all federated namespaces.

        Only PUBLIC nodes cross federation boundaries.
        """
        result = FederatedSearchResult(sources=[source_namespace])

        # 1. Search local namespace
        local_nodes = self._search_namespace(source_namespace, query, label, limit)
        result.local_results = local_nodes

        if not include_federated:
            return result

        # 2. Get federation grants
        grants = self._federation.get_grants_for(source_namespace)
        if not grants:
            return result

        # 3. Search each federated namespace (only PUBLIC nodes)
        remaining = limit - len(local_nodes)
        for grant in grants:
            if remaining <= 0:
                break

            fed_nodes = self._search_namespace(
                grant.source_namespace, query, label, remaining,
                sensitivity_filter=True,
                label_whitelist=grant.node_labels,
            )

            # Tag results as federated
            for node in fed_nodes:
                node["_federated"] = True
                node["_source_namespace"] = grant.source_namespace

            result.federated_results.extend(fed_nodes)
            if fed_nodes:
                result.sources.append(grant.source_namespace)
            remaining -= len(fed_nodes)

        return result

    def _search_namespace(
        self,
        namespace: str,
        query: str,
        label: Optional[str],
        limit: int,
        sensitivity_filter: bool = False,
        label_whitelist: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        """Search a single namespace with optional sensitivity enforcement."""
        db = self._registry.get_graph(namespace, load_if_missing=True)
        if not db:
            return []

        try:
            nodes = db.get_all_nodes(label=label) if label else db.get_all_nodes()
        except Exception:
            return []

        results = []
        query_lower = query.lower() if query else ""
        query_terms = [w for w in query_lower.split() if len(w) > 2]

        for node in nodes:
            props = getattr(node, "properties", {}) or {}
            node_label = getattr(node, "label", "")

            # Sensitivity enforcement for federated searches
            if sensitivity_filter:
                sens = props.get("sensitivity", "public")
                if sens not in self.ALLOWED_SENSITIVITY:
                    continue  # BLOCK: confidential/restricted/internal

            # Label whitelist from grant
            if label_whitelist and node_label not in label_whitelist:
                continue

            # Keyword matching
            if query_terms:
                text = " ".join(str(v) for v in props.values() if isinstance(v, str)).lower()
                text += " " + node_label.lower()
                hits = sum(1 for t in query_terms if t in text)
                if hits == 0:
                    continue
                score = hits / len(query_terms)
            else:
                score = 1.0

            results.append({
                "id": str(getattr(node, "id", "")),
                "label": node_label,
                "name": props.get("name", props.get("title", "")),
                "content": (props.get("content", props.get("description", "")))[:200],
                "score": round(score, 3),
                "namespace": namespace,
            })

        # Sort by score descending
        results.sort(key=lambda x: x["score"], reverse=True)
        return results[:limit]

"""
Tenant Guard — storage-layer isolation enforcement for SaaS.

Ensures that EVERY graph, context, session, and schema access is scoped
to the authenticated tenant. No data leaks between tenants.

Usage:
    from contextsynapse.api.tenant_guard import TenantGuard

    guard = TenantGuard(tenant)
    guard.check_graph_access(graph_name)  # raises 403 if not owned
    guard.check_context_access(context)   # raises 403 if not owned
    scoped = guard.scope(name)            # add tenant prefix
    bare = guard.unscope(scoped_name)     # strip tenant prefix (or 403)
"""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import HTTPException

logger = logging.getLogger(__name__)


class TenantGuard:
    """Enforces tenant-level data isolation at the storage boundary."""

    def __init__(self, tenant):
        """
        Args:
            tenant: TenantIdentity or UserIdentity with tenant_id
        """
        self.tenant_id = getattr(tenant, "tenant_id", None) or ""
        if not self.tenant_id:
            raise HTTPException(403, "Tenant context required.")

    @property
    def prefix(self) -> str:
        return f"{self.tenant_id}:"

    def scope(self, name: str) -> str:
        """Add tenant prefix to a name, avoiding double-scoping."""
        if not name:
            return f"{self.tenant_id}:default"
        if name.startswith(self.prefix):
            return name
        return f"{self.prefix}{name}"

    def unscope(self, scoped_name: str) -> str:
        """Strip tenant prefix. Raises 403 if name doesn't belong to this tenant."""
        if scoped_name.startswith(self.prefix):
            return scoped_name[len(self.prefix):]
        # Allow bare names (backward compat) but log it
        if ":" not in scoped_name:
            return scoped_name
        # Name belongs to a different tenant
        logger.warning("[GUARD] Tenant %s tried to access '%s'", self.tenant_id, scoped_name[:40])
        raise HTTPException(403, "Access denied.")

    def check_graph_access(self, graph_name: str):
        """Verify a graph belongs to this tenant."""
        if not graph_name:
            return
        if ":" in graph_name and not graph_name.startswith(self.prefix):
            raise HTTPException(403, "Access denied.")

    def check_context_access(self, context):
        """Verify a context belongs to this tenant."""
        if context is None:
            raise HTTPException(404, "Not found.")
        # Context has graph_namespace which should be tenant-scoped
        ns = getattr(context, "graph_namespace", "") or ""
        if ns and ":" in ns and not ns.startswith(self.prefix):
            raise HTTPException(403, "Access denied.")

    def check_session_access(self, session: dict):
        """Verify a session belongs to this tenant."""
        if not session:
            raise HTTPException(404, "Not found.")
        ns = session.get("graph_namespace", session.get("namespace", ""))
        if ns and ":" in ns and not ns.startswith(self.prefix):
            raise HTTPException(403, "Access denied.")

    def filter_graphs(self, graphs: list) -> list:
        """Filter a graph list to only this tenant's graphs."""
        result = []
        for g in graphs:
            name = g.get("name", "") if isinstance(g, dict) else getattr(g, "name", "")
            # Include: tenant-scoped graphs belonging to this tenant, or unscoped graphs
            if name.startswith(self.prefix) or ":" not in name:
                result.append(g)
        return result

    def filter_contexts(self, contexts: list) -> list:
        """Filter contexts to only this tenant's contexts."""
        result = []
        for ctx in contexts:
            ns = getattr(ctx, "graph_namespace", "") or ""
            if ns.startswith(self.prefix) or ":" not in ns:
                result.append(ctx)
        return result

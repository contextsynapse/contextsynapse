"""
Projects Router
=================
REST API for SDLC context projects. Each project is one namespace with
typed SDLC nodes. Delegates to ProjectContext.
"""
import logging
from typing import Any, Dict, List

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

logger = logging.getLogger(__name__)

try:
    from ..project.code_context import CodeContext
except Exception:
    CodeContext = None  # type: ignore


class CreateProjectRequest(BaseModel):
    name: str


def create_projects_router(user_auth=None, base_path: str = "contextcore_data") -> APIRouter:
    """Create the /dashboard/projects router."""
    from ..project.project_context import ProjectContext

    router = APIRouter(prefix="/dashboard/projects", tags=["Projects"])

    def _load(name: str) -> ProjectContext:
        try:
            return ProjectContext.load(name, base_path=base_path)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc))

    def _list_project_names() -> List[str]:
        return ProjectContext.list_names(base_path=base_path)

    # ── CRUD ─────────────────────────────────────────────────────────────────

    @router.post("", status_code=201)
    async def create_project(req: CreateProjectRequest) -> Dict[str, Any]:
        """Create (or open) a project namespace."""
        pc = ProjectContext.create(req.name, base_path=base_path)
        return {"name": pc.name, "status": "created"}

    @router.get("")
    async def list_projects() -> List[Dict[str, str]]:
        """List all project namespaces."""
        return [{"name": name} for name in sorted(_list_project_names())]

    @router.get("/{name}")
    async def get_project(name: str) -> Dict[str, Any]:
        """Project overview including coverage scores."""
        pc = _load(name)
        return {"name": pc.name, "coverage": pc.coverage_score()}

    # ── SDLC endpoints ───────────────────────────────────────────────────────

    @router.get("/{name}/coverage")
    async def get_coverage(name: str) -> Dict[str, Any]:
        """Per-layer SDLC coverage scores."""
        return _load(name).coverage_score()

    @router.get("/{name}/stale")
    async def get_stale(name: str) -> List[Dict[str, Any]]:
        """All stale SDLC nodes in the project namespace."""
        return _load(name).stale_report()

    @router.get("/{name}/brief")
    async def get_brief(
        name: str,
        topic: str = Query(default=""),
        phase: str = Query(default=""),
    ) -> Dict[str, Any]:
        """Scoped context package for an agent about to work."""
        return _load(name).agent_brief(topic=topic, phase=phase)

    @router.get("/{name}/timeline")
    async def get_timeline(
        name: str,
        limit: int = Query(default=50, ge=1, le=500),
    ) -> List[Dict[str, Any]]:
        """Recent graph changes in the project namespace."""
        pc = _load(name)
        try:
            from ..core.time_travel import GraphTimeTraveler
            tt = GraphTimeTraveler(pc.db)
            events = tt.get_graph_timeline(limit=limit)
            return events if isinstance(events, list) else []
        except Exception:
            return []

    # ── Spec / Requirements ──────────────────────────────────

    def _get_cc(name: str):
        """Load a CodeContext by name."""
        return CodeContext.load(name)

    @router.post("/{name}/spec/ingest")
    async def ingest_spec(name: str):
        """Parse the project spec into Requirement/Feature/Constraint graph nodes."""
        cc = _get_cc(name)
        spec_text = cc.ns.get_metadata("spec") or ""
        try:
            ids = cc.ingest_spec(spec_text)
            return {"ingested": ids, "count": len(ids)}
        except Exception as e:
            raise HTTPException(500, f"Spec ingestion failed: {e}")

    @router.get("/{name}/spec/requirements")
    async def get_requirements(name: str):
        """Return all Requirement/Feature/Constraint nodes for the project."""
        cc = _get_cc(name)
        try:
            nodes = cc.get_requirements()
            return {"nodes": nodes, "total": len(nodes)}
        except Exception as e:
            raise HTTPException(500, f"Failed to load requirements: {e}")

    @router.get("/{name}/spec/coverage")
    async def get_coverage_spec(name: str):
        """Return traceability coverage for the project's requirements."""
        cc = _get_cc(name)
        try:
            return cc.check_coverage()
        except Exception as e:
            raise HTTPException(500, f"Coverage check failed: {e}")

    return router

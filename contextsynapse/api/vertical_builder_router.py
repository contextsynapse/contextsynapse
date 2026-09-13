"""Vertical Builder API — create plugin verticals via REST/UI.

This is the backend for the Vertical Builder UI page.
Generates the same boilerplate as `contextcore create-vertical` CLI.
"""
from __future__ import annotations

import json
import logging
import shutil
import tempfile
import zipfile
from io import BytesIO
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


# ── Request/Response Models ──

class NodeTypeProperty(BaseModel):
    name: str
    type: str = "string"  # string, int, float, bool, datetime, list
    required: bool = False
    enum: Optional[List[str]] = None

class NodeTypeDef(BaseModel):
    name: str
    description: str = ""
    properties: List[NodeTypeProperty] = []

class EdgeTypeDef(BaseModel):
    name: str
    source: str
    target: str
    description: str = ""

class SensorDef(BaseModel):
    name: str
    interval_seconds: int = 300
    source_type: str = "api"  # api, rss, database, file, webhook
    source_url: str = ""
    description: str = ""

class RouteDef(BaseModel):
    method: str = "GET"  # GET, POST, PUT, DELETE
    path: str
    description: str = ""

class VerticalCreateRequest(BaseModel):
    name: str = Field(..., min_length=2, max_length=50, pattern=r'^[a-zA-Z][a-zA-Z0-9_ -]*$')
    description: str = ""
    author: str = "ContextCore Developer"
    node_types: List[NodeTypeDef] = []
    edge_types: List[EdgeTypeDef] = []
    sensors: List[SensorDef] = []
    routes: List[RouteDef] = []

class VerticalPreviewResponse(BaseModel):
    project_name: str
    package_name: str
    files: dict  # filename -> content preview


def create_vertical_builder_router() -> APIRouter:
    router = APIRouter(prefix="/vertical-builder", tags=["vertical-builder"])

    @router.post("/preview")
    async def preview_vertical(req: VerticalCreateRequest) -> VerticalPreviewResponse:
        """Preview what files will be generated without creating them."""
        import re
        slug = re.sub(r"[^a-z0-9_]", "_", req.name.lower().strip())
        pkg_name = f"contextcore_{slug}"
        project_name = f"contextcore-{slug}"

        files = {
            "pyproject.toml": f"[project]\nname = \"{project_name}\"\nversion = \"0.1.0\"\n...",
            f"{pkg_name}/plugin.py": f"class {req.name.title().replace(' ', '')}Vertical(VerticalPlugin):\n    name = \"{slug}\"\n    ...",
            f"{pkg_name}/schemas.py": f"# {len(req.node_types)} node types, {len(req.edge_types)} edge types",
            f"{pkg_name}/sensors/": f"{len(req.sensors)} sensor(s) configured",
            f"{pkg_name}/api/routes.py": f"{len(req.routes)} custom route(s)",
            "tests/test_plugin.py": "5 auto-generated tests",
            "README.md": f"# {project_name}\n{req.description}",
        }

        return VerticalPreviewResponse(
            project_name=project_name,
            package_name=pkg_name,
            files=files,
        )

    @router.post("/generate")
    async def generate_vertical(req: VerticalCreateRequest):
        """Generate a vertical plugin and return as a downloadable zip."""
        import os
        os.environ.setdefault("PYTHONUTF8", "1")
        from contextsynapse.cli.scaffold import scaffold_vertical
        import re

        slug = re.sub(r"[^a-z0-9_]", "_", req.name.lower().strip())

        # Generate into a temp directory
        with tempfile.TemporaryDirectory() as tmpdir:
            try:
                project_dir = scaffold_vertical(
                    name=req.name,
                    output_dir=tmpdir,
                    author=req.author,
                    description=req.description or f"{req.name.title()} vertical for ContextCore",
                )
            except FileExistsError:
                raise HTTPException(409, f"Vertical '{req.name}' already exists")

            pkg_name = f"contextcore_{slug}"
            display = req.name.title().replace(" ", "")

            # Override schemas.py with user-defined types
            if req.node_types or req.edge_types:
                _write_custom_schemas(
                    project_dir / pkg_name / "schemas.py",
                    slug, display, req.node_types, req.edge_types,
                )

            # Override sensors with user-defined sensors
            if req.sensors:
                _write_custom_sensors(
                    project_dir / pkg_name / "sensors",
                    slug, display, req.sensors,
                )

            # Override routes with user-defined routes
            if req.routes:
                _write_custom_routes(
                    project_dir / pkg_name / "api" / "routes.py",
                    slug, req.routes,
                )

            # Regenerate plugin.py to reference custom sensors
            if req.sensors:
                _write_custom_plugin(
                    project_dir / pkg_name / "plugin.py",
                    slug, display, req, pkg_name,
                )

            # Create zip
            zip_buffer = BytesIO()
            with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
                for file_path in project_dir.rglob("*"):
                    if file_path.is_file():
                        arcname = file_path.relative_to(project_dir.parent)
                        zf.write(file_path, arcname)

            zip_buffer.seek(0)
            return StreamingResponse(
                zip_buffer,
                media_type="application/zip",
                headers={
                    "Content-Disposition": f"attachment; filename=contextcore-{slug}.zip"
                },
            )

    @router.get("/templates")
    async def list_templates():
        """List available vertical templates for quick start."""
        return {
            "templates": [
                {
                    "id": "blank",
                    "name": "Blank Vertical",
                    "description": "Empty vertical with example schema and sensor",
                    "node_types": 1,
                    "sensors": 1,
                },
                {
                    "id": "data-feed",
                    "name": "Data Feed",
                    "description": "Vertical focused on ingesting external data (API, RSS, webhooks)",
                    "node_types": 3,
                    "sensors": 2,
                },
                {
                    "id": "document-analysis",
                    "name": "Document Analysis",
                    "description": "Vertical for processing and analyzing documents",
                    "node_types": 4,
                    "sensors": 1,
                },
                {
                    "id": "monitoring",
                    "name": "Monitoring & Alerts",
                    "description": "Vertical for monitoring systems with alert thresholds",
                    "node_types": 3,
                    "sensors": 3,
                },
            ]
        }

    return router


# ── Code generation helpers ──

def _write_custom_schemas(path: Path, slug: str, display: str,
                          node_types: List[NodeTypeDef], edge_types: List[EdgeTypeDef]):
    """Generate schemas.py from user-defined types."""
    nt_code = "    node_types={\n"
    for nt in node_types:
        props = {}
        for p in nt.properties:
            prop_def = {"type": p.type}
            if p.required:
                prop_def["required"] = True
            if p.enum:
                prop_def["enum"] = p.enum
            props[p.name] = prop_def
        nt_code += f'        "{nt.name}": {{\n'
        nt_code += f'            "properties": {json.dumps(props, indent=12)},\n'
        nt_code += f'            "description": "{nt.description}",\n'
        nt_code += f'        }},\n'
    nt_code += "    },\n"

    et_code = "    edge_types={\n"
    for et in edge_types:
        et_code += f'        "{et.name}": {{\n'
        et_code += f'            "source": "{et.source}",\n'
        et_code += f'            "target": "{et.target}",\n'
        et_code += f'            "description": "{et.description}",\n'
        et_code += f'        }},\n'
    et_code += "    },\n"

    content = f'''"""Graph schemas for the {display} vertical."""
from contextsynapse.plugins import PluginSchema

{slug.upper()}_SCHEMA = PluginSchema(
    name="{slug}",
    version="1.0",
    description="{display} domain schema",
{nt_code}{et_code})
'''
    path.write_text(content, encoding="utf-8")


def _write_custom_sensors(sensors_dir: Path, slug: str, display: str, sensors: List[SensorDef]):
    """Generate sensor files from user definitions."""
    for sensor in sensors:
        sname = sensor.name.lower().replace(" ", "_").replace("-", "_")
        classname = "".join(w.title() for w in sname.split("_")) + "Sensor"

        content = f'''"""Sensor: {sensor.name} — {sensor.description}"""
from __future__ import annotations
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List
from contextsynapse.plugins import Sensor

logger = logging.getLogger(__name__)


class {classname}(Sensor):
    """{sensor.description or sensor.name}"""
    name = "{sname}"
    interval_seconds = {sensor.interval_seconds}

    async def collect(self, db: Any) -> List[Dict[str, Any]]:
        """Collect data from {sensor.source_type} source."""
        now = datetime.now(timezone.utc).isoformat()
        # TODO: Implement data collection from: {sensor.source_url or 'your source'}
        #
        # For {sensor.source_type} sources:
'''
        if sensor.source_type == "api":
            content += '''        # import aiohttp
        # async with aiohttp.ClientSession() as session:
        #     async with session.get("YOUR_API_URL") as resp:
        #         data = await resp.json()
'''
        elif sensor.source_type == "rss":
            content += '''        # import feedparser
        # feed = feedparser.parse("YOUR_RSS_URL")
        # for entry in feed.entries[:20]:
        #     results.append({"id": entry.id, "type": "Article", ...})
'''
        elif sensor.source_type == "database":
            content += '''        # import asyncpg  # or your database driver
        # conn = await asyncpg.connect("YOUR_DB_URL")
        # rows = await conn.fetch("SELECT * FROM your_table WHERE updated > $1", since)
'''

        content += f'''        return [
            {{
                "id": f"{sname}_{{now[:10]}}",
                "type": "{display}Item",
                "properties": {{"name": "Example", "collected_at": now}},
            }},
        ]
'''
        (sensors_dir / f"{sname}.py").write_text(content, encoding="utf-8")


def _write_custom_routes(path: Path, slug: str, routes: List[RouteDef]):
    """Generate routes.py from user-defined routes."""
    content = f'''"""API routes for the {slug} vertical."""
from fastapi import APIRouter


def create_{slug}_router() -> APIRouter:
    router = APIRouter()

'''
    for route in routes:
        method = route.method.lower()
        func_name = route.path.strip("/").replace("/", "_").replace("{", "").replace("}", "") or "root"
        content += f'''    @router.{method}("{route.path}")
    async def {func_name}():
        """{route.description or route.path}"""
        # TODO: Implement
        return {{"message": "Implement {route.path}"}}

'''
    content += "    return router\n"
    path.write_text(content, encoding="utf-8")


def _write_custom_plugin(path: Path, slug: str, display: str,
                         req: VerticalCreateRequest, pkg_name: str):
    """Regenerate plugin.py to reference all custom sensors."""
    sensor_imports = []
    sensor_instances = []
    for sensor in req.sensors:
        sname = sensor.name.lower().replace(" ", "_").replace("-", "_")
        classname = "".join(w.title() for w in sname.split("_")) + "Sensor"
        sensor_imports.append(f"from {pkg_name}.sensors.{sname} import {classname}")
        sensor_instances.append(f"            {classname}(),")

    imports = "\n".join(sensor_imports)
    instances = "\n".join(sensor_instances)

    content = f'''"""{display} Vertical Plugin for ContextCore."""
from __future__ import annotations
import logging
from typing import Any, Dict, List
from contextsynapse.plugins import DashboardCard, PluginRoute, PluginSchema, Sensor, VerticalPlugin
from {pkg_name}.schemas import {slug.upper()}_SCHEMA
{imports}

logger = logging.getLogger(__name__)


class {display}Vertical(VerticalPlugin):
    """{display} vertical for ContextCore."""
    name = "{slug}"
    version = "0.1.0"
    description = "{req.description}"
    author = "{req.author}"

    def schemas(self) -> List[PluginSchema]:
        return [{slug.upper()}_SCHEMA]

    def sensors(self) -> List[Sensor]:
        return [
{instances}
        ]

    def api_routers(self) -> List[PluginRoute]:
        from {pkg_name}.api.routes import create_{slug}_router
        return [PluginRoute(router=create_{slug}_router(), prefix="/{slug}", tags=["{slug}"])]

    def dashboard_cards(self) -> List[DashboardCard]:
        return [
            DashboardCard(
                id="{slug}-overview", title="{display}",
                component_path="{slug}/OverviewPage.js",
                route="/dashboard/{slug}", icon="box",
                category="{slug}", order=50,
            ),
        ]

    async def on_startup(self, db: Any) -> None:
        await super().on_startup(db)
        logger.info("{display} vertical started")
'''
    path.write_text(content, encoding="utf-8")

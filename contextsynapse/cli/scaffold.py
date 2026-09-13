"""Vertical scaffolder -- generates plugin boilerplate from templates.

Usage:
    contextcore create-vertical legal
    contextcore create-vertical legal --author "Your Name" --output ./my-verticals
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Optional


def _write(path: Path, content: str):
    """Write text file with UTF-8 encoding (avoids Windows cp1252 issues)."""
    path.write_text(content, encoding="utf-8")


def scaffold_vertical(
    name: str,
    output_dir: Optional[str] = None,
    author: str = "ContextCore Developer",
    description: str = "",
) -> Path:
    """Generate a complete vertical plugin project.

    Args:
        name: Vertical name (e.g. "legal", "healthcare", "logistics")
        output_dir: Where to create the project (default: current dir)
        author: Author name for pyproject.toml
        description: Plugin description

    Returns:
        Path to the created project directory
    """
    # Normalize name
    slug = re.sub(r"[^a-z0-9_]", "_", name.lower().strip())
    pkg_name = f"contextcore_{slug}"
    project_name = f"contextcore-{slug}"
    display_name = name.strip().title()

    if not description:
        description = f"{display_name} vertical plugin for ContextCore"

    # Create project root
    base = Path(output_dir or ".") / project_name
    if base.exists():
        raise FileExistsError(f"Directory already exists: {base}")

    pkg_dir = base / pkg_name
    sensors_dir = pkg_dir / "sensors"
    api_dir = pkg_dir / "api"
    tests_dir = base / "tests"

    for d in [pkg_dir, sensors_dir, api_dir, tests_dir]:
        d.mkdir(parents=True)

    # -- pyproject.toml --
    _write(base / "pyproject.toml", f'''[build-system]
requires = ["setuptools>=68.0"]
build-backend = "setuptools.build_meta"

[project]
name = "{project_name}"
version = "0.1.0"
description = "{description}"
requires-python = ">=3.10"
authors = [{{name = "{author}"}}]
dependencies = ["contextcore>=1.0.0"]

[project.entry-points."contextsynapse.plugins"]
{slug} = "{pkg_name}.plugin:{display_name.replace(' ', '')}Vertical"
''')

    # -- __init__.py --
    _write(pkg_dir / "__init__.py", 
        f'"""ContextCore {display_name} Vertical Plugin."""\n'
    )

    # -- schemas.py --
    _write(pkg_dir / "schemas.py", f'''"""Graph schemas for the {display_name} vertical.

Define your domain-specific node types, edge types, and property constraints.
These schemas drive ingestion validation, query optimization, and documentation.
"""
from contextsynapse.plugins import PluginSchema

{slug.upper()}_SCHEMA = PluginSchema(
    name="{slug}",
    version="1.0",
    description="{display_name} domain schema",
    node_types={{
        # Define your node types here. Example:
        #
        # "Document": {{
        #     "properties": {{
        #         "title": {{"type": "string", "required": True}},
        #         "status": {{"type": "string", "enum": ["draft", "active", "archived"]}},
        #         "created_at": {{"type": "datetime"}},
        #     }},
        #     "description": "A document in the {slug} domain",
        # }},

        "{display_name.replace(' ', '')}Item": {{
            "properties": {{
                "name": {{"type": "string", "required": True}},
                "status": {{"type": "string"}},
                "category": {{"type": "string"}},
            }},
            "description": "A {slug} domain item",
        }},
    }},
    edge_types={{
        # Define relationships between your node types. Example:
        #
        # "BELONGS_TO": {{
        #     "source": "Document",
        #     "target": "Category",
        #     "description": "Document belongs to a category",
        # }},

        "RELATED_TO": {{
            "source": "{display_name.replace(' ', '')}Item",
            "target": "{display_name.replace(' ', '')}Item",
            "description": "Items related to each other",
        }},
    }},
)
''')

    # -- sensors/__init__.py --
    _write(sensors_dir / "__init__.py", "")

    # -- sensors/example_sensor.py --
    _write(sensors_dir / "example_sensor.py", f'''"""Example sensor for the {display_name} vertical.

Sensors collect data from external sources on a schedule and return
node/edge dicts that are automatically upserted into the graph.

Replace this with your real data source (API, RSS, database, etc.).
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List

from contextsynapse.plugins import Sensor

logger = logging.getLogger(__name__)


class {display_name.replace(' ', '')}Sensor(Sensor):
    """{display_name} data feed.

    Replace the collect() method with your real data source logic.
    """

    name = "{slug}_feed"
    interval_seconds = 300  # every 5 minutes

    async def collect(self, db: Any) -> List[Dict[str, Any]]:
        """Collect data and return nodes/edges to upsert.

        Each dict should have:
            - id: str (deterministic for idempotent upsert)
            - type: str (matching a schema node_type)
            - properties: dict

        For edges, include source_id, target_id, edge_type.
        """
        now = datetime.now(timezone.utc).isoformat()

        # TODO: Replace with your real data source
        # Example: fetch from an API, parse RSS, query a database
        return [
            {{
                "id": f"{slug}_item_1",
                "type": "{display_name.replace(' ', '')}Item",
                "properties": {{
                    "name": "Example Item 1",
                    "status": "active",
                    "category": "general",
                    "collected_at": now,
                }},
            }},
            {{
                "id": f"{slug}_item_2",
                "type": "{display_name.replace(' ', '')}Item",
                "properties": {{
                    "name": "Example Item 2",
                    "status": "draft",
                    "category": "general",
                    "collected_at": now,
                }},
            }},
            # Edge example:
            {{
                "source_id": f"{slug}_item_1",
                "target_id": f"{slug}_item_2",
                "edge_type": "RELATED_TO",
                "properties": {{"reason": "example relationship"}},
            }},
        ]

    def decay_config(self):
        return {{"ttl_hours": 168, "decay_rate": 0.01}}  # 1 week TTL

    async def health_check(self):
        # TODO: Check if your data source is reachable
        return {{"healthy": True, "message": "Example sensor (replace with real check)", "latency_ms": 0.0}}
''')

    # -- api/__init__.py --
    _write(api_dir / "__init__.py", "")

    # -- api/routes.py --
    _write(api_dir / "routes.py", f'''"""API routes for the {display_name} vertical.

These routes are automatically mounted at /v1/{slug}/ by ContextCore.
Add your domain-specific endpoints here.
"""
from fastapi import APIRouter


def create_{slug}_router() -> APIRouter:
    router = APIRouter()

    @router.get("/items")
    async def list_items():
        """List all {slug} items in the graph."""
        # TODO: Query the graph for your domain nodes
        return {{"items": [], "message": "Implement graph query here"}}

    @router.get("/items/{{item_id}}")
    async def get_item(item_id: str):
        """Get a specific {slug} item by ID."""
        # TODO: Look up node in the graph
        return {{"id": item_id, "message": "Implement node lookup here"}}

    @router.get("/stats")
    async def stats():
        """Get statistics for the {slug} vertical."""
        return {{
            "vertical": "{slug}",
            "status": "running",
            "message": "Implement domain statistics here",
        }}

    return router
''')

    # -- plugin.py --
    _write(pkg_dir / "plugin.py", f'''"""{display_name} Vertical Plugin for ContextCore.

This is the main plugin class that wires together schemas, sensors,
API routes, and dashboard cards for the {slug} domain.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List

from contextsynapse.plugins import (
    DashboardCard,
    PluginRoute,
    PluginSchema,
    Sensor,
    VerticalPlugin,
)

from {pkg_name}.schemas import {slug.upper()}_SCHEMA
from {pkg_name}.sensors.example_sensor import {display_name.replace(' ', '')}Sensor

logger = logging.getLogger(__name__)


class {display_name.replace(' ', '')}Vertical(VerticalPlugin):
    """{display_name} vertical for ContextCore."""

    name = "{slug}"
    version = "0.1.0"
    description = "{description}"
    author = "{author}"

    def schemas(self) -> List[PluginSchema]:
        return [{slug.upper()}_SCHEMA]

    def sensors(self) -> List[Sensor]:
        return [{display_name.replace(' ', '')}Sensor()]

    def api_routers(self) -> List[PluginRoute]:
        from {pkg_name}.api.routes import create_{slug}_router
        return [
            PluginRoute(
                router=create_{slug}_router(),
                prefix="/{slug}",
                tags=["{slug}"],
            )
        ]

    def dashboard_cards(self) -> List[DashboardCard]:
        return [
            DashboardCard(
                id="{slug}-overview",
                title="{display_name}",
                component_path="{slug}/OverviewPage.js",
                route="/dashboard/{slug}",
                icon="box",
                category="{slug}",
                description="{display_name} dashboard",
                order=50,
            ),
        ]

    async def on_startup(self, db: Any) -> None:
        await super().on_startup(db)
        logger.info("{display_name} vertical started")

    def on_graph_event(self, event_type: str, payload: Dict[str, Any]) -> None:
        # React to graph events relevant to your domain
        pass
''')

    # -- tests/__init__.py --
    _write(tests_dir / "__init__.py", "")

    # -- tests/test_plugin.py --
    _write(tests_dir / "test_plugin.py", f'''"""Basic tests for the {display_name} vertical plugin."""
from {pkg_name}.plugin import {display_name.replace(' ', '')}Vertical
from contextsynapse.plugins.loader import validate_plugin


def test_plugin_loads():
    """Test that the plugin can be instantiated."""
    plugin = {display_name.replace(' ', '')}Vertical()
    assert plugin.name == "{slug}"
    assert plugin.version == "0.1.0"


def test_plugin_validates():
    """Test that the plugin passes validation."""
    plugin = {display_name.replace(' ', '')}Vertical()
    issues = validate_plugin(plugin)
    assert len(issues) == 0, f"Validation issues: {{issues}}"


def test_plugin_has_schemas():
    plugin = {display_name.replace(' ', '')}Vertical()
    schemas = plugin.schemas()
    assert len(schemas) >= 1
    assert schemas[0].name == "{slug}"


def test_plugin_has_sensors():
    plugin = {display_name.replace(' ', '')}Vertical()
    sensors = plugin.sensors()
    assert len(sensors) >= 1
    assert sensors[0].name == "{slug}_feed"


def test_plugin_has_routes():
    plugin = {display_name.replace(' ', '')}Vertical()
    routes = plugin.api_routers()
    assert len(routes) >= 1
''')

    # -- README.md --
    _write(base / "README.md", f'''# {project_name}

{description}

## Install

```bash
pip install -e .
```

## Usage

Start the ContextCore server -- the plugin auto-discovers:

```bash
contextcore serve
# or: uvicorn contextsynapse.api.api:app --reload
```

Check that it loaded:
```bash
curl http://localhost:8000/plugins/
```

## Development

```bash
# Install in dev mode
pip install -e ".[dev]"

# Run tests
pytest tests/ -v

# Edit your schemas, sensors, and routes:
#   {pkg_name}/schemas.py      - define node/edge types
#   {pkg_name}/sensors/        - add data feeds
#   {pkg_name}/api/routes.py   - add REST endpoints
#   {pkg_name}/plugin.py       - wire everything together
```

## Structure

```
{pkg_name}/
|-- plugin.py           # Main plugin class
|-- schemas.py          # Graph schemas (node types, edge types)
|-- sensors/
|   +-- example_sensor.py  # Data feed (replace with your source)
+-- api/
    +-- routes.py       # REST endpoints (mounted at /v1/{slug}/)
```

## Query Your Data

```
FIND NODES WHERE label = '{display_name.replace(' ', '')}Item'
```
''')

    return base


# -- CLI integration --

def cli_create_vertical(args=None):
    """CLI entry point for `contextcore create-vertical`."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Create a new ContextCore vertical plugin"
    )
    parser.add_argument("name", help="Vertical name (e.g. legal, healthcare, logistics)")
    parser.add_argument("--author", default="ContextCore Developer", help="Author name")
    parser.add_argument("--description", default="", help="Plugin description")
    parser.add_argument("--output", default=".", help="Output directory")

    parsed = parser.parse_args(args)

    try:
        project_dir = scaffold_vertical(
            name=parsed.name,
            output_dir=parsed.output,
            author=parsed.author,
            description=parsed.description,
        )
        print(f"\n  Created vertical '{parsed.name}' at {project_dir}\n")
        print(f"  Next steps:")
        print(f"    1. cd {project_dir}")
        print(f"    2. Edit schemas.py -- define your node/edge types")
        print(f"    3. Edit sensors/ -- add your data feeds")
        print(f"    4. Edit api/routes.py -- add your endpoints")
        print(f"    5. pip install -e . && contextcore serve")
        print(f"    6. Visit http://localhost:8000/plugins/ to verify\n")
    except FileExistsError as e:
        print(f"Error: {e}")
        raise SystemExit(1)

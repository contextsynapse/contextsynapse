# Plugin Development Guide

Build domain-specific vertical applications on top of the ContextSynapse engine.

## Overview

ContextSynapse uses a plugin architecture that lets you extend the core graph database with:

- **Schemas** — Define domain-specific node and edge types
- **Sensors** — Scheduled data feeds that populate the graph
- **API Routes** — Custom REST endpoints mounted on the core server
- **MCP Tools** — Claude/Copilot integration
- **Dashboard Cards** — UI components injected into the frontend

## Quick Start

### 1. Create your plugin class

```python
from contextsynapse.plugins import VerticalPlugin, Sensor, PluginSchema, PluginRoute
from fastapi import APIRouter

class MyDataSensor(Sensor):
    name = "my_feed"
    interval_seconds = 300  # every 5 minutes

    async def collect(self, db):
        # Fetch data from your source
        # Return list of node/edge dicts
        return [
            {"id": "item_1", "type": "MyNode", "properties": {"value": 42}},
            {"source_id": "item_1", "target_id": "item_2", "edge_type": "RELATED"},
        ]

class MyVertical(VerticalPlugin):
    name = "my_domain"
    version = "0.1.0"
    description = "My domain-specific extension"

    def sensors(self):
        return [MyDataSensor()]

    def schemas(self):
        return [PluginSchema(
            name="my_schema",
            node_types={"MyNode": {"properties": {"value": {"type": "int"}}}},
        )]

    def api_routers(self):
        router = APIRouter(tags=["my_domain"])
        @router.get("/status")
        async def status():
            return {"status": "ok"}
        return [PluginRoute(router=router, prefix="/my_domain")]
```

### 2. Register via entry points

In your `pyproject.toml`:

```toml
[project]
name = "contextsynapse-my-domain"
dependencies = ["contextsynapse>=1.0.0"]

[project.entry-points."contextsynapse.plugins"]
my_domain = "my_package.plugin:MyVertical"
```

### 3. Install and run

```bash
pip install -e .
uvicorn contextsynapse.api.api:app --reload
```

The plugin is auto-discovered at startup. Check `/plugins/` to verify.

## Plugin Lifecycle

```
Discovery (entry points) → Validation → on_startup(db) → Sensors scheduled → Running
                                                                              ↓
                                                                         on_shutdown()
```

1. **Discovery**: `discover_plugins()` finds all installed entry points in the `contextsynapse.plugins` group
2. **Validation**: Plugin name, sensor intervals, and schema names are checked
3. **Startup**: `on_startup(db)` is called with the graph registry — register schemas, seed data
4. **Sensors**: Each sensor's `collect()` runs immediately, then on its `interval_seconds` schedule
5. **Shutdown**: `on_shutdown()` is called — clean up resources

## Extension Points

### Schemas (`PluginSchema`)

Define the graph structure for your domain:

```python
PluginSchema(
    name="finance",
    version="1.0",
    node_types={
        "Stock": {"properties": {"ticker": {"type": "string", "required": True}}},
        "PricePoint": {"properties": {"price": {"type": "float"}, "timestamp": {"type": "datetime"}}},
    },
    edge_types={
        "HAS_PRICE": {"source": "Stock", "target": "PricePoint"},
    },
)
```

### Sensors (`Sensor`)

Data feeds that run on a schedule:

```python
class MySensor(Sensor):
    name = "my_sensor"           # unique within plugin
    interval_seconds = 600       # collection interval (min 10s)
    batch_size = 1000            # max items per collection
    enabled = True               # toggle on/off

    async def collect(self, db):
        """Return nodes/edges to upsert."""
        ...

    def decay_config(self):
        """Optional: configure data staleness."""
        return {"ttl_hours": 24, "decay_rate": 0.1}

    async def health_check(self):
        """Optional: check if data source is reachable."""
        return {"healthy": True, "message": "ok"}
```

### API Routes (`PluginRoute`)

Custom FastAPI endpoints:

```python
def api_routers(self):
    router = APIRouter()
    @router.get("/items")
    async def list_items():
        return {"items": [...]}
    return [PluginRoute(router=router, prefix="/my_domain", tags=["my_domain"])]
```

Routes are mounted at `/v1/{prefix}/`.

### MCP Tools (`PluginMCPTool`)

Expose tools to Claude/Copilot:

```python
def mcp_tools(self):
    return [PluginMCPTool(
        name="lookup_item",
        description="Look up an item by ID",
        input_schema={"type": "object", "properties": {"id": {"type": "string"}}},
        handler=lambda id: {"item": id, "status": "found"},
    )]
```

### Dashboard Cards (`DashboardCard`)

Inject UI into the frontend:

```python
def dashboard_cards(self):
    return [DashboardCard(
        id="my-overview",
        title="My Dashboard",
        component_path="my_domain/Overview.js",
        route="/dashboard/my-domain",
        icon="bar-chart",
        category="monitoring",
    )]
```

### Graph Events

React to graph changes:

```python
def on_graph_event(self, event_type, payload):
    if event_type == "node_added" and payload.get("label") == "Alert":
        logger.warning("New alert: %s", payload)
```

## Example

See [examples/weather_vertical/](../examples/weather_vertical/) for a complete working example.

## API Endpoints

The plugin system exposes these management endpoints:

| Endpoint | Description |
|----------|-------------|
| `GET /plugins/` | List all plugins and status |
| `GET /plugins/schemas` | All schemas from all plugins |
| `GET /plugins/dashboard/cards` | All dashboard cards |
| `GET /plugins/{name}` | Plugin details |
| `GET /plugins/{name}/sensors` | Sensor status and health |

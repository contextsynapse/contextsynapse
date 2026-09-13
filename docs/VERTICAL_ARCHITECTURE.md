# Vertical Architecture — How Plugin Verticals Work

## The Big Picture

ContextSynapse is the **engine**. Verticals are **applications** built on top of it.

```
┌────────────────────────────────────────────────────────┐
│              VERTICAL APPS (pip packages)               │
│  ┌──────────────┐ ┌──────────────┐ ┌───────────────┐  │
│  │ contextsynapse- │ │ contextsynapse- │ │  your-company  │  │
│  │   finance    │ │    legal     │ │   -vertical    │  │
│  └──────┬───────┘ └──────┬───────┘ └───────┬────────┘  │
│         │                │                  │           │
│         ▼                ▼                  ▼           │
│  ┌─────────────────────────────────────────────────┐   │
│  │           PLUGIN CONTRACT (base.py)              │   │
│  │  VerticalPlugin · Sensor · PluginSchema          │   │
│  │  PluginRoute · PluginMCPTool · DashboardCard     │   │
│  └─────────────────────┬───────────────────────────┘   │
│                        │                                │
│  ┌─────────────────────▼───────────────────────────┐   │
│  │           CONTEXTCORE ENGINE                     │   │
│  │  graph · aiql · mcp · pipelines · context        │   │
│  │  storage · vector · search · agents · a2a        │   │
│  └─────────────────────────────────────────────────┘   │
└────────────────────────────────────────────────────────┘
```

## Step-by-Step: Building a Legal Vertical

### Step 1 — Create the package

```
contextsynapse-legal/
├── pyproject.toml
├── contextsynapse_legal/
│   ├── __init__.py
│   ├── plugin.py          # The main plugin class
│   ├── schemas.py          # Domain schemas
│   ├── sensors/
│   │   ├── court_filings.py
│   │   └── regulatory.py
│   └── api/
│       └── routes.py
└── tests/
```

### Step 2 — Define your schemas

Schemas tell ContextSynapse what node and edge types your vertical introduces:

```python
# contextsynapse_legal/schemas.py
from contextsynapse.plugins import PluginSchema

LEGAL_SCHEMA = PluginSchema(
    name="legal",
    version="1.0",
    description="Legal domain — contracts, cases, regulations",
    node_types={
        "Contract": {
            "properties": {
                "title": {"type": "string", "required": True},
                "parties": {"type": "list"},
                "effective_date": {"type": "datetime"},
                "expiry_date": {"type": "datetime"},
                "status": {"type": "string", "enum": ["draft", "active", "expired", "terminated"]},
                "value": {"type": "float"},
                "jurisdiction": {"type": "string"},
            }
        },
        "Case": {
            "properties": {
                "case_number": {"type": "string", "required": True},
                "court": {"type": "string"},
                "filed_date": {"type": "datetime"},
                "status": {"type": "string"},
                "parties": {"type": "list"},
            }
        },
        "Regulation": {
            "properties": {
                "code": {"type": "string", "required": True},
                "title": {"type": "string"},
                "agency": {"type": "string"},
                "effective_date": {"type": "datetime"},
            }
        },
        "Clause": {
            "properties": {
                "text": {"type": "string"},
                "clause_type": {"type": "string"},  # "liability", "termination", "ip", etc.
                "risk_level": {"type": "string", "enum": ["low", "medium", "high"]},
            }
        },
    },
    edge_types={
        "HAS_CLAUSE": {"source": "Contract", "target": "Clause"},
        "GOVERNED_BY": {"source": "Contract", "target": "Regulation"},
        "CITES": {"source": "Case", "target": "Regulation"},
        "RELATED_CASE": {"source": "Case", "target": "Case"},
        "PARTY_TO": {"source": "Entity", "target": "Contract"},
    },
)
```

### Step 3 — Build sensors (data feeds)

Sensors automatically populate the graph on a schedule:

```python
# contextsynapse_legal/sensors/court_filings.py
import aiohttp
from contextsynapse.plugins import Sensor

class CourtFilingSensor(Sensor):
    """Polls court filing APIs for new cases."""
    name = "court_filings"
    interval_seconds = 3600  # every hour

    async def collect(self, db):
        results = []

        # Fetch from court API (example)
        async with aiohttp.ClientSession() as session:
            async with session.get("https://api.courts.example/filings?since=24h") as resp:
                filings = await resp.json()

        for filing in filings:
            # Each dict becomes a graph node
            results.append({
                "id": f"case_{filing['case_number']}",
                "type": "Case",
                "properties": {
                    "case_number": filing["case_number"],
                    "court": filing["court"],
                    "filed_date": filing["filed_date"],
                    "status": "filed",
                    "parties": filing["parties"],
                },
            })

            # Link to cited regulations
            for reg_code in filing.get("regulations_cited", []):
                results.append({
                    "source_id": f"case_{filing['case_number']}",
                    "target_id": f"reg_{reg_code}",
                    "edge_type": "CITES",
                    "properties": {"cited_in_section": filing.get("section")},
                })

        return results

    def decay_config(self):
        # Case data stays relevant for 1 year
        return {"ttl_hours": 8760, "decay_rate": 0.001}

    async def health_check(self):
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get("https://api.courts.example/health") as resp:
                    return {"healthy": resp.status == 200, "message": "ok", "latency_ms": 0}
        except Exception as e:
            return {"healthy": False, "message": str(e), "latency_ms": 0}
```

### Step 4 — Add API routes

Custom REST endpoints for your domain:

```python
# contextsynapse_legal/api/routes.py
from fastapi import APIRouter, Depends, HTTPException
from contextsynapse.plugins import PluginRoute

def create_legal_router(db=None):
    router = APIRouter()

    @router.get("/contracts")
    async def list_contracts(status: str = None):
        """List all contracts, optionally filtered by status."""
        nodes = db.get_all_nodes(label="Contract")
        if status:
            nodes = [n for n in nodes if n.properties.get("status") == status]
        return {"contracts": [{"id": n.id, **n.properties} for n in nodes]}

    @router.get("/contracts/{contract_id}/clauses")
    async def get_clauses(contract_id: str):
        """Get all clauses for a contract."""
        neighbors = db.get_neighbors(contract_id, edge_label="HAS_CLAUSE")
        clauses = []
        for neighbor_id, edge in neighbors:
            node = db.get_node(neighbor_id)
            if node:
                clauses.append({"id": node.id, **node.properties})
        return {"contract_id": contract_id, "clauses": clauses}

    @router.get("/contracts/{contract_id}/risk")
    async def assess_risk(contract_id: str):
        """Assess risk by analyzing clause risk levels."""
        neighbors = db.get_neighbors(contract_id, edge_label="HAS_CLAUSE")
        risk_counts = {"low": 0, "medium": 0, "high": 0}
        for neighbor_id, edge in neighbors:
            node = db.get_node(neighbor_id)
            if node:
                level = node.properties.get("risk_level", "low")
                risk_counts[level] = risk_counts.get(level, 0) + 1
        overall = "high" if risk_counts["high"] > 0 else "medium" if risk_counts["medium"] > 2 else "low"
        return {"contract_id": contract_id, "risk": overall, "breakdown": risk_counts}

    @router.post("/contracts/{contract_id}/analyze")
    async def analyze_contract(contract_id: str):
        """Use ContextHub to build LLM analysis context for a contract."""
        from contextsynapse.context.hub import ContextHub

        hub = ContextHub(system_prompt="You are a legal analyst. Analyze this contract for risks.")
        node = db.get_node(contract_id)
        if not node:
            raise HTTPException(404, "Contract not found")

        # Add the contract + its clauses + related regulations to context
        hub.add_nodes([node])
        neighbors = db.get_neighbors(contract_id)
        related_nodes = [db.get_node(nid) for nid, _ in neighbors]
        hub.add_nodes([n for n in related_nodes if n])

        return {
            "contract_id": contract_id,
            "context": hub.to_messages(),
            "token_count": hub.estimate_tokens(),
        }

    return router
```

### Step 5 — Wire it all together in the plugin class

```python
# contextsynapse_legal/plugin.py
from contextsynapse.plugins import VerticalPlugin, PluginRoute, PluginMCPTool, DashboardCard
from contextsynapse_legal.schemas import LEGAL_SCHEMA
from contextsynapse_legal.sensors.court_filings import CourtFilingSensor

class LegalVertical(VerticalPlugin):
    name = "legal"
    version = "0.1.0"
    description = "Legal domain — contracts, cases, regulations, compliance"
    author = "Your Company"

    def __init__(self):
        super().__init__()
        self._router = None

    def schemas(self):
        return [LEGAL_SCHEMA]

    def sensors(self):
        return [CourtFilingSensor()]

    def api_routers(self):
        from contextsynapse_legal.api.routes import create_legal_router
        self._router = create_legal_router(db=self._db)
        return [PluginRoute(router=self._router, prefix="/legal", tags=["legal"])]

    def mcp_tools(self):
        return [
            PluginMCPTool(
                name="search_contracts",
                description="Search contracts by party name or status",
                input_schema={
                    "type": "object",
                    "properties": {
                        "query": {"type": "string"},
                        "status": {"type": "string", "enum": ["draft", "active", "expired"]},
                    },
                    "required": ["query"],
                },
                handler=self._search_contracts,
            ),
        ]

    def dashboard_cards(self):
        return [
            DashboardCard(
                id="legal-contracts",
                title="Contracts",
                component_path="legal/ContractsPage.js",
                route="/dashboard/legal/contracts",
                icon="file-text",
                category="legal",
                order=10,
            ),
            DashboardCard(
                id="legal-compliance",
                title="Regulatory Compliance",
                component_path="legal/CompliancePage.js",
                route="/dashboard/legal/compliance",
                icon="shield",
                category="legal",
                order=20,
            ),
        ]

    async def on_startup(self, db):
        await super().on_startup(db)
        # Register schemas with the core engine
        # Seed any base regulation data
        pass

    def _search_contracts(self, query, status=None):
        nodes = self._db.get_all_nodes(label="Contract")
        results = []
        for n in nodes:
            if query.lower() in str(n.properties).lower():
                if status is None or n.properties.get("status") == status:
                    results.append({"id": n.id, **n.properties})
        return {"contracts": results[:20]}
```

### Step 6 — Register via entry points

```toml
# contextsynapse-legal/pyproject.toml
[build-system]
requires = ["setuptools>=68.0"]
build-backend = "setuptools.build_meta"

[project]
name = "contextsynapse-legal"
version = "0.1.0"
description = "Legal vertical for ContextSynapse"
requires-python = ">=3.10"
dependencies = ["contextsynapse>=1.0.0", "aiohttp"]

[project.entry-points."contextsynapse.plugins"]
legal = "contextsynapse_legal.plugin:LegalVertical"
```

### Step 7 — Install and run

```bash
# Install core + your vertical
pip install contextsynapse
pip install contextsynapse-legal   # or: pip install -e ./contextsynapse-legal

# Start server — plugin auto-discovers
contextsynapse                     # or: uvicorn contextsynapse.api.api:app

# Server logs:
#   Discovered plugin 'legal' v0.1.0 (loaded in 12.3ms)
#   Started plugin 'legal' in 5.1ms
#   Started sensor 'legal.court_filings' (every 3600s)
#   Loaded 1 plugin(s): legal
```

### Step 8 — Use it

```bash
# Check plugin is loaded
curl http://localhost:8000/plugins/
# → {"plugins_registered": 1, "plugins_running": 1, ...}

# Use the legal API
curl http://localhost:8000/v1/legal/contracts
curl http://localhost:8000/v1/legal/contracts/c_123/risk

# Query via AIQL
curl -X POST http://localhost:8000/v1/query \
  -d '{"query": "FIND NODES WHERE label = '\''Contract'\'' AND status = '\''active'\''"}'

# Sensor data appears automatically in the graph
curl http://localhost:8000/plugins/legal/sensors
# → {"sensors": [{"name": "court_filings", "healthy": true, ...}]}
```

## What the Engine Does Automatically

When a vertical is installed, ContextSynapse handles:

| Concern | Engine handles it | Vertical provides |
|---------|------------------|-------------------|
| **Discovery** | Scans Python entry points at startup | Entry point in `pyproject.toml` |
| **Validation** | Checks names, intervals, schemas | Schema definitions |
| **API mounting** | Mounts routers at `/v1/{plugin_name}/` | FastAPI routers |
| **Sensor scheduling** | Runs `collect()` on interval, handles retries | `collect()` implementation |
| **Graph writes** | Upserts nodes/edges from sensor output | Return dicts from `collect()` |
| **MCP tools** | Registers tools with Claude/Copilot | Tool definitions + handlers |
| **Dashboard** | Serves card metadata at `/plugins/dashboard/cards` | Card definitions |
| **Lifecycle** | Calls `on_startup`/`on_shutdown` | Startup/shutdown logic |
| **Graph events** | Forwards events to plugins | `on_graph_event()` handler |
| **Auth/RBAC** | Core auth middleware applies | Nothing extra needed |
| **Rate limiting** | Core rate limiter applies | Nothing extra needed |

## What the Vertical Does NOT Need to Do

- No database setup — the graph engine is already running
- No auth implementation — core middleware handles it
- No vector indexing — core pipelines handle embedding
- No deployment config — just `pip install` and restart
- No frontend build system — dashboard cards are metadata, not bundles (yet)

## How Your Finance Vertical Would Work

Your current `private/` directory maps perfectly to this architecture:

```
contextsynapse-finance/
├── pyproject.toml
├── contextsynapse_finance/
│   ├── plugin.py              ← FinanceVertical(VerticalPlugin)
│   ├── schemas/               ← pms.yaml, finance.yaml, mutual_fund.yaml
│   ├── sensors/
│   │   ├── price_feed.py      ← PriceSensor(Sensor) — wraps yfinance
│   │   ├── fundamental.py     ← FundamentalSensor(Sensor)
│   │   └── watchdog.py        ← NewsWatchdog(Sensor) — RSS polling
│   ├── pms/                   ← portfolio.py, backtest.py, risk.py, etc.
│   ├── connectors/            ← AMFI, NSE, NSDL, RBI connectors
│   ├── api/
│   │   ├── pms_router.py      ← Portfolio management endpoints
│   │   ├── fusion_router.py   ← Sensor fusion endpoints
│   │   └── dashboard_router.py
│   └── frontend/              ← React pages (PortfolioCockpit, StockHUD, etc.)
└── tests/

[project.entry-points."contextsynapse.plugins"]
finance = "contextsynapse_finance.plugin:FinanceVertical"
```

Install: `pip install contextsynapse-finance`
Result: Full PMS, stock analysis, sensor fusion — all auto-discovered.

# ContextSynapse

**The shared brain your agents are missing.** Open-source context engine for AI agents — ingest knowledge, build connections, and deliver the right context to any LLM.

[![CI](https://github.com/contextsynapse/contextsynapse/actions/workflows/ci.yml/badge.svg)](https://github.com/contextsynapse/contextsynapse/actions)
[![License](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)

---

## What is ContextSynapse?

ContextSynapse is the **shared brain** for your AI agents. It stores knowledge as connected graphs, lets agents remember and recall across sessions, and delivers the right context to any LLM — Claude, GPT, Llama, Gemini, or any other model.

**Not a database. Not an agent framework. The context layer between them.**

```
  Your Agents (any LLM)          ContextSynapse              Your Data
  +------------------+      +--------------------+      +----------------+
  | Claude agent     |----->|                    |<-----| Documents      |
  | GPT agent        |----->|  Shared Brain      |<-----| APIs           |
  | Llama agent      |----->|                    |<-----| Databases      |
  | Custom agent     |----->|  Remember, Recall  |<-----| Files          |
  +------------------+      |  Search, Reason    |      +----------------+
                             +--------------------+
```

**Core capabilities:**
- **Shared agent memory** — Agents remember, recall, and share knowledge across sessions
- **Graph RAG** — Retrieve context via keyword, BM25, and vector fusion — relationships that vector-only RAG misses
- **AIQL query language** — SQL-like syntax with graph patterns, traversals, and hybrid search
- **Context assembly** — ContextHub builds LLM-ready messages from graph data
- **MCP server** — Expose your brain as tools for Claude, Copilot, and other AI agents
- **AgentShield** — Trust scoring, adaptive permissions, PII detection, audit trails
- **Plugin system** — Build domain-specific verticals on top of the context engine
- **Any LLM, any framework** — Works with LangChain, CrewAI, AutoGen, OpenAI, Anthropic, and 8+ more

---

## Quick Start

### Install

```bash
pip install contextsynapse
```

### 30-Second Demo

```python
from contextsynapse import ContextSynapse
from contextsynapse.aiql import AIQLExecutor

# Create a graph and executor
db = ContextSynapse()
ex = AIQLExecutor(contextcore=db)

# Build a knowledge graph
ex.execute("CREATE GRAPH company")
ex.execute("USE GRAPH company")
ex.execute('CREATE NODE Person {name: "Alice", role: "Engineer", age: 30}')
ex.execute('CREATE NODE Person {name: "Bob", role: "Manager", age: 42}')
ex.execute('CREATE NODE Project {name: "Atlas", status: "active"}')
ex.execute('CREATE EDGE WORKS_ON FROM Person WHERE name = "Alice" TO Project WHERE name = "Atlas"')
ex.execute('CREATE EDGE MANAGES FROM Person WHERE name = "Bob" TO Project WHERE name = "Atlas"')

# Query it
result = ex.execute("SELECT * FROM Person")
for node in result.get("nodes", []):
    print(node.properties.get("name"), "—", node.properties.get("role"))

# Build LLM-ready context
from contextsynapse.context.hub import ContextHub
hub = ContextHub(system_prompt="You are a project analyst.")
hub.add_nodes(db.get_all_nodes())
messages = hub.to_messages()  # Ready for OpenAI/Anthropic API
```

Run the full demo:
```bash
python examples/demo.py
```

---

## Running the Full Stack

ContextSynapse has a Python backend (API server) and a React frontend (dashboard). Three ways to run it:

### Option 1: Docker Compose (recommended)

```bash
# Clone the repo
git clone https://github.com/contextsynapse/contextsynapse.git
cd contextsynapse

# Copy env file and set your keys
cp .env.example .env
# Edit .env — at minimum set CONTEXTSYNAPSE_ADMIN_KEY and CONTEXTSYNAPSE_JWT_SECRET

# Start everything (API + Redis + PostgreSQL)
docker compose up -d

# With the frontend dashboard
docker compose --profile ui up -d

# API:       http://localhost:8000
# Dashboard: http://localhost:3000
# Redis:     localhost:6379
# Postgres:  localhost:5432
```

### Option 2: Manual Setup

**Backend:**
```bash
# Create a virtual environment
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate

# Install with all extras
pip install -e ".[all]"

# Copy and configure environment
cp .env.example .env
# Edit .env — set at minimum:
#   CONTEXTSYNAPSE_ADMIN_KEY=your-secure-key
#   CONTEXTSYNAPSE_JWT_SECRET=your-jwt-secret

# Start the API server
uvicorn contextsynapse.api.api:app --host 0.0.0.0 --port 8000 --reload
```

**Frontend:**
```bash
cd frontend
npm install
npm start
# Opens at http://localhost:3000
```

**Optional services (Redis, PostgreSQL):**
```bash
# Redis — needed for multi-agent coordination, caching, agent registry
docker run -d --name redis -p 6379:6379 redis:7-alpine

# PostgreSQL — needed for user auth, tenant management, audit logs
docker run -d --name postgres -p 5432:5432 \
  -e POSTGRES_DB=contextsynapse \
  -e POSTGRES_USER=contextsynapse \
  -e POSTGRES_PASSWORD=contextsynapse \
  postgres:16-alpine
```

### Option 3: Minimal (Python library only)

No server needed — use ContextSynapse as an in-process graph database:

```bash
pip install contextsynapse
```
```python
from contextsynapse import ContextSynapse
db = ContextSynapse()
# Use directly — no API server required
```

---

## Integration

ContextSynapse connects to your agents via **5 integration paths** — use whichever fits your stack:

```
  +-----------+     +-------+     +-----+     +--------+     +---------+
  | MCP Server|     | REST  |     | A2A |     | Python |     |Framework|
  | (Claude,  |     | API   |     |Proto|     | SDK    |     |Adapters |
  | Copilot)  |     |       |     |     |     |        |     |         |
  +-----------+     +-------+     +-----+     +--------+     +---------+
       |                |            |             |               |
       +--------+-------+-----+------+------+------+------+-------+
                |              |             |             |
                +-------- ContextSynapse (shared brain) ---+
```

### 1. MCP Server (Claude / Copilot / any MCP client)

Exposes 29 tools via Model Context Protocol. Two agents using this server share the **same graph**.

```bash
# Stdio transport (default — for Claude Desktop, Claude Code)
python -m contextsynapse.mcp

# SSE transport (for web clients, remote agents)
python -m contextsynapse.mcp --transport sse --port 8100

# With namespace and auth
python -m contextsynapse.mcp --namespace myproject --api-key agent1:secret
```

Claude Desktop config (`claude_desktop_config.json`):
```json
{
  "mcpServers": {
    "contextsynapse": {
      "command": "python",
      "args": ["-m", "contextsynapse.mcp"]
    }
  }
}
```

Claude Code:
```bash
claude mcp add contextsynapse -- python -m contextsynapse.mcp
```

**MCP Auth:** Multi-session auth middleware — each agent gets scoped access to their session's graph. Set `CONTEXTSYNAPSE_API_KEY` for authenticated connections.

### 2. REST API

Full-featured FastAPI server with 30+ route modules:

```bash
uvicorn contextsynapse.api.api:app --host 0.0.0.0 --port 8000
```

Key endpoints:
| Endpoint | Description |
|----------|-------------|
| `POST /context/agents` | Register an agent, get API key |
| `POST /context/sessions` | Create a shared session |
| `POST /context/sessions/{id}/ingest` | Ingest content into session |
| `GET /context/search` | Search across contexts |
| `POST /context/query` | Execute AIQL query |
| `GET /context/briefing` | Get agent briefing |
| `POST /experiments/{id}/run` | Run agent experiment |
| `GET /dashboard/*` | Dashboard data endpoints |

Auth: JWT tokens via `POST /auth/login` + `x-admin-key` header for admin ops.

### 3. A2A Protocol (Agent-to-Agent)

Implements [Google's A2A protocol](https://github.com/google/A2A) for agent interoperability:

```python
from contextsynapse.a2a import Task, TaskState, TextPart, Message

# Create a task for another agent
task = Task(id="task-1", state=TaskState.SUBMITTED)
task.messages.append(Message(
    role="user",
    parts=[TextPart(text="Analyze TSLA earnings")]
))
```

Features: task state machine, agent card discovery, SSE streaming, artifact exchange.

### 4. Python SDK

```bash
pip install contextsynapse-sdk
```

```python
from contextsynapse_sdk import ContextSynapseClient

# Connect to server
client = ContextSynapseClient("http://localhost:8000", api_key="your-key")

# Register an agent
agent = client.agents.register("my-bot", role="researcher")

# Create a session and work with it
session = client.sessions.create("research-project")
session.ingest({"content": "Tesla Q3 revenue was $25.2B", "type": "Fact"})
results = session.search("Tesla revenue")
```

### 5. Framework Adapters

Drop-in integration with 8 AI frameworks:

| Framework | Import | What you get |
|-----------|--------|-------------|
| **LangChain** | `contextsynapse.adapters.langchain` | Retriever, tools, chat message history |
| **LangGraph** | `contextsynapse.adapters.langgraph` | Checkpoint saver, context tools, message history |
| **CrewAI** | `contextsynapse.adapters.crewai` | Tool wrappers for CrewAI agents |
| **AutoGen** | `contextsynapse.adapters.autogen` | Tool wrappers for AutoGen agents |
| **OpenAI** | `contextsynapse.adapters.openai` | Function definitions for function calling |
| **LlamaIndex** | `contextsynapse.adapters.llamaindex` | Tool specs for LlamaIndex agents |
| **PydanticAI** | `contextsynapse.adapters.pydantic_ai` | Tool wrappers for PydanticAI |
| **Swarm** | `contextsynapse.adapters.swarm` | Tool functions for OpenAI Swarm |

```python
# Example: LangChain retriever
from contextsynapse.adapters.langchain import AIContextDBRetriever
retriever = AIContextDBRetriever(db=my_graph, k=10)
docs = retriever.get_relevant_documents("What is TSLA outlook?")
```

---

## LLM Providers

ContextSynapse supports 12+ LLM providers. **No LLM is required for core graph operations** — LLMs are only needed for entity extraction, RAG queries, natural language search, and the agent playground.

| Provider | Env Variable | Default Model | Notes |
|----------|-------------|---------------|-------|
| **Groq** | `GROQ_API_KEY` | `llama-3.1-8b-instant` | Fastest, free tier available |
| **OpenAI** | `OPENAI_API_KEY` | `gpt-4o-mini` | Most reliable |
| **Anthropic** | `ANTHROPIC_API_KEY` | `claude-sonnet-4-20250514` | Best reasoning |
| **Ollama** | `OLLAMA_HOST` | `gemma3:1b` | Local, no API key needed |
| **DeepSeek** | `DEEPSEEK_API_KEY` | `deepseek-chat` | Cost-effective |
| **Together** | `TOGETHER_API_KEY` | `Meta-Llama-3.1-70B-Instruct-Turbo` | Open-source models |
| **Mistral** | `MISTRAL_API_KEY` | `mistral-large-latest` | EU-hosted |
| **Cerebras** | `CEREBRAS_API_KEY` | `llama3.1-70b` | Fast inference |
| **Fireworks** | `FIREWORKS_API_KEY` | `llama-v3p1-70b-instruct` | Serverless |
| **Perplexity** | `PERPLEXITY_API_KEY` | `sonar-pro` | Search-augmented |
| **Google Gemini** | `GOOGLE_API_KEY` | `gemini-pro` | Multimodal |
| **Cohere** | `COHERE_API_KEY` | `command-r-plus` | RAG-optimized |

Auto-detection: set any API key and ContextSynapse picks it up. Or specify explicitly:
```python
from contextsynapse.llm import get_llm_client
llm = get_llm_client(provider="groq")
```

---

## Security

ContextSynapse includes enterprise-grade security out of the box:

| Layer | What it does |
|-------|-------------|
| **RBAC** | Role-based access control — Admin, Manager, Analyst, Viewer, custom roles |
| **Row-Level Security** | Tenant-isolated queries — each tenant sees only their data |
| **AgentShield** | Continuous behavioral auth for AI agents — trust scoring, anomaly detection, adaptive permissions |
| **PII Detection** | Auto-detect and redact PII (emails, phones, SSNs) before storage |
| **Field Encryption** | Scoped AES encryption — encrypt specific fields per tenant/scope |
| **Audit Trail** | Every read/write logged with who, what, when, from where |
| **JWT Auth** | JWT-based authentication with tenant, role, and scope claims |
| **Context ACL** | Fine-grained path-based access control on assembled contexts |
| **Auto-Tagger** | Classify sensitivity level of ingested content automatically |
| **Security Middleware** | Agent clearance levels, request validation, rate limiting |

```python
from contextsynapse.security import DataSecurity, PIIDetector

# Detect PII in text
pii = PIIDetector()
result = pii.scan("Contact john@example.com or call 555-0123")
# → [PIIMatch(type=EMAIL, value="john@example.com"), PIIMatch(type=PHONE, value="555-0123")]

# Enforce RBAC
from contextsynapse.security.rbac import RBACManager
rbac = RBACManager()
rbac.check_permission(user_role="analyst", action="read", resource="graph:company")
```

---

## Architecture

```
contextsynapse/
├── core/           # Graph storage engine (CSR, Redis, LMDB)
├── aiql/           # AIQL query language (grammar, parser, compiler, executor)
├── api/            # FastAPI REST API
├── mcp/            # MCP server for Claude/Copilot
├── context/        # ContextHub — LLM context building + assembled contexts
├── storage/        # Storage backends + WAL + namespace store
├── vector/         # Vector DB integration (NumPy, FAISS, Qdrant, Chroma)
├── ingestion/      # Universal ingestion pipeline (URL, file, API)
├── search/         # Graph-enhanced search + RAG + full-text
├── extraction/     # Entity/fact extraction from documents
├── security/       # RBAC, RLS, encryption, PII detection, audit
├── shield/         # AgentShield — behavioral auth + trust engine
├── governance/     # Data governance layer
├── a2a/            # Agent-to-agent protocol
├── adapters/       # Framework adapters (LangChain, CrewAI, etc.)
├── llm/            # Multi-provider LLM client (12+ providers)
├── rules/          # Rule engine with temporal + aggregate evaluators
├── workspace/      # Git/GitHub workspace connectors
└── plugins/        # Plugin system for vertical applications

frontend/           # React dashboard (graph explorer, playground, sessions)
plugins/            # Domain plugins (installed separately)
verticals/          # Vertical applications (private, not included in package)
sdk/                # Python SDK for REST API access
```

## Storage Backends

| Backend | Best For | Scale |
|---------|----------|-------|
| **CSR (in-memory)** | Development, small graphs | ~1M nodes |
| **Redis** | Multi-worker, shared state | ~10M nodes |
| **LMDB** | Single-node persistence | ~50M nodes |
| **PostgreSQL** | Production, horizontal scale | Unlimited |

```bash
# Redis backend
CONTEXTSYNAPSE_GRAPH_BACKEND=redis
CONTEXTSYNAPSE_REDIS_URL=redis://localhost:6379

# LMDB backend
CONTEXTSYNAPSE_STORAGE_BACKEND=lmdb
```

### Cloud Storage

For Kubernetes and ephemeral deployments, graphs persist to cloud storage:

| Provider | Backend | Auth | Install |
|----------|---------|------|---------|
| **AWS S3** | `s3` | IAM / env credentials | `pip install boto3` |
| **Google Cloud Storage** | `gcs` | Service account JSON | `pip install google-cloud-storage` |
| **Azure Blob** | `azure` | Connection string or DefaultAzureCredential | `pip install azure-storage-blob azure-identity` |
| **MinIO / R2** | `s3` | S3-compatible endpoint | `pip install boto3` |

```bash
# AWS S3
CONTEXTSYNAPSE_STORAGE_BACKEND=s3
CONTEXTSYNAPSE_S3_BUCKET=my-context-graphs
CONTEXTSYNAPSE_S3_REGION=us-east-1

# Google Cloud Storage
CONTEXTSYNAPSE_STORAGE_BACKEND=gcs
CONTEXTSYNAPSE_GCS_BUCKET=my-context-graphs

# Azure Blob Storage
CONTEXTSYNAPSE_STORAGE_BACKEND=azure
CONTEXTSYNAPSE_AZURE_CONTAINER=context-graphs
CONTEXTSYNAPSE_AZURE_CONNECTION_STRING=DefaultEndpointsProtocol=https;AccountName=...
```

---

## Configuration

All configuration is via environment variables (prefix `CONTEXTSYNAPSE_`). Copy `.env.example` to `.env` and set what you need — everything has sensible defaults.

> **Note:** The older `AICONTEXTDB_*` prefix is still supported for backward compatibility.

### Core Settings

| Variable | Default | Description |
|----------|---------|-------------|
| `CONTEXTSYNAPSE_ADMIN_KEY` | *required* | Admin API key for server mode |
| `CONTEXTSYNAPSE_JWT_SECRET` | *required* | JWT signing secret for auth |
| `CONTEXTSYNAPSE_ENV` | `development` | `development` or `production` |
| `CONTEXTSYNAPSE_CORS_ORIGINS` | `http://localhost:3000` | Allowed CORS origins |
| `CONTEXTSYNAPSE_RATE_LIMIT_RPM` | `300` | API rate limit (requests/minute) |

### Graph Backend

| Variable | Default | Options | When to use |
|----------|---------|---------|-------------|
| `CONTEXTSYNAPSE_GRAPH_BACKEND` | `csr` | `csr`, `redis`, `lmdb` | `csr` for dev/library, `redis` for multi-worker |
| `CONTEXTSYNAPSE_REDIS_URL` | — | `redis://host:port` | Set to enable Redis backend |
| `CONTEXTSYNAPSE_STORAGE_BACKEND` | `csr` | `csr`, `lmdb` | `lmdb` for persistent single-node |
| `DATABASE_URL` | SQLite | `postgresql://...` | Set for PostgreSQL (users, auth, tenants) |

### LLM Providers

**Not required for core graph operations.** Only needed for entity extraction, RAG answers, NL queries, and the agent playground. Set any one key — auto-detected:

| Provider | Env Variable | Default Model | Speed | Cost |
|----------|-------------|---------------|-------|------|
| **Groq** | `GROQ_API_KEY` | `llama-3.1-8b-instant` | Fastest | Free tier |
| **OpenAI** | `OPENAI_API_KEY` | `gpt-4o-mini` | Fast | Pay-per-use |
| **Anthropic** | `ANTHROPIC_API_KEY` | `claude-sonnet-4-20250514` | Fast | Pay-per-use |
| **Ollama** | `OLLAMA_HOST` | `gemma3:1b` | Local | Free (self-hosted) |
| **DeepSeek** | `DEEPSEEK_API_KEY` | `deepseek-chat` | Fast | Cheapest |
| **Together** | `TOGETHER_API_KEY` | `Llama-3.1-70B-Instruct` | Fast | Pay-per-use |
| **Mistral** | `MISTRAL_API_KEY` | `mistral-large-latest` | Fast | EU-hosted |
| **Cerebras** | `CEREBRAS_API_KEY` | `llama3.1-70b` | Fastest | Free beta |
| **Fireworks** | `FIREWORKS_API_KEY` | `llama-v3p1-70b-instruct` | Fast | Serverless |
| **Perplexity** | `PERPLEXITY_API_KEY` | `sonar-pro` | Fast | Search-augmented |
| **Google** | `GOOGLE_API_KEY` | `gemini-pro` | Fast | Free tier |
| **Cohere** | `COHERE_API_KEY` | `command-r-plus` | Fast | RAG-optimized |

### Embedding Models

**Default: Ollama `nomic-embed-text` (768 dims, local, free).** Falls back to NumPy cosine if no embedding model available.

| Provider | Model | Dimensions | Notes |
|----------|-------|-----------|-------|
| **Ollama** | `nomic-embed-text` | 768 | Default, local, free |
| **Ollama** | `mxbai-embed-large` | 1024 | Higher quality |
| **Ollama** | `all-minilm` | 384 | Smallest, fastest |
| **OpenAI** | `text-embedding-3-small` | 1536 | Best quality/cost |
| **OpenAI** | `text-embedding-3-large` | 3072 | Highest quality |
| **Cohere** | `embed-english-v2.0` | 4096 | RAG-optimized |
| **Gemini** | `text-embedding-004` | 768 | Multimodal |
| **Mistral** | `mistral-embed` | 1024 | EU-hosted |
| **Together** | `m2-bert-80M-8k-retrieval` | 768 | Open-source |
| **Local** | `all-MiniLM-L6-v2` | 384 | sentence-transformers, no API |

Configure in `config/config.yaml`:
```yaml
embeddings:
  provider: "local"          # "local" (sentence-transformers) or "ollama"
  model: "all-MiniLM-L6-v2"
  dimension: 384
```

### Vector Database

| Backend | Env Variable | Notes |
|---------|-------------|-------|
| **NumPy** (default) | — | Built-in, no setup, good for <100K vectors |
| **Qdrant** | `QDRANT_URL=http://localhost:6333` | Production, scalable |
| **FAISS** | `pip install faiss-cpu` | Fast, in-process |
| **ChromaDB** | `pip install chromadb` | Embedded, easy setup |

```bash
CONTEXTSYNAPSE_VECTOR_DB_BACKEND=qdrant  # or: custom, faiss, chroma
```

### Advanced Settings

| Variable | Default | Description |
|----------|---------|-------------|
| `CONTEXTSYNAPSE_MAX_GRAPHS` | `50` | Max graphs in memory (LRU eviction) |
| `CONTEXTSYNAPSE_LLM_CONCURRENCY` | `10` | Max concurrent LLM calls |
| `CONTEXTSYNAPSE_LLM_RPM` | `100` | LLM rate limit per minute |
| `CONTEXTSYNAPSE_CONFIDENCE_HALFLIFE_DAYS` | `28` | Memory confidence decay half-life |
| `CONTEXTSYNAPSE_PRUNE_INTERVAL` | `3600` | Auto-prune interval (seconds) |
| `CONTEXTSYNAPSE_MCP_MAX_SESSIONS` | `50` | Max concurrent MCP sessions |
| `CONTEXTSYNAPSE_S3_BUCKET` | — | S3 bucket for cloud graph storage |
| `CONTEXTSYNAPSE_GCS_BUCKET` | — | Google Cloud Storage bucket |
| `CONTEXTSYNAPSE_AZURE_CONTAINER` | — | Azure Blob Storage container |

### Zero-Config Quick Start

ContextSynapse works out of the box with **zero configuration**:
- Graph: in-memory CSR (no Redis needed)
- Search: keyword + BM25 index (no vector DB needed)
- Embedding: NumPy cosine fallback (no embedding model needed)
- Database: SQLite (no PostgreSQL needed)
- Encryption: Base64 fallback (no cryptography package needed)

Just `pip install contextsynapse` and go.

---

## Building Plugins

ContextSynapse follows an **open-core model** — the graph engine is open source, and domain-specific applications are built as plugins.

```python
from contextsynapse.plugins import VerticalPlugin, Sensor, PluginSchema

class MySensor(Sensor):
    name = "my_data_feed"
    interval_seconds = 300

    async def collect(self, db):
        return [{"id": "item_1", "type": "MyType", "properties": {"value": 42}}]

class MyVertical(VerticalPlugin):
    name = "my_domain"
    version = "0.1.0"

    def sensors(self):
        return [MySensor()]

    def schemas(self):
        return [PluginSchema(name="my_schema", node_types={...})]
```

Register via `pyproject.toml`:
```toml
[project.entry-points."contextsynapse.plugins"]
my_domain = "my_package.plugin:MyVertical"
```

---

## SDK

```bash
pip install contextsynapse-sdk
```

```python
from contextsynapse_sdk import ContextSynapseClient

client = ContextSynapseClient("http://localhost:8000", api_key="your-key")
client.add_node("person_1", "Person", {"name": "Alice"})
results = client.search("Alice")
```

---

## Examples

| Example | Description |
|---------|-------------|
| [`real_demo.py`](examples/real_demo.py) | **Start here** — startup knowledge graph with PII detection + LLM context |
| [`demo_graph_rag.py`](examples/demo_graph_rag.py) | **Graph RAG** — document ingestion, keyword search, topic clustering, hybrid retrieval |
| [`demo_shared_memory.py`](examples/demo_shared_memory.py) | **Shared Memory** — multi-agent remember/recall, cross-agent sharing, versioning |
| [`demo_memory_layer.py`](examples/demo_memory_layer.py) | **Memory Layer** — 4-tier memory (working/hot/persistent/cold), cross-agent sharing, versioning |
| [`demo_secure_vault.py`](examples/demo_secure_vault.py) | **Secure Vault** — PII gate, field encryption, AgentShield trust, RBAC, audit trail |
| [`demo_context_scoring.py`](examples/demo_context_scoring.py) | **Context Quality** — node scoring, freshness detection, quality filtering, usage tracking |
| [`demo_traceability.py`](examples/demo_traceability.py) | **Traceability** — blockchain hash chains, Merkle roots, proof tokens, tamper detection |
| [`demo_agent_security.py`](examples/demo_agent_security.py) | **Agent Security** — trust scoring, adaptive permissions, anomaly detection |
| [`demo.py`](examples/demo.py) | Simple walkthrough — graph, AIQL, context building |
| [`quickstart.py`](examples/quickstart.py) | Minimal 30-line getting started |
| [`rag_pipeline.py`](examples/rag_pipeline.py) | RAG pipeline with graph-enhanced retrieval |
| [`multi_agent.py`](examples/multi_agent.py) | Multi-agent coordination and task queues |
| [`session_graph_demo.py`](examples/session_graph_demo.py) | Session-based graph management |
| [`team_workflow.py`](examples/team_workflow.py) | Team collaboration with shared context |
| [`folder_ingestion_example.py`](examples/folder_ingestion_example.py) | Ingest documents from a folder |
| [`run_aiql_pipeline.py`](examples/run_aiql_pipeline.py) | AIQL pipeline queries |

---

## Performance

### Graph Operations (CSR in-memory backend)

| Operation | 1K nodes | 10K nodes | 50K nodes |
|-----------|----------|-----------|-----------|
| Node insert | ~25K ops/s | ~20K ops/s | ~15K ops/s |
| Edge insert | ~20K ops/s | ~15K ops/s | ~10K ops/s |
| Node lookup | ~500K ops/s | ~500K ops/s | ~500K ops/s |
| Neighbor traverse | ~200K ops/s | ~180K ops/s | ~150K ops/s |
| Memory per node | ~500 bytes | ~600 bytes | ~700 bytes |

### Performance Optimizations

| Component | Optimization | Impact |
|-----------|-------------|--------|
| **CSR Graph** | Compressed Sparse Row format, O(1) neighbor access | 10-100x vs NetworkX |
| **Lazy CSR Rebuild** | O(1) edge adds, deferred matrix construction | Fast writes, amortized reads |
| **Property Index** | Hash-based index on node properties | O(1) property lookups |
| **HNSW Vector Index** | Hierarchical Navigable Small World graph | 100x vs linear scan, sub-ms queries |
| **LMDB Search Index** | Persistent keyword + BM25 index | Microsecond reads, no rebuild cycle |
| **Query Cache** | LRU/LFU/TTL eviction, 5000 entries, 500MB cap | Avoid re-computing AIQL queries |
| **RAG Cache** | 2-tier (Redis + in-process LRU) | Skip redundant LLM calls |
| **Embedding Cache** | LMDB-backed vector cache | 3-5s saved per cache hit |
| **Write-Ahead Log** | ACID compliance with checkpoints | Crash recovery, 1000-op checkpoints |
| **Buffer Manager** | Configurable batching (70% threshold flush) | Smooth write latency |
| **Connection Pool** | LRU cache, 50 sessions, 30min timeout | Reuse graph connections |

### Search & Retrieval Latency

| Operation | Latency | Backend |
|-----------|---------|---------|
| Keyword search | ~1ms | Inverted index (cached) |
| BM25 full-text search | ~50ms | LMDB / Whoosh |
| Agent memory recall | ~10ms | Graph traversal |
| Working memory cache hit | ~5ms | Redis |
| Hot memory get/set | ~0.1ms | Redis hash |
| Vector search | 6-12s | Ollama embedding (local) |

---

## Agent Memory System

ContextSynapse provides a 4-tier memory system for AI agents:

| Tier | Purpose | Latency | Backend |
|------|---------|---------|---------|
| **Working Memory** | Per-agent context cache, reactive invalidation | ~5ms | Redis |
| **Hot Memory** | Current task, active state (TTL-based) | ~0.1ms | Redis hash |
| **Agent Memory** | Persistent facts, decisions, preferences with confidence decay | ~10ms | Graph |
| **Cold Memory** | Time-anchored, recall-at-timestamp, auto-decay | ~50ms | DuckDB |

```python
from contextsynapse.context.agent_memory import AgentMemory
from contextsynapse.core.registry import GraphRegistry

mem = AgentMemory(GraphRegistry(), namespace="shared_brain")

# Agent stores a fact
mem.remember("agent-1", "Revenue grew 8% YoY", tags=["fact", "revenue"], confidence=0.95)

# Another agent recalls it
memories = mem.recall("agent-2", query="revenue", limit=5)

# Build LLM context from agent's memories
hub = mem.build_context("agent-1", system_prompt="You are an analyst.")
messages = hub.to_messages()  # Ready for any LLM API
```

---

## Graph RAG

ContextSynapse's RAG pipeline combines vector similarity, BM25 full-text, and keyword matching via Reciprocal Rank Fusion:

```python
from contextsynapse.search.rag import hybrid_retrieve, plain_search, topic_scan

# Fast keyword search (no LLM needed, ~1ms)
results = plain_search(db, "connection pool incident", k=10)

# Topic clustering (no LLM needed)
topics = topic_scan(db, graph_name="knowledge_base", max_topics=10)

# Hybrid retrieval (vector + BM25 + keyword fusion)
top_results, sources = hybrid_retrieve(db, "What caused the outage?", k=5)
```

**Why Graph RAG > Vector RAG:** Regular RAG retrieves similar chunks. Graph RAG retrieves chunks *and* follows relationships — connecting incidents to root causes to runbooks. Relationships that pure vector similarity would miss.

---

## Context Quality Scoring

ContextSynapse doesn't just store context — it **measures whether context is any good.**

| Layer | What it does | Speed |
|-------|-------------|-------|
| **Ingest Gate** | Scores every node 0-100 (connectivity, specificity, source, completeness) | 300K scores/s |
| **Freshness Detection** | Extracts event dates, classifies as fresh/recent/aging/stale/historical | <1ms |
| **Quality Filter** | Blocks low-quality nodes before delivery to agents | instant |
| **Usage Tracking** | Measures if delivered context was actually used (relevance, waste ratio) | per-agent |
| **Promotion Scoring** | Auto-promotes high-value agent memories to main graph | on-demand |

```python
from contextsynapse.context.quality import score_node
from contextsynapse.intelligence.freshness import detect_event_date, score_freshness

# Score a node at ingest
score = score_node({"label": "Fact", "properties": {"statement": "Revenue grew 8%", "source_url": "..."}, "edge_count": 4})
# → 85 (high quality: sourced, connected, specific)

# Detect content freshness
result = detect_event_date("Tesla reported Q3 2026 results on August 15")
freshness = score_freshness(result.event_date)
# → freshness="recent", staleness_days=29
```

---

## Documentation

| Document | Description |
|----------|-------------|
| [AIQL Guide](docs/AIQL_GUIDE.md) | Complete AIQL query language reference |
| [Architecture](docs/ARCHITECTURE.md) | System architecture and design decisions |
| [Storage Formats](docs/STORAGE_FORMATS.md) | Storage backend details and configuration |
| [Cloud Deployment](docs/CLOUD_DEPLOYMENT.md) | Production deployment guide |
| [Contributing](CONTRIBUTING.md) | Development setup and contribution guidelines |
| [Changelog](CHANGELOG.md) | Version history |

---

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for development setup and guidelines.

## License

Apache 2.0 — see [LICENSE](LICENSE).

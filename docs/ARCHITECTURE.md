# AIContextDB Architecture Overview

## System Diagram

```
┌─────────────────────────────────────────────────────────┐
│                    Frontend (React)                      │
│  Pages / Components / CommandPalette / NotificationCenter│
└──────────────────────┬──────────────────────────────────┘
                       │ HTTP/REST
                       ▼
┌─────────────────────────────────────────────────────────┐
│                  API Layer (FastAPI)                      │
│  /api/v1/  │  Auth (4-tier)  │  Rate Limiting  │  CORS  │
│  Middleware │  Structured Logging (structlog)            │
└──────┬──────────┬──────────────┬────────────────────────┘
       │          │              │
       ▼          ▼              ▼
┌──────────┐ ┌──────────┐ ┌───────────┐
│   AIQL   │ │ Context  │ │   Jobs    │
│  Engine  │ │  Hub     │ │  (Async)  │
└────┬─────┘ └────┬─────┘ └─────┬─────┘
     │            │              │
     ▼            ▼              ▼
┌─────────────────────────────────────────────────────────┐
│                Core: AIContextDB / GraphRegistry             │
│           CSR Graph Storage  │  WAL  │  HNSW Index       │
└──────────────────────┬──────────────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────────────┐
│              Storage (Disk / Redis / Whoosh)              │
│  Checkpoints  │  Cache  │  Full-Text Index  │  Vectors   │
└─────────────────────────────────────────────────────────┘
```

## Directory Structure

```
contextsynapse/
  core/               # AIContextDB class, GraphRegistry, graph structures
    hybrid_graph_storage.py   # Main DB class (CSR-backed)
    registry.py               # Multi-graph management
    checkpoint.py             # Snapshot save/restore
  aiql/               # Query language stack
    grammar/          # Lark EBNF grammar definitions
    parser/           # String -> AST (AIQLParser)
    compiler/         # Validator -> Planner -> Optimizer
    engine/           # Executor (plan -> results)
  api/                # FastAPI server
    api.py            # Route definitions and middleware
  context/            # Context-as-a-Service
    hub.py            # ContextHub: build LLM context from graph data
  search/             # Full-text search (Whoosh)
  storage/            # CSR store, WAL, HNSW, document store
  jobs/               # Async job queue
  cache/              # Redis caching layer
  security/           # Auth, rate limiting, CORS
  models/             # Model registry, embedding service

frontend/src/
  pages/              # 22 route-level page components
  components/         # Shared UI (CommandPalette, NotificationCenter, etc.)
  context/            # React context providers (auth, theme, notifications)
```

## Key Subsystems

### AIQL Pipeline

The query language processes queries through four stages:

```
Query String
  -> Grammar (Lark EBNF)    # Tokenize and parse into parse tree
  -> Parser (AIQLParser)     # Transform parse tree into AST
  -> Compiler                # Validate -> Plan -> Optimize
  -> Executor                # Execute plan against AIContextDB, return results
```

The compiler has three passes: **Validator** checks semantic correctness, **Planner** converts the AST into a physical execution plan, and **Optimizer** rewrites the plan (predicate pushdown, index selection).

### Storage Engine

- **CSR (Compressed Sparse Row)**: Primary graph storage. Nodes and edges stored in compact arrays for cache-friendly traversal.
- **WAL (Write-Ahead Log)**: All mutations are logged before being applied, enabling crash recovery.
- **HNSW Index**: Approximate nearest-neighbor search for vector embeddings.
- **Checkpoints**: Periodic full snapshots of graph state for fast restore.

### Context Service (ContextHub)

Builds structured context payloads for LLM consumption:

1. Query the graph for relevant nodes/edges
2. Format results as context items with roles (system, retrieved, background)
3. Export as OpenAI/Anthropic message arrays or plain-text prompts
4. Session management tracks conversation state and agent registration

### Authentication (4-Tier)

| Tier     | Scope                        | Use Case                    |
|----------|------------------------------|-----------------------------|
| Tenant   | Organization-level isolation | Multi-tenant deployments    |
| User     | Individual user access       | Dashboard login             |
| Admin    | Full system access           | Configuration, management   |
| Agent    | Machine-to-machine tokens    | LLM agents, MCP clients    |

## Data Flow: Ingestion Pipeline

Documents flow through five stages before reaching the graph:

```
EXTRACT          # Parse raw input (PDF, text, URL) into content blocks
  -> CHUNK       # Split content into semantically coherent chunks
  -> EMBED       # Generate vector embeddings for each chunk
  -> EXTRACT_ENTITIES   # LLM-powered entity and relationship extraction
  -> PERSIST     # Write nodes, edges, and vectors to storage
```

Each stage is independently configurable and can be run as an async job. Pipeline progress is tracked and exposed via the monitoring API.

## External Integrations

- **MCP Server**: Exposes AIContextDB as a shared-brain tool for Claude and Copilot agents
- **Redis**: Caching layer for query results and session state
- **Whoosh**: Full-text search indexing for node/edge properties
- **LLM Providers**: OpenAI, Anthropic, and local models via universal adapter

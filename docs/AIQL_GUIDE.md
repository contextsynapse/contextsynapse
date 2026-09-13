# AIQL — AI Query Language Guide

AIQL is the query language for AIContextDB. It's Cypher-inspired with extensions for AI agent workflows, context management, and multi-agent collaboration.

## Quick Start

```python
from contextsynapse import AIContextDB
from contextsynapse.core.registry import GraphRegistry
from contextsynapse.aiql.engine.executor import AIQLExecutor

# Setup
registry = GraphRegistry()
db = registry.create_graph("my_project")
executor = AIQLExecutor(contextsynapse=db, graph_registry=registry)
executor.active_namespace = "my_project"

# Execute queries
result = executor.execute('CREATE NODE Person {name: "Alice", role: "Engineer"}')
result = executor.execute('SELECT * FROM Person')
```

Or via the REST API:

```bash
curl -X POST http://localhost:8000/aiql \
  -H "Content-Type: application/json" \
  -d '{"query": "CREATE NODE Person {name: \"Alice\"}", "namespace": "my_project"}'
```

Or via MCP (Claude Code / Copilot):

```
# In Claude Code, after adding the MCP server:
Use the query_graph tool with: CREATE NODE Person {name: "Alice"}
```

---

## Graph Management

### Create a Graph

```sql
CREATE GRAPH my_project
```

Creates a new named graph (namespace). Each graph is isolated — nodes and edges in one graph are not visible to another.

### Switch to a Graph

```sql
USE GRAPH my_project
```

Sets the active graph for subsequent queries.

### List Graphs

```sql
SHOW NAMESPACES
```

or

```sql
SHOW GRAPHS
```

### Delete a Graph

```sql
DELETE GRAPH my_project
```

or

```sql
DROP GRAPH my_project
```

Deletes the graph and all its data.

---

## Nodes

### Create a Node

```sql
CREATE NODE Person {name: "Alice", role: "Engineer", age: 30}
```

- `Person` is the **label** (type) of the node
- Properties are key-value pairs inside `{}`
- Values can be strings (`"..."`), numbers, or booleans
- The system auto-generates a UUID for each node

**Variations:**

```sql
-- Minimal (no properties)
CREATE NODE Milestone

-- Empty properties
CREATE NODE Tag {}

-- With many properties
CREATE NODE Task {
  title: "Build Auth",
  status: "open",
  priority: "high",
  estimated_hours: 40,
  assigned_to: "alice"
}
```

**Duplicate prevention:**

```sql
CREATE NODE Person {name: "Alice"} UNIQUE KEY (name)
```

If a Person with `name: "Alice"` already exists, the system returns the existing node instead of creating a duplicate.

### Query Nodes

```sql
-- All nodes
SELECT *

-- By label
SELECT * FROM Person

-- With property filter
SELECT * FROM Person WHERE role = "Engineer"

-- With LIMIT
SELECT * FROM Task WHERE status = "open" LIMIT 10
```

**Response format:**

```json
{
  "success": true,
  "nodes": [
    {
      "id": "6bb965cd-...",
      "label": "Person",
      "properties": {"name": "Alice", "role": "Engineer", "age": 30}
    }
  ]
}
```

### Update a Node

```sql
UPDATE NODE Task WHERE title = "Build Auth" SET {status: "in_progress", started_at: "2026-03-23"}
```

### Delete a Node

```sql
-- By UUID
DELETE NODE "6bb965cd-fe74-4a2f-8b1e-abc123def456"

-- All nodes of a type
DELETE ALL NODES WHERE label = "TempNode"
```

When deleting by UUID, connected edges are automatically removed.

---

## Edges

### Create an Edge

The most common syntax uses `FROM` and `TO` with node UUIDs:

```sql
CREATE EDGE WORKS_ON FROM "alice-uuid" TO "project-uuid" {role: "lead"}
```

- `WORKS_ON` is the edge label (relationship type)
- `FROM` specifies the source node UUID
- `TO` specifies the target node UUID
- Properties are optional

**Variations:**

```sql
-- No properties
CREATE EDGE KNOWS FROM "alice-uuid" TO "bob-uuid" {}

-- Arrow syntax (by node type, not UUID)
CREATE EDGE Person -> Project {type: "WORKS_ON"}

-- SRC/DEST syntax
CREATE EDGE DEPENDS_ON SRC "task1-uuid" DEST "task2-uuid" {blocking: true}
```

### Query Edges

```sql
SHOW EDGES
```

**Response:**

```json
{
  "success": true,
  "edges": [
    {
      "id": "edge-uuid",
      "source": "alice-uuid",
      "target": "project-uuid",
      "label": "WORKS_ON",
      "properties": {"role": "lead"}
    }
  ]
}
```

### Delete an Edge

```sql
DELETE EDGE "edge-uuid"
```

---

## Common Patterns

### Build a Team Graph

```sql
-- Create team members
CREATE NODE Person {name: "Alice", role: "Lead Engineer"}
CREATE NODE Person {name: "Bob", role: "Designer"}
CREATE NODE Person {name: "Charlie", role: "QA"}

-- Create project
CREATE NODE Project {name: "Atlas", deadline: "2026-Q3", budget: 2400000}

-- Create tasks
CREATE NODE Task {title: "Design API", status: "open", priority: "high"}
CREATE NODE Task {title: "Build Frontend", status: "open", priority: "medium"}
CREATE NODE Task {title: "Write Tests", status: "blocked", priority: "high"}

-- Create relationships
CREATE EDGE WORKS_ON FROM "alice-uuid" TO "project-uuid" {role: "lead"}
CREATE EDGE WORKS_ON FROM "bob-uuid" TO "project-uuid" {role: "designer"}
CREATE EDGE ASSIGNED_TO FROM "task1-uuid" TO "alice-uuid" {}
CREATE EDGE ASSIGNED_TO FROM "task2-uuid" TO "bob-uuid" {}
CREATE EDGE DEPENDS_ON FROM "task3-uuid" TO "task1-uuid" {reason: "needs API first"}

-- Record decisions
CREATE NODE Decision {title: "Use PostgreSQL", rationale: "Better scaling than MongoDB"}
CREATE EDGE IMPACTS FROM "decision-uuid" TO "project-uuid" {}
```

### Query the Team Graph

```sql
-- Who is on the project?
SELECT * FROM Person

-- What tasks are open?
SELECT * FROM Task WHERE status = "open"

-- What decisions were made?
SELECT * FROM Decision

-- Get everything
SELECT *
```

### SDLC Document Graph

```sql
-- Requirements
CREATE NODE Requirement {title: "User Authentication", priority: "critical", source: "PRD v2.1"}
CREATE NODE Requirement {title: "Data Export", priority: "medium", source: "Customer Feedback"}

-- Components
CREATE NODE Component {name: "AuthService", language: "Go", owner: "alice"}
CREATE NODE Component {name: "ExportService", language: "Python", owner: "bob"}

-- Link requirements to components
CREATE EDGE IMPLEMENTED_BY FROM "req1-uuid" TO "component1-uuid" {}
CREATE EDGE IMPLEMENTED_BY FROM "req2-uuid" TO "component2-uuid" {}

-- Risks
CREATE NODE Risk {description: "SSO integration may delay launch", severity: "high", mitigation: "Start early"}
CREATE EDGE AFFECTS FROM "risk-uuid" TO "req1-uuid" {}
```

---

## Using with AI Agents

### Via MCP (Claude Code)

After adding the AIContextDB MCP server:

```bash
claude mcp add contextsynapse -- python -m contextsynapse.mcp.server
```

Agents can use these tools:

| Tool | What it does |
|------|-------------|
| `query_graph` | Execute any AIQL query |
| `add_knowledge` | Add a node (simpler than raw AIQL) |
| `add_relationship` | Add an edge between nodes |
| `search_nodes` | Search nodes by label and properties |
| `briefing` | Get full project context |
| `ask` | Ask questions about the project |
| `claim_task` | Claim a task to work on |
| `complete_task` | Mark a task as done |

### Via Python SDK

```python
from contextsynapse.adapters._base import AIContextDBConnection

conn = AIContextDBConnection(namespace="my_project")

# Add nodes
alice = conn.add_node("Person", {"name": "Alice", "role": "Engineer"})
project = conn.add_node("Project", {"name": "Atlas"})

# Add edge
conn.add_edge(alice.id, project.id, "WORKS_ON", {"role": "lead"})

# Query
persons = conn.get_nodes(label="Person")
all_edges = conn.get_edges()

# AIQL query
result = conn.query("SELECT * FROM Task WHERE status = 'open'")

# Build LLM context
hub = conn.build_context(system_prompt="You are a project analyst.", max_tokens=4000)
messages = hub.to_messages()  # Ready for OpenAI/Anthropic API
```

### Via OpenAI Function Calling

```python
from contextsynapse.adapters.openai import configure, create_openai_tools, dispatch_tool_call

configure(namespace="my_project", agent_name="assistant")
tools = create_openai_tools()

# Pass tools to OpenAI
response = openai.chat.completions.create(
    model="gpt-4o",
    messages=[{"role": "user", "content": "What tasks are open?"}],
    tools=tools,
)

# Dispatch tool calls
for tool_call in response.choices[0].message.tool_calls:
    result = dispatch_tool_call(tool_call.function.name, tool_call.function.arguments)
```

---

## Multi-Worker / Production

### Start with Multiple Workers

```bash
python scripts/start_server.py --workers 4
```

Requires Redis for cross-worker state sync:

```bash
# Set in .env
AICONTEXTDB_REDIS_URL=redis://localhost:6379/0
```

The system automatically patches in-memory singletons (EventBus, Propagator, Gravity) with Redis bridges when multiple workers are detected.

### Cost Tracking

Every tool call is tracked through the Agent Gateway:

```sql
-- See your usage
-- (via MCP tool: my_usage)

-- See boundary costs
-- (via MCP tool: boundary_costs)
```

### Policy Enforcement

Set tool access policies per boundary:

```python
# Via API
PUT /dashboard/gateway/policy/{session_id}
{
    "deny_tools": ["ws_run_command"],
    "allow_categories": ["graph", "context", "task"],
    "max_tool_calls_per_minute": 60,
    "allow_external": true
}
```

---

## Reference: All AIQL Commands

### Graph Management
| Command | Description |
|---------|-------------|
| `CREATE GRAPH name` | Create a new graph namespace |
| `USE GRAPH name` | Switch to a graph |
| `SHOW NAMESPACES` | List all graphs |
| `SHOW GRAPHS` | List all graphs (alias) |
| `DROP GRAPH name` | Delete a graph |
| `DELETE GRAPH name` | Delete a graph (alias) |

### Node Operations
| Command | Description |
|---------|-------------|
| `CREATE NODE Label {props}` | Create a node |
| `CREATE NODE Label {}` | Create with no properties |
| `CREATE NODE Label` | Create with no properties |
| `CREATE NODE Label {props} UNIQUE KEY (field)` | Create or return existing |
| `SELECT *` | Get all nodes |
| `SELECT * FROM Label` | Get nodes by type |
| `SELECT * FROM Label WHERE field = "value"` | Filter nodes |
| `SELECT * FROM Label LIMIT 10` | Limit results |
| `UPDATE NODE Label WHERE field = "val" SET {new: "val"}` | Update properties |
| `DELETE NODE "uuid"` | Delete by ID |
| `DELETE ALL NODES WHERE condition` | Delete matching nodes |

### Edge Operations
| Command | Description |
|---------|-------------|
| `CREATE EDGE Label FROM "src-uuid" TO "tgt-uuid" {props}` | Create edge by UUIDs |
| `CREATE EDGE Label FROM "src" TO "tgt" {}` | Create edge, no props |
| `CREATE EDGE Src -> Tgt {type: "REL"}` | Arrow syntax |
| `SHOW EDGES` | List all edges |
| `DELETE EDGE "uuid"` | Delete an edge |

### Search and Traversal
| Command | Description |
|---------|-------------|
| `FIND NODES` | Find all nodes |
| `FIND NODES WHERE field = "val"` | Find with filter |
| `FIND EDGES` | Find all edges |
| `TRAVERSE FROM node VIA (EDGE_TYPE) DEPTH 3` | Graph traversal |
| `SHORTEST_PATH FROM "uuid1" TO "uuid2"` | Find shortest path |
| `NEIGHBORS FROM Label WHERE field = "val"` | Get neighbor nodes |

### Statistics
| Command | Description |
|---------|-------------|
| `SHOW STATS` | Graph statistics |
| `SHOW CURRENT GRAPH` | Current active graph |

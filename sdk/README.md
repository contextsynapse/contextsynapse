# AIContextDB SDK

Python SDK for [AIContextDB](https://github.com/qgraph/contextsynapse) — the shared context database for AI agents.

## Install

```bash
pip install contextsynapse-sdk
```

## Quick Start

```python
from contextsynapse_sdk import connect, AIContextDBAgent

# Connect to your AIContextDB server
agent = AIContextDBAgent("my-bot", server="https://your-server.com")

# Add knowledge
agent.add("User prefers dark mode and concise answers")
agent.add("Meeting notes: decided to use PostgreSQL for auth", label="meeting-2026-03-19")

# Get LLM-ready context
messages = agent.context(max_tokens=4000)
# → [{"role": "system", ...}, {"role": "user", ...}]

# Search
results = agent.search("user preferences")
```

## Agent Registration

Agents auto-register on first use. Credentials are saved to `~/.contextsynapse/` for reuse:

```python
# First run — registers and saves credentials
agent = AIContextDBAgent("my-bot", server="https://your-server.com")

# Subsequent runs — auto-loads saved credentials
agent = AIContextDBAgent("my-bot", server="https://your-server.com")
```

## Session Collaboration

Multiple agents share context through sessions:

```python
from contextsynapse_sdk import connect

# Agent A adds context
ctx = connect("https://your-server.com", agent_name="researcher")
session = ctx.sessions.create(name="project-alpha")
session.contribute(content="Found 3 critical bugs in auth module", role="background")

# Agent B reads context
ctx2 = connect("https://your-server.com", agent_name="developer")
sessions = ctx2.sessions.list()
session = ctx2.sessions.get(sessions[0].session_id)
messages = session.export(format="messages", max_tokens=4000)
```

## Real-time Events

```python
import asyncio

async def watch():
    async for event in session.subscribe():
        print(f"[{event.event_type}] {event.data}")

asyncio.run(watch())
```

## API Reference

### `connect(url, api_key=None, agent_name=None)`
Connect to AIContextDB. Auto-registers if `agent_name` provided.

### `AIContextDBAgent(name, server, platform="desktop")`
Zero-config agent wrapper. Handles registration, credentials, and session management.

### `agent.add(content, role="background", label=None)`
Add context to the shared session.

### `agent.context(format="messages", max_tokens=None)`
Export session context in LLM-ready format.

### `agent.search(query, top_k=10)`
Semantic search across the knowledge graph.

## License

MIT

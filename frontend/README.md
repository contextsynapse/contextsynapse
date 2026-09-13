# ContextSynapse Frontend

Dashboard UI for ContextSynapse — graph explorer, agent playground, context management, and monitoring.

## Quick Start

```bash
# Install dependencies
npm install

# Start development server (connects to API at localhost:8000)
npm start
# Opens at http://localhost:3000
```

## Production Build

```bash
npm run build
# Output in build/ — serve with any static file server
```

## Docker

```bash
# From repo root — starts API + Redis + Postgres + Frontend
docker compose --profile ui up -d
# Frontend at http://localhost:3000
```

## Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `REACT_APP_API_URL` | `http://localhost:8000` | Backend API URL |
| `PORT` | `3000` | Dev server port |

## Pages

The dashboard includes 40 core pages:

| Category | Pages |
|----------|-------|
| **Overview** | Overview, Graphs, Explorer, Context Lake |
| **Agents** | Agents, Agent Analytics, Playground, Sessions |
| **Data** | Ingest, Pipelines, Schemas, Chunking Config, Jobs |
| **Search** | Search, Query (AIQL), RAG, Compare |
| **Intelligence** | Intelligence, Cognition, Contexts, Context Page |
| **Monitoring** | Monitoring, Audit, Usage, API Docs |
| **Admin** | Settings, Team, User Management, API Keys, Profile |
| **Dev Tools** | Projects, Rule Builder, Automations, Integrations |
| **Build** | Canvas, Vertical Builder, Onboarding, Mobile Agent |

## Stack

- React 18
- React Router 6
- Cytoscape.js (graph visualization)
- Recharts (charts)
- D3.js (data viz)
- Framer Motion (animations)
- Lucide React (icons)
- Styled Components
- Axios (HTTP client)

## Plugin System

Vertical-specific pages (PMS, MF, etc.) are loaded via the plugin menu system.
They are NOT included in the open-source frontend — only the 40 core platform pages ship here.
Verticals add their own pages by registering with the sidebar navigation.

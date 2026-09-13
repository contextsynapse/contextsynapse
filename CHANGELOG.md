# Changelog

All notable changes to ContextSynapse will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.0.0] - 2026-09-12

### Added
- Core graph engine with CSR in-memory storage
- AIQL query language with SQL-like syntax, graph patterns, traversals, and hybrid search
- ContextHub for building LLM-ready context from graph data
- Plugin system for domain-specific extensions
- Multi-agent coordination with agent-to-agent protocol and task queues
- MCP server for Claude, Copilot, and other AI agent integration
- REST API with FastAPI
- SDK client package (`contextsynapse-sdk`)
- Framework adapters: LangChain, LangGraph, CrewAI, AutoGen, OpenAI, LlamaIndex, PydanticAI, Swarm
- Multiple storage backends: Redis, LMDB, PostgreSQL
- Vector database support: FAISS, ChromaDB, Qdrant
- Full-text search with Whoosh
- Entity and fact extraction
- Schema-driven ingestion pipeline
- RBAC and tenant isolation security
- Web-based dashboard frontend

### Details

#### AIQL Query Language
- Cypher-inspired query language with full pipeline support
- Commands: `CREATE NODE`, `SELECT`, `MATCH NODE`, `CREATE GRAPH`, `USE GRAPH`, `SHOW GRAPHS`
- Pipeline stages for chained transformations (filter, sort, limit, aggregate)
- Lark-based grammar with AST parser, validator, planner, and optimizer

#### Context-as-a-Service
- **ContextHub** for building structured LLM context from graph data
- Session management with scoping, conversation key-value store, and embedding hooks
- Agent lifecycle management (register, heartbeat, deregister)
- Export to OpenAI/Anthropic message formats or plain-text prompts

#### React Dashboard
- 22-page frontend with Neo4j-inspired dark theme
- Mobile responsive layout with adaptive sidebar and touch support
- **CommandPalette** (Ctrl+K) for keyboard-driven navigation
- **NotificationCenter** with real-time toast alerts and history
- Graph visualization, query editor, pipeline builder, and monitoring views

#### FastAPI Backend
- REST API with versioned routes under `/api/v1/`
- Multi-tier authentication: Tenant, User, Admin, and Agent roles
- Pipeline orchestration with async job queue for long-running tasks
- Redis caching layer for query results and session data
- Structured logging with **structlog** (JSON output, correlation IDs)
- Standardized error responses with error codes and request tracing

#### Search & Algorithms
- Full-text search powered by **Whoosh** with index management
- Graph algorithms API: shortest path, PageRank, community detection, centrality

#### Storage
- CSR (Compressed Sparse Row) graph storage engine
- Write-ahead log (WAL) for crash recovery
- HNSW index for approximate nearest-neighbor vector search
- Checkpoint/restore for graph snapshots

#### Security
- CORS lockdown with configurable origin allowlists
- JWT hardening with token rotation and expiry management
- Rate limiting per endpoint and per tenant
- Input validation and sanitization on all API routes

#### Infrastructure
- Pagination support across all list endpoints
- Health check and readiness probes
- MCP server integration for Claude/Copilot shared-brain access
- Environment-based configuration with `.env` support

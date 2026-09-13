# 📚 AIContextDB Documentation

Welcome to the AIContextDB documentation! This folder contains all guides, demos, technical docs, and implementation logs organized for easy access.

---

## 📖 Quick Navigation

### [User Guides](./guides/) - Learn How to Use AIContextDB

Essential documentation for developers and users:

| Guide | Description | For |
|-------|-------------|-----|
| [**AIQL Complete Guide**](./guides/AIQL_COMPLETE_GUIDE.md) | Comprehensive AIQL documentation | All users |
| [**Quick Reference**](./guides/AIQL_QUICK_REFERENCE.md) | Fast syntax lookup | Quick reference |
| [**AIQL LLM Integration**](./guides/AIQL_LLM_INTEGRATION.md) | Use LLMs directly in AIQL queries | AIQL users |
| [**LLM & Prompts (Python)**](./guides/LLM_AND_PROMPT_GUIDE.md) | OpenAI, Claude, Gemini, LLaMA in Python | Python developers |
| [**Embedding Storage**](./guides/EMBEDDING_STORAGE_GUIDE.md) | Store & search embeddings | RAG systems |
| [**Streaming & CDC**](./guides/STREAMING_INGESTION_CDC.md) | Real-time ingestion (Kafka, Pulsar, S3) | Data engineering |
| [**EVAL Guide**](./guides/EVAL_GUIDE.md) | Evaluate RAG & Agents | ML engineers |
| [**Backend CLI**](./guides/BACKEND_CLI_GUIDE.md) | Test operations before frontend | Integration testing |
| [**Regression Testing**](./guides/REGRESSION_TESTING_GUIDE.md) | Automated testing | DevOps, QA |
| [**Security Features**](./guides/SECURITY_FEATURES_GUIDE.md) | Enterprise security & compliance | Security engineers |

**Start here if you're**: New to AIContextDB, integrating AIContextDB, or building applications

---

### [Demos](./demos/) - See What AIContextDB Can Do

Advanced demonstrations showcasing unique capabilities:

| Demo | Description | Highlights |
|------|-------------|-----------|
| [**Advanced Demos**](./demos/ADVANCED_DEMOS.md) | RAG, Agents, Governance | Competitive advantages |

**Start here if you're**: Evaluating AIContextDB, comparing databases, or learning advanced features

---

### [Architecture](./architecture/) - Technical Deep Dives

Internal design, technical decisions, and advanced concepts:

| Document | Description | For |
|----------|-------------|-----|
| [**Package Structure**](./architecture/PACKAGE_STRUCTURE.md) | Code organization | Contributors |
| [**Enterprise AIQL Analysis**](./architecture/ENTERPRISE_AIQL_ANALYSIS.md) | Complex query patterns | Advanced users |
| [**Entity Identification**](./architecture/AIQL_ENTITY_IDENTIFICATION.md) | Entity recognition | NLP integration |
| [**Aggregation**](./architecture/AIQL_COMPREHENSIVE_AGGREGATION.md) | Aggregation features | Analytics |
| [**Graph Counting**](./architecture/AIQL_GRAPH_NATIVE_COUNTING.md) | Counting mechanisms | Performance tuning |
| [**Chunking Guide**](./architecture/REAL_WORLD_CHUNKING_GUIDE.md) | Document chunking | RAG optimization |
| [**Composite Queries**](./architecture/COMPOSITE_QGRQL.md) | Complex query patterns | Advanced queries |
| [**Enhanced Features**](./architecture/ENHANCED_QGRQL.md) | Extended AIQL | Power users |
| [**Security Architecture**](./architecture/SECURITY_ARCHITECTURE.md) | Multi-tenant security design | Security architects |

**Start here if you're**: Contributing code, optimizing performance, or understanding internals

---

### [Maintenance](./maintenance/) - Implementation Logs

Historical records of major implementations and changes:

| Log | Description | Date |
|-----|-------------|------|
| [**Cleanup Complete**](./maintenance/CLEANUP_COMPLETE.md) | Initial package cleanup | Oct 2025 |
| [**Neo4j References**](./maintenance/NEO4J_REFERENCES_CLEANUP.md) | Clarified Neo4j mentions | Oct 2025 |
| [**EVAL Implementation**](./maintenance/EVAL_IMPLEMENTATION_SUMMARY.md) | EVAL stage build log | Oct 2025 |
| [**Regression Tests**](./maintenance/REGRESSION_TEST_SUMMARY.md) | Test suite creation | Oct 2025 |
| [**AIQL Regression Tests**](./maintenance/AIQL_REGRESSION_TESTS_ADDED.md) | 38+ AIQL tests added | Oct 12, 2025 |
| [**Security Phase 1**](./maintenance/SECURITY_PHASE1_COMPLETE.md) | Security implementation | Oct 12, 2025 |
| [**LLM & Prompt System**](./maintenance/LLM_PROMPT_IMPLEMENTATION.md) | Multi-provider LLM integration | Oct 12, 2025 |
| [**Security Implementation**](./maintenance/SECURITY_IMPLEMENTATION_STATUS.md) | Security feature status | Oct 2025 |
| [**Docs Reorganization**](./maintenance/DOCUMENTATION_REORGANIZATION.md) | This structure | Oct 2025 |

**Start here if you're**: Reviewing project history, understanding evolution, or maintaining the project

---

## 🚀 Getting Started

### New to AIContextDB?
1. **[Quick Reference](./guides/AIQL_QUICK_REFERENCE.md)** - Get syntax basics
2. **[Advanced Demos](./demos/ADVANCED_DEMOS.md)** - See what's possible
3. **[Complete Guide](./guides/AIQL_COMPLETE_GUIDE.md)** - Deep dive

### Building a RAG System?
1. **[Embedding Storage](./guides/EMBEDDING_STORAGE_GUIDE.md)** - Store embeddings
2. **[EVAL Guide](./guides/EVAL_GUIDE.md)** - Evaluate performance
3. **[Advanced Demos](./demos/ADVANCED_DEMOS.md)** - RAG examples

### Integrating with Frontend?
1. **[Backend CLI](./guides/BACKEND_CLI_GUIDE.md)** - Test backend first
2. **[Complete Guide](./guides/AIQL_COMPLETE_GUIDE.md)** - API reference
3. **[Regression Testing](./guides/REGRESSION_TESTING_GUIDE.md)** - Ensure stability

### Setting Up Real-Time Data?
1. **[Streaming & CDC](./guides/STREAMING_INGESTION_CDC.md)** - Ingest data
2. **[Chunking Guide](./architecture/REAL_WORLD_CHUNKING_GUIDE.md)** - Process docs
3. **[Complete Guide](./guides/AIQL_COMPLETE_GUIDE.md)** - Query patterns

---

## 📊 Documentation Structure

```
docs/
├── README.md                    # This file - your starting point
├── guides/                      # User-facing documentation
│   ├── AIQL_COMPLETE_GUIDE.md   # Comprehensive reference
│   ├── AIQL_QUICK_REFERENCE.md  # Quick syntax lookup
│   ├── EMBEDDING_STORAGE_GUIDE.md
│   ├── STREAMING_INGESTION_CDC.md
│   ├── EVAL_GUIDE.md
│   ├── BACKEND_CLI_GUIDE.md
│   └── REGRESSION_TESTING_GUIDE.md
├── demos/                       # Advanced demonstrations
│   └── ADVANCED_DEMOS.md        # Showcase features
├── architecture/                # Technical documentation
│   ├── PACKAGE_STRUCTURE.md
│   ├── ENTERPRISE_AIQL_ANALYSIS.md
│   ├── AIQL_*.md                # AIQL internals
│   └── REAL_WORLD_CHUNKING_GUIDE.md
└── maintenance/                 # Implementation logs
    ├── CLEANUP_COMPLETE.md
    ├── EVAL_IMPLEMENTATION_SUMMARY.md
    └── REGRESSION_TEST_SUMMARY.md
```

---

## 🎯 Documentation by Role

### 👨‍💻 **Developers**
- [Complete Guide](./guides/AIQL_COMPLETE_GUIDE.md) - Full API reference
- [Quick Reference](./guides/AIQL_QUICK_REFERENCE.md) - Fast lookup
- [Backend CLI](./guides/BACKEND_CLI_GUIDE.md) - Testing tools

### 🧪 **Data Scientists / ML Engineers**
- [Embedding Storage](./guides/EMBEDDING_STORAGE_GUIDE.md) - Vector search
- [EVAL Guide](./guides/EVAL_GUIDE.md) - Evaluation metrics
- [Advanced Demos](./demos/ADVANCED_DEMOS.md) - RAG & Agents

### 🏗️ **Data Engineers**
- [Streaming & CDC](./guides/STREAMING_INGESTION_CDC.md) - Real-time ingestion
- [Chunking Guide](./architecture/REAL_WORLD_CHUNKING_GUIDE.md) - Data processing
- [Complete Guide](./guides/AIQL_COMPLETE_GUIDE.md) - Data operations

### ✅ **QA / DevOps**
- [Regression Testing](./guides/REGRESSION_TESTING_GUIDE.md) - Automated tests
- [Backend CLI](./guides/BACKEND_CLI_GUIDE.md) - Testing suite
- [Complete Guide](./guides/AIQL_COMPLETE_GUIDE.md) - API validation

### 🔍 **Evaluators / Decision Makers**
- [Advanced Demos](./demos/ADVANCED_DEMOS.md) - See capabilities
- [EVAL Guide](./guides/EVAL_GUIDE.md) - Benchmarking
- [Quick Reference](./guides/AIQL_QUICK_REFERENCE.md) - Feature overview

### 🛠️ **Contributors / Maintainers**
- [Package Structure](./architecture/PACKAGE_STRUCTURE.md) - Code organization
- [Maintenance Logs](./maintenance/) - Implementation history
- [Architecture Docs](./architecture/) - Technical design

---

## 💡 Common Tasks

### "How do I...?"

<details>
<summary><b>Query my graph database?</b></summary>

Start with [Quick Reference](./guides/AIQL_QUICK_REFERENCE.md), then dive into [Complete Guide](./guides/AIQL_COMPLETE_GUIDE.md)
</details>

<details>
<summary><b>Build a RAG system?</b></summary>

Follow: [Embedding Storage](./guides/EMBEDDING_STORAGE_GUIDE.md) → [EVAL Guide](./guides/EVAL_GUIDE.md) → [Advanced Demos](./demos/ADVANCED_DEMOS.md)
</details>

<details>
<summary><b>Set up streaming data ingestion?</b></summary>

Read [Streaming & CDC](./guides/STREAMING_INGESTION_CDC.md) for Kafka, Pulsar, S3/GCS, and database CDC
</details>

<details>
<summary><b>Test my backend before UI integration?</b></summary>

Use [Backend CLI](./guides/BACKEND_CLI_GUIDE.md) for comprehensive testing
</details>

<details>
<summary><b>Add automated tests?</b></summary>

Follow [Regression Testing](./guides/REGRESSION_TESTING_GUIDE.md) to set up CI/CD
</details>

<details>
<summary><b>Evaluate RAG performance?</b></summary>

See [EVAL Guide](./guides/EVAL_GUIDE.md) for metrics like P@K, NDCG, hallucination rate
</details>

<details>
<summary><b>Understand AIContextDB's architecture?</b></summary>

Start with [Package Structure](./architecture/PACKAGE_STRUCTURE.md), then explore other architecture docs
</details>

---

## 🔗 External Links

- **Main README**: [../README.md](../README.md) - Package overview
- **Examples**: [../AIContextDB/examples/](../AIContextDB/examples/) - Code samples
- **Tests**: [../tests/](../tests/) - Test suite
- **Source Code**: [../AIContextDB/](../AIContextDB/) - Core package

---

## 📝 Contributing to Documentation

### Adding New Guides
1. Place in appropriate folder (`guides/`, `demos/`, `architecture/`, `maintenance/`)
2. Update this README.md with link and description
3. Update main [README.md](../README.md) if user-facing
4. Follow existing format and structure

### Documentation Standards
- Use markdown format (`.md`)
- Include clear section headers
- Add code examples where relevant
- Link to related documentation
- Keep language clear and concise

### File Naming
- Use `UPPER_CASE_WITH_UNDERSCORES.md`
- Be descriptive: `FEATURE_GUIDE.md`, not `GUIDE.md`
- Include purpose: `IMPLEMENTATION_SUMMARY.md`, `TESTING_GUIDE.md`

---

## 📞 Questions?

- **Can't find what you need?** Check the [Complete Guide](./guides/AIQL_COMPLETE_GUIDE.md)
- **Need examples?** See [Advanced Demos](./demos/ADVANCED_DEMOS.md) or [../AIContextDB/examples/](../AIContextDB/examples/)
- **Found an issue?** Report on GitHub Issues
- **Want to contribute?** See [Package Structure](./architecture/PACKAGE_STRUCTURE.md)

---

## 📈 Documentation Statistics

- **Total Documents**: 21
- **User Guides**: 7
- **Demos**: 1
- **Architecture Docs**: 8
- **Maintenance Logs**: 5

**Last Updated**: October 12, 2025  
**Organization**: [DOCUMENTATION_REORGANIZATION.md](./maintenance/DOCUMENTATION_REORGANIZATION.md)

---

<div align="center">

**Happy coding with AIContextDB! 🚀**

[Back to Main README](../README.md)

</div>


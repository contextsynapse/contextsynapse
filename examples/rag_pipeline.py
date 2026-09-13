"""
RAG Pipeline Example
====================
Ingest text, build a knowledge graph, and use ContextHub for LLM-ready context.

Prerequisites:
    pip install -e "."
    Set OPENAI_API_KEY in .env (for embeddings/extraction)

Run:
    python examples/rag_pipeline.py
"""

from contextsynapse import AIContextDB, ContextHub
from contextsynapse.aiql.engine import AIQLExecutor

# Sample text to ingest
SAMPLE_TEXT = """
Artificial intelligence has transformed software engineering. Machine learning
models can now generate code, detect bugs, and optimize database queries.
Graph databases like AIContextDB store knowledge as interconnected nodes and edges,
enabling retrieval-augmented generation (RAG) for AI agents.

Key concepts:
- Knowledge graphs represent entities and their relationships
- Embeddings encode semantic meaning as vectors
- RAG retrieves relevant context before generating answers
- Context-as-a-Service provides shared memory for multi-agent systems
"""

# 1. Set up
db = AIContextDB()
ex = AIQLExecutor(contextsynapse=db)
ex.execute("CREATE GRAPH knowledge")
ex.execute("USE GRAPH knowledge")

# 2. Create nodes from the text
concepts = [
    ("Concept", {"name": "AI", "description": "Artificial intelligence transforms software"}),
    ("Concept", {"name": "Knowledge Graphs", "description": "Store entities and relationships"}),
    ("Concept", {"name": "RAG", "description": "Retrieval-augmented generation for AI"}),
    ("Concept", {"name": "Embeddings", "description": "Encode semantic meaning as vectors"}),
    ("Tool", {"name": "AIContextDB", "description": "Graph database for AI agents"}),
]

for label, props in concepts:
    props_str = ", ".join(f'{k}: "{v}"' for k, v in props.items())
    ex.execute(f"CREATE NODE {label} {{{props_str}}}")

# 3. Add relationships
ex.execute('CREATE EDGE ENABLES FROM Concept WHERE name = "AI" TO Concept WHERE name = "RAG"')
ex.execute('CREATE EDGE USES FROM Concept WHERE name = "RAG" TO Concept WHERE name = "Knowledge Graphs"')
ex.execute('CREATE EDGE USES FROM Concept WHERE name = "RAG" TO Concept WHERE name = "Embeddings"')
ex.execute('CREATE EDGE IMPLEMENTS FROM Tool WHERE name = "AIContextDB" TO Concept WHERE name = "Knowledge Graphs"')

# 4. Build LLM-ready context with ContextHub
hub = ContextHub(system_prompt="You are a knowledge graph expert. Answer using the provided context.")

# Add all nodes as context
result = ex.execute("SELECT * FROM Concept")
hub.add_nodes(result.get("nodes", []), role="retrieved")

# Add the original text as background
hub.add_text(SAMPLE_TEXT.strip(), role="background")

# 5. Export context
print("=== Messages format (for OpenAI/Anthropic API) ===")
messages = hub.to_messages()
for msg in messages:
    role = msg.get("role", "?")
    content = msg.get("content", "")[:100]
    print(f"  [{role}] {content}...")

print(f"\n=== Single prompt ({len(hub.to_prompt())} chars) ===")
print(hub.to_prompt()[:200] + "...")

# 6. Save context for later
hub.save("examples/rag_context.json")
print("\nContext saved to examples/rag_context.json")

# 7. Reload and verify
hub2 = ContextHub.load("examples/rag_context.json")
print(f"Reloaded context: {len(hub2.items)} items")

print("\nDone!")

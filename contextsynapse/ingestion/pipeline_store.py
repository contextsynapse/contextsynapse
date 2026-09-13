"""
Pipeline Store
==============
Stored-procedure-style pipeline definitions with metadata, run tracking,
governance tagging, and extraction accuracy scoring.

Pipelines are parameterized AIQL templates that can be:
  - Created, versioned, and shared across tenants
  - Executed with runtime parameters
  - Tracked per-run with stage-level metrics
  - Scored for extraction accuracy
  - Tagged for data governance and lineage
"""

from __future__ import annotations

import json
import logging
import secrets
import sqlite3
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

# Stage-level explanations (shared across pipelines)
STAGE_EXPLANATIONS = {
    "PARSE_FILE": "Reads the uploaded file (PDF, DOCX, Excel, CSV, etc.) and extracts raw content — text, tables, images.",
    "CLASSIFY": "Auto-detects the file structure: Is it a document? A spreadsheet with rows? A table with source/target columns? Picks the right processing path.",
    "CHUNK": "Splits long text into smaller pieces (chunks) so each piece fits in an LLM context window. Methods: paragraph (split by ¶), semantic (split at sentence boundaries), fixed (split every N chars).",
    "EXTRACT": "Sends each chunk to an LLM to extract named entities (people, systems, concepts) and relationships between them. Creates graph nodes and edges.",
    "EXTRACT_FACTS": "Sends each chunk to an LLM to extract factual statements — things the document states as true. Each fact becomes a Fact node in the graph.",
    "EXTRACT_ENTITIES": "Same as EXTRACT — extracts entities and relationships using LLM.",
    "EXTRACT_ENTITIES_AND_RELATIONSHIPS": "Extracts both entities AND their relationships in a single LLM pass. More comprehensive than separate extraction.",
    "EMBED": "Generates vector embeddings for each chunk using an embedding model (e.g. nomic-embed-text). Enables semantic/similarity search later.",
    "INDEX_BM25": "Builds a full-text search index (BM25/Whoosh) for keyword-based retrieval. Enables fast text search without vectors.",
    "STORE_VECTORS": "Stores the generated embeddings in the vector database (FAISS, Qdrant, or NumPy). Makes chunks searchable by meaning.",
    "MAP_TABULAR": "Converts spreadsheet data into graph nodes and edges. Detects column roles: source/target → edges, IDs → node keys, foreign keys → relationships.",
    "CANONICALIZE": "Deduplicates entities — 'John Smith' and 'J. Smith' merged into one node. Uses name similarity and LLM disambiguation.",
    "ENHANCE_GRAPH": "Adds inferred edges: co-occurrence links between entities mentioned together, hierarchical relationships, and cross-document connections.",
    "PERSIST": "Saves all created nodes and edges to the graph database (CSR storage + HDF5). Makes data queryable.",
}

BUILTIN_PIPELINES = [
    {
        "id": "builtin:smart",
        "name": "Smart Pipeline",
        "description": "Default pipeline for all content. Auto-detects input type and applies the right schema.",
        "explanation": "**What it does:** Auto-detects your content (articles, chat exports, documents, code) and applies the appropriate extraction schema. Chunks text, extracts entities/facts/relationships with LLM, generates embeddings, builds search indexes, and clusters into Context Units.\n\n**When to use:** Always. This is the recommended pipeline for everything — news, PDFs, chat history ZIPs, code files.\n\n**Accepts:** PDF, DOCX, TXT, HTML, CSV, JSON, ZIP (ChatGPT/Claude/Gemini exports)\n\n**Result:** Rich knowledge graph with entities, facts, relationships, semantic search, and Context Units.",
        "version": 1,
        "category": "ingestion",
        "schema_name": "",
        "parser": "",
        "filters": {},
        "graph_config": {},
        "_smart_pipeline": True,
        "stages": [
            {"name": "parse", "type": "PARSE_FILE", "config": {}},
            {"name": "classify", "type": "CLASSIFY", "config": {}},
            {"name": "chunk", "type": "CHUNK", "config": {"method": "semantic", "max_size": 1500}},
            {"name": "embed", "type": "EMBED", "config": {}},
            {"name": "extract", "type": "EXTRACT", "config": {}},
            {"name": "canonicalize", "type": "CANONICALIZE", "config": {}},
            {"name": "index", "type": "INDEX_BM25", "config": {}},
            {"name": "persist", "type": "PERSIST", "config": {}},
        ],
        "default_params": {
            "chunk_method": "semantic",
            "chunk_size": 1500,
            "llm_model": "groq:gpt-oss-120b",
            "embedding_model": "nomic-embed-text",
        },
        "aiql_template": "",
        "governance": {"owner": "system", "classification": "public", "approved_by": "system"},
        "tags": ["recommended", "auto", "smart", "default"],
        "builtin": True,
    },
    {
        "id": "builtin:basic",
        "name": "Basic Chunking",
        "description": "Split text into chunks. No LLM, no embeddings. Fast and free.",
        "explanation": "**What it does:** Takes your text and splits it into paragraph-sized chunks (~2000 chars each). Each chunk becomes a TextChunk node in the graph.\n\n**When to use:** Quick ingestion when you just want searchable text. No AI processing — instant and free.\n\n**Result:** Document → TextChunk nodes (keyword-searchable only, no semantic search).",
        "version": 1,
        "category": "ingestion",
        "schema_name": "",
        "parser": "",
        "filters": {},
        "graph_config": {},
        "stages": [
            {"name": "chunk", "type": "CHUNK", "config": {"method": "paragraph", "max_size": 2000}},
            {"name": "persist", "type": "PERSIST", "config": {}},
        ],
        "default_params": {"chunk_method": "paragraph", "chunk_size": 2000},
        "aiql_template": (
            'USE GRAPH {{graph}}\n'
            'INGEST TEXT {{source}} CHUNK BY {{chunk_method}}'
        ),
        "governance": {"owner": "system", "classification": "public", "approved_by": "system"},
        "tags": ["basic", "no-llm", "fast"],
        "builtin": True,
    },
    {
        "id": "builtin:semantic",
        "name": "Semantic Search Ready",
        "description": "Chunk text and generate embeddings for vector search. No entity extraction.",
        "explanation": "**What it does:** Splits text into smart chunks (at sentence boundaries), then generates vector embeddings for each chunk using an embedding model.\n\n**When to use:** You want to search by meaning ('find content about authentication') not just keywords. No entity extraction — just text chunks with vectors.\n\n**Result:** Document → TextChunk nodes with embeddings (semantic search + keyword search).",
        "version": 1,
        "category": "ingestion",
        "schema_name": "",
        "parser": "",
        "filters": {},
        "graph_config": {},
        "stages": [
            {"name": "chunk", "type": "CHUNK", "config": {"method": "semantic", "max_size": 1500}},
            {"name": "embed", "type": "EMBED", "config": {}},
            {"name": "persist", "type": "PERSIST", "config": {}},
        ],
        "default_params": {"chunk_method": "semantic", "chunk_size": 1500, "embedding_model": "nomic-embed-text"},
        "aiql_template": (
            'USE GRAPH {{graph}}\n'
            'INGEST TEXT {{source}} CHUNK BY {{chunk_method}}\n'
            'THEN EMBED USING MODEL "{{embedding_model}}"'
        ),
        "governance": {"owner": "system", "classification": "public", "approved_by": "system"},
        "tags": ["embeddings", "vector-search", "no-llm"],
        "builtin": True,
    },
    {
        "id": "builtin:knowledge-graph",
        "name": "Knowledge Graph",
        "description": "Extract entities and relationships with LLM to build a knowledge graph.",
        "explanation": "**What it does:** Chunks text, then sends each chunk to an LLM which identifies entities (people, systems, concepts) and how they relate. Creates a connected knowledge graph.\n\n**When to use:** You want to understand WHO, WHAT, and HOW things connect in your documents. Best for meeting notes, specs, reports.\n\n**Result:** Entity nodes (Person, System, Concept) connected by relationship edges (MAINTAINS, DEPENDS_ON, etc.).\n\n**Requires:** LLM (Groq, OpenAI, or Ollama).",
        "version": 1,
        "category": "ingestion",
        "schema_name": "",
        "parser": "",
        "filters": {},
        "graph_config": {},
        "stages": [
            {"name": "chunk", "type": "CHUNK", "config": {"method": "paragraph", "max_size": 2000}},
            {"name": "extract_entities", "type": "EXTRACT_ENTITIES", "config": {}},
            {"name": "enhance", "type": "ENHANCE_GRAPH", "config": {}},
            {"name": "persist", "type": "PERSIST", "config": {}},
        ],
        "default_params": {"chunk_method": "paragraph", "chunk_size": 2000, "llm_model": "groq:gpt-oss-120b"},
        "aiql_template": (
            'USE GRAPH {{graph}}\n'
            'INGEST TEXT {{source}} CHUNK BY {{chunk_method}}\n'
            'THEN EXTRACT ENTITIES USING LLM "{{llm_model}}"\n'
            'THEN ENHANCE_GRAPH'
        ),
        "governance": {"owner": "system", "classification": "internal", "approved_by": "system"},
        "tags": ["knowledge-graph", "entities", "relationships", "llm"],
        "builtin": True,
    },
    {
        "id": "builtin:full",
        "name": "Full Pipeline",
        "description": "Complete pipeline: chunk, embed, extract entities & relationships, enhance graph.",
        "explanation": "**What it does:** Everything — chunks text, generates embeddings, extracts entities and relationships with LLM, deduplicates, and adds inferred connections.\n\n**When to use:** You want the richest possible graph from your documents. Best for important docs where you need both semantic search AND knowledge graph traversal.\n\n**Result:** TextChunks (with embeddings) + Entity nodes + Relationship edges + Co-occurrence links.\n\n**Requires:** LLM + Embedding model. Slower but most comprehensive.",
        "version": 1,
        "category": "ingestion",
        "schema_name": "",
        "parser": "",
        "filters": {},
        "graph_config": {},
        "stages": [
            {"name": "chunk", "type": "CHUNK", "config": {"method": "semantic", "max_size": 1500}},
            {"name": "embed", "type": "EMBED", "config": {}},
            {"name": "extract_entities", "type": "EXTRACT_ENTITIES_AND_RELATIONSHIPS", "config": {}},
            {"name": "enhance", "type": "ENHANCE_GRAPH", "config": {}},
            {"name": "persist", "type": "PERSIST", "config": {}},
        ],
        "default_params": {
            "chunk_method": "semantic",
            "chunk_size": 1500,
            "llm_model": "groq:gpt-oss-120b",
            "embedding_model": "nomic-embed-text",
        },
        "aiql_template": (
            'USE GRAPH {{graph}}\n'
            'INGEST TEXT {{source}} CHUNK BY {{chunk_method}}\n'
            'THEN EMBED USING MODEL "{{embedding_model}}"\n'
            'THEN EXTRACT ENTITIES AND RELATIONSHIPS USING LLM "{{llm_model}}"\n'
            'THEN ENHANCE_GRAPH'
        ),
        "governance": {"owner": "system", "classification": "internal", "approved_by": "system"},
        "tags": ["full", "knowledge-graph", "embeddings", "llm"],
        "builtin": True,
    },
    {
        "id": "builtin:schema-guided",
        "name": "Schema-Guided Extraction",
        "description": "Extract entities constrained to a YAML schema. Best for structured domains.",
        "explanation": "**What it does:** Like Knowledge Graph, but the LLM is constrained to only extract entity types defined in your schema (e.g. Patient, Condition, Treatment for healthcare). Produces a cleaner, more structured graph.\n\n**When to use:** You have domain-specific documents and want consistent entity types. Select an industry schema or create a custom one.\n\n**Result:** Typed entities matching your schema + relationships + embeddings.\n\n**Requires:** LLM + Embedding model + Extraction schema (auto-detected from context tags).",
        "version": 1,
        "category": "ingestion",
        "schema_name": "",
        "parser": "",
        "filters": {},
        "graph_config": {},
        "stages": [
            {"name": "chunk", "type": "CHUNK", "config": {"method": "paragraph", "max_size": 2000}},
            {"name": "embed", "type": "EMBED", "config": {}},
            {"name": "extract_entities", "type": "EXTRACT_ENTITIES", "config": {"schema_guided": True}},
            {"name": "enhance", "type": "ENHANCE_GRAPH", "config": {}},
            {"name": "persist", "type": "PERSIST", "config": {}},
        ],
        "default_params": {
            "chunk_method": "paragraph",
            "chunk_size": 2000,
            "llm_model": "groq:gpt-oss-120b",
            "embedding_model": "nomic-embed-text",
        },
        "aiql_template": (
            'USE GRAPH {{graph}}\n'
            'INGEST TEXT {{source}} CHUNK BY {{chunk_method}}\n'
            'THEN EMBED USING MODEL "{{embedding_model}}"\n'
            'THEN EXTRACT ENTITIES USING LLM "{{llm_model}}" WITH SCHEMA {{schema}}\n'
            'THEN ENHANCE_GRAPH'
        ),
        "governance": {"owner": "system", "classification": "internal", "approved_by": "system"},
        "tags": ["schema", "structured", "knowledge-graph", "llm"],
        "builtin": True,
    },
    # --- Scenario-driven pipelines (file-aware) ---
    {
        "id": "builtin:structured-graph",
        "name": "Structured Graph (Excel/CSV)",
        "description": "Parse Excel/CSV with source/target/relation columns directly into a graph. No LLM needed.",
        "schema_name": "",
        "parser": "",
        "filters": {},
        "graph_config": {},
        "explanation": "**What it does:** Reads an Excel/CSV file that has columns like 'Source', 'Target', 'Relation'. Each row becomes an edge in the graph, with source and target as nodes.\n\n**When to use:** Your data is already structured as relationships — org charts, dependency maps, network diagrams, adjacency lists.\n\n**Example input:**\n| Source | Target | Relation | Weight |\n| Alice | Auth | MAINTAINS | high |\n| Auth | Billing | DEPENDS_ON | critical |\n\n**Result:** Nodes (Alice, Auth, Billing) + Edges (MAINTAINS, DEPENDS_ON) with properties.\n\n**Requires:** Nothing — no LLM, no embeddings. Instant.",
        "version": 1,
        "category": "ingestion",
        "stages": [
            {"name": "parse_file", "type": "PARSE_FILE", "config": {}},
            {"name": "classify", "type": "CLASSIFY", "config": {}},
            {"name": "map_tabular", "type": "MAP_TABULAR", "config": {}},
            {"name": "persist", "type": "PERSIST", "config": {}},
        ],
        "default_params": {},
        "aiql_template": (
            'USE GRAPH {{graph}}\n'
            'LOAD FILE {{source}}\n'
            'THEN MAP_TABULAR\n'
            'THEN PERSIST'
        ),
        "governance": {"owner": "system", "classification": "public", "approved_by": "system"},
        "tags": ["structured", "excel", "csv", "no-llm", "fast", "tabular"],
        "builtin": True,
    },
    {
        "id": "builtin:record-to-graph",
        "name": "Record to Graph",
        "description": "Convert flat records (Excel/CSV rows) into graph nodes with FK-derived edges and embeddings.",
        "schema_name": "",
        "parser": "",
        "filters": {},
        "graph_config": {},
        "explanation": "**What it does:** Each row in your spreadsheet becomes a node. Columns ending in '_id' or '_key' are treated as foreign keys — they create edges to other nodes. Embeddings are generated for search.\n\n**When to use:** You have a flat table (customer list, product catalog, inventory) and want to create a searchable graph with auto-discovered relationships.\n\n**Example input:**\n| customer_id | name | order_id | product |\n| C001 | Alice | O123 | Widget |\n\n**Result:** Customer nodes linked to Order nodes via FK edges + embeddings.\n\n**Requires:** Embedding model (optional).",
        "version": 1,
        "category": "ingestion",
        "stages": [
            {"name": "parse_file", "type": "PARSE_FILE", "config": {}},
            {"name": "classify", "type": "CLASSIFY", "config": {}},
            {"name": "map_tabular", "type": "MAP_TABULAR", "config": {}},
            {"name": "canonicalize", "type": "CANONICALIZE", "config": {}},
            {"name": "embed", "type": "EMBED", "config": {}},
            {"name": "persist", "type": "PERSIST", "config": {}},
        ],
        "default_params": {"embedding_model": "nomic-embed-text"},
        "aiql_template": (
            'USE GRAPH {{graph}}\n'
            'LOAD FILE {{source}}\n'
            'THEN MAP_TABULAR\n'
            'THEN CANONICALIZE\n'
            'THEN EMBED USING MODEL "{{embedding_model}}"\n'
            'THEN PERSIST'
        ),
        "governance": {"owner": "system", "classification": "public", "approved_by": "system"},
        "tags": ["records", "excel", "csv", "embeddings", "tabular"],
        "builtin": True,
    },
    {
        "id": "builtin:table-extraction",
        "name": "Table Extraction",
        "description": "Extract tables from documents (PDF, DOCX), map to graph, and enrich with LLM.",
        "schema_name": "",
        "parser": "",
        "filters": {},
        "graph_config": {},
        "explanation": "**What it does:** Finds tables inside PDF/DOCX documents, maps them to graph nodes/edges (like Structured Graph), then uses LLM to extract additional entities from the surrounding text.\n\n**When to use:** Your documents contain embedded tables (financial reports, spec sheets with data tables) that should become graph structure.\n\n**Result:** Table rows → nodes + edges, plus LLM-extracted entities from text content + embeddings.\n\n**Requires:** LLM + Embedding model.",
        "version": 1,
        "category": "ingestion",
        "stages": [
            {"name": "parse_file", "type": "PARSE_FILE", "config": {}},
            {"name": "classify", "type": "CLASSIFY", "config": {}},
            {"name": "map_tabular", "type": "MAP_TABULAR", "config": {}},
            {"name": "extract", "type": "EXTRACT", "config": {}},
            {"name": "embed", "type": "EMBED", "config": {}},
            {"name": "persist", "type": "PERSIST", "config": {}},
        ],
        "default_params": {"llm_model": "groq:gpt-oss-120b", "embedding_model": "nomic-embed-text"},
        "aiql_template": (
            'USE GRAPH {{graph}}\n'
            'LOAD FILE {{source}}\n'
            'THEN MAP_TABULAR\n'
            'THEN EXTRACT ENTITIES USING LLM "{{llm_model}}"\n'
            'THEN EMBED USING MODEL "{{embedding_model}}"\n'
            'THEN PERSIST'
        ),
        "governance": {"owner": "system", "classification": "internal", "approved_by": "system"},
        "tags": ["tables", "pdf", "docx", "llm", "embeddings"],
        "builtin": True,
    },
    {
        "id": "builtin:auto",
        "name": "Auto-Detect Pipeline",
        "description": "Automatically classify the input file and select the best pipeline. Recommended for most use cases.",
        "schema_name": "",
        "parser": "",
        "filters": {},
        "graph_config": {},
        "explanation": "**What it does:** Reads your file, auto-detects its type (document, spreadsheet, table-heavy), and picks the best pipeline automatically.\n\n**When to use:** You're not sure which pipeline to pick. Let the system decide.\n\n**How it decides:**\n- Excel/CSV with source/target columns → Structured Graph\n- Excel/CSV with flat rows → Record to Graph\n- PDF/DOCX with tables → Table Extraction\n- PDF/DOCX with text → Multi-Layer Extraction\n- Plain text → Knowledge Graph\n\n**Requires:** LLM + Embedding model (for full extraction).",
        "version": 1,
        "category": "ingestion",
        "stages": [
            {"name": "parse_file", "type": "PARSE_FILE", "config": {}},
            {"name": "classify", "type": "CLASSIFY", "config": {}},
            # Remaining stages determined by ScenarioRouter at runtime
        ],
        "default_params": {"llm_model": "groq:gpt-oss-120b", "embedding_model": "nomic-embed-text"},
        "aiql_template": (
            'USE GRAPH {{graph}}\n'
            'RUN AUTO PIPELINE ON FILE {{source}}'
        ),
        "governance": {"owner": "system", "classification": "public", "approved_by": "system"},
        "tags": ["auto", "smart", "recommended"],
        "builtin": True,
    },
    # --- Smart Article Pipeline (new 7-stage) ---
    {
        "id": "builtin:smart-article",
        "name": "Smart Article Ingestion",
        "schema_name": "",
        "parser": "",
        "filters": {},
        "graph_config": {},
        "description": (
            "For news articles, blog posts, and web pages. Uses Trafilatura for HTML cleaning, "
            "hybrid paragraph chunking, local LLM for entity/fact/relationship extraction, "
            "and builds a rich knowledge graph with Passages, Entities, Facts, and Links."
        ),
        "explanation": (
            "**What it does:** The smartest ingestion pipeline. 7 stages:\n\n"
            "1. **Clean** \u2014 Trafilatura strips HTML/nav/ads, extracts article body + metadata + links\n"
            "2. **Chunk** \u2014 Hybrid paragraph chunker (merge short, split long, sentence overlap)\n"
            "3. **Extract** \u2014 Per-chunk LLM extraction (entities, facts, relationships) + regex supplement\n"
            "4. **Link** \u2014 Create graph: Document, Passage, Entity, Fact, Link nodes + all edges\n"
            "5. **Embed** \u2014 Vector embed each Passage + Entity for semantic search\n"
            "6. **Index** \u2014 BM25 index for keyword search\n"
            "7. **Validate** \u2014 Schema validation + dedup\n\n"
            "**When to use:** Best quality for web content. Produces the richest graph.\n\n"
            "**Result:** Document \u2192 Passages (with overlap) \u2192 Entities (deduped) \u2192 Facts (with provenance) + Links + typed relationship edges.\n\n"
            "**Requires:** LLM (local Ollama = free, or cloud)."
        ),
        "version": 1,
        "category": "ingestion",
        "stages": [
            {"name": "clean", "type": "CLEAN", "config": {"method": "trafilatura"}},
            {"name": "chunk", "type": "CHUNK", "config": {"method": "hybrid_paragraph", "overlap": True}},
            {"name": "extract", "type": "EXTRACT", "config": {"method": "llm+regex", "per_chunk": True}},
            {"name": "link", "type": "LINK", "config": {"edges": ["CONTAINS", "NEXT", "MENTIONS", "STATES", "DERIVED_FROM", "LINKS_TO"]}},
            {"name": "embed", "type": "EMBED", "config": {"targets": ["Passage", "Entity"]}},
            {"name": "index", "type": "INDEX_BM25", "config": {"targets": ["Passage", "Entity", "Fact"]}},
            {"name": "validate", "type": "VALIDATE_SCHEMA", "config": {}},
        ],
        "default_params": {},
        "aiql_template": "",
        "governance": {"owner": "system", "classification": "public", "approved_by": "system"},
        "tags": ["smart", "article", "web", "llm", "recommended"],
        "builtin": True,
        "_smart_pipeline": "smart_article",
    },
    {
        "id": "builtin:smart-fast",
        "name": "Fast Ingest (No LLM)",
        "schema_name": "",
        "parser": "",
        "filters": {},
        "graph_config": {},
        "description": "Regex-only extraction, no LLM calls. Fastest pipeline for bulk imports where speed matters more than extraction depth.",
        "explanation": (
            "**What it does:** Same chunking as Smart Article but uses only regex for extraction (no LLM).\n\n"
            "**When to use:** Bulk imports, low-priority content, or when no LLM is available.\n\n"
            "**Result:** Document \u2192 Passages + basic entities (names, emails, dates).\n\n"
            "**Requires:** Nothing \u2014 no LLM, no embeddings."
        ),
        "version": 1,
        "category": "ingestion",
        "stages": [
            {"name": "clean", "type": "CLEAN", "config": {}},
            {"name": "chunk", "type": "CHUNK", "config": {}},
            {"name": "extract", "type": "EXTRACT", "config": {"method": "regex_only"}},
            {"name": "link", "type": "LINK", "config": {}},
            {"name": "validate", "type": "VALIDATE_SCHEMA", "config": {}},
        ],
        "default_params": {},
        "aiql_template": "",
        "governance": {"owner": "system", "classification": "public", "approved_by": "system"},
        "tags": ["fast", "no-llm", "bulk"],
        "builtin": True,
        "_smart_pipeline": "fast_ingest",
    },
    # --- Multi-Layer Graph-Based Extraction ---
    {
        "id": "builtin:multi-layer-extraction",
        "name": "Multi-Layer Graph-Based Extraction",
        "schema_name": "",
        "parser": "",
        "filters": {},
        "graph_config": {},
        "description": (
            "Multi-layer extraction: Document \u2192 Passage \u2192 Fact \u2192 Entity hierarchy. "
            "Passages are embedded and stored in vector DB. "
            "Facts are BM25-indexed for keyword search. "
            "Entities are linked across the entire graph for traversal-based retrieval."
        ),
        "explanation": "**What it does:** Creates a 3-layer knowledge hierarchy:\n\n1. **Passages** — text chunks (embedded for vector search)\n2. **Facts** — individual factual statements extracted by LLM (BM25-indexed)\n3. **Entities** — named things with relationships (graph-traversable)\n\nEach layer is connected: Passage → STATES → Fact → MENTIONS → Entity.\n\n**When to use:** Maximum retrieval quality. RAG queries can search by meaning (vectors), keywords (BM25), or graph traversal (follow edges). Best for important knowledge bases.\n\n**Result:** Document → Passages → Facts → Entities, all cross-linked.\n\n**Requires:** LLM + Embedding model. Most comprehensive but slowest.",
        "version": 1,
        "category": "ingestion",
        "stages": [
            {"name": "parse_file", "type": "PARSE_FILE", "config": {}},
            {"name": "classify", "type": "CLASSIFY", "config": {}},
            {"name": "chunk", "type": "CHUNK", "config": {"method": "paragraph", "max_size": 1500}},
            {"name": "extract_facts", "type": "EXTRACT_FACTS", "config": {}},
            {"name": "extract_entities", "type": "EXTRACT", "config": {}},
            {"name": "embed", "type": "EMBED", "config": {}},
            {"name": "index_bm25", "type": "INDEX_BM25", "config": {}},
            {"name": "store_vectors", "type": "STORE_VECTORS", "config": {}},
            {"name": "canonicalize", "type": "CANONICALIZE", "config": {}},
            {"name": "enhance_graph", "type": "ENHANCE_GRAPH", "config": {}},
            {"name": "persist", "type": "PERSIST", "config": {}},
        ],
        "default_params": {
            "llm_model": "groq:gpt-oss-120b",
            "embedding_model": "nomic-embed-text",
            "chunk_method": "paragraph",
            "chunk_size": 1500,
        },
        "aiql_template": (
            'USE GRAPH {{graph}}\n'
            'LOAD FILE {{source}}\n'
            'THEN CHUNK BY {{chunk_method}}\n'
            'THEN EXTRACT FACTS USING LLM "{{llm_model}}"\n'
            'THEN EXTRACT ENTITIES USING LLM "{{llm_model}}"\n'
            'THEN EMBED USING MODEL "{{embedding_model}}"\n'
            'THEN INDEX_BM25\n'
            'THEN STORE_VECTORS\n'
            'THEN CANONICALIZE\n'
            'THEN ENHANCE_GRAPH\n'
            'THEN PERSIST'
        ),
        "governance": {"owner": "system", "classification": "internal", "approved_by": "system"},
        "tags": ["multi-layer", "extraction", "facts", "bm25", "vector", "knowledge-graph", "rag"],
        "builtin": True,
    },
    # --- Software Development / SDLC ---
    {
        "id": "builtin:sdlc-graph-rag",
        "name": "SDLC Graph RAG",
        "schema_name": "",
        "parser": "",
        "filters": {},
        "graph_config": {},
        "description": (
            "Extract software development artifacts — features, requirements, components, APIs, "
            "data models, risks, stakeholders — from project specs and design docs. "
            "Uses the SDLC extraction schema for structured, schema-guided extraction."
        ),
        "explanation": "**What it does:** Specialized for software projects. Extracts:\n- Requirements, Features, User Stories\n- System components, APIs, Endpoints\n- Data models, Schemas\n- Risks, Stakeholders, Decisions\n\nUses the SDLC extraction schema to constrain the LLM to only extract software-relevant entities.\n\n**When to use:** Ingesting project specs, PRDs, design docs, API documentation. The extracted graph powers agent briefings and task planning.\n\n**Result:** Typed software entities + relationships + facts + embeddings.\n\n**Requires:** LLM + Embedding model.",
        "version": 1,
        "category": "ingestion",
        "stages": [
            {"name": "parse_file", "type": "PARSE_FILE", "config": {}},
            {"name": "classify", "type": "CLASSIFY", "config": {}},
            {"name": "chunk", "type": "CHUNK", "config": {"method": "paragraph", "max_size": 2000}},
            {"name": "extract_facts", "type": "EXTRACT_FACTS", "config": {}},
            {"name": "extract_entities", "type": "EXTRACT", "config": {"schema_guided": True}},
            {"name": "embed", "type": "EMBED", "config": {}},
            {"name": "index_bm25", "type": "INDEX_BM25", "config": {}},
            {"name": "store_vectors", "type": "STORE_VECTORS", "config": {}},
            {"name": "canonicalize", "type": "CANONICALIZE", "config": {}},
            {"name": "enhance_graph", "type": "ENHANCE_GRAPH", "config": {}},
            {"name": "persist", "type": "PERSIST", "config": {}},
        ],
        "default_params": {
            "llm_model": "groq:gpt-oss-120b",
            "embedding_model": "nomic-embed-text",
            "chunk_method": "paragraph",
            "chunk_size": 2000,
        },
        "aiql_template": (
            'USE GRAPH {{graph}}\n'
            'LOAD FILE {{source}}\n'
            'THEN CHUNK BY {{chunk_method}}\n'
            'THEN EXTRACT FACTS USING LLM "{{llm_model}}"\n'
            'THEN EXTRACT ENTITIES USING LLM "{{llm_model}}" WITH SCHEMA sdlc\n'
            'THEN EMBED USING MODEL "{{embedding_model}}"\n'
            'THEN INDEX_BM25\n'
            'THEN STORE_VECTORS\n'
            'THEN CANONICALIZE\n'
            'THEN ENHANCE_GRAPH\n'
            'THEN PERSIST'
        ),
        "governance": {"owner": "system", "classification": "internal", "approved_by": "system"},
        "tags": ["sdlc", "software", "schema", "extraction", "facts", "knowledge-graph", "rag", "recommended"],
        "builtin": True,
    },
    {
        "id": "builtin:conversation",
        "name": "Conversation Import",
        "description": "For ChatGPT/Claude/Gemini exports. TurnParser + full graph construction with entity resolution and domain inference.",
        "explanation": "**What it does:** Parses chat conversations into turns, detects signals (decisions/problems/solutions), extracts entities, resolves duplicates across conversations, builds schema-driven edges, infers domains, and clusters into Context Units.\n\n**When to use:** Upload a ChatGPT or Claude export (ZIP or JSON). This pipeline understands conversation structure.\n\n**Accepts:** JSON, ZIP (conversation exports)\n\n**Result:** Rich entity-centric graph with cross-conversation linking and domain clustering.",
        "version": 1,
        "category": "ingestion",
        "schema_name": "claude_conversation",
        "parser": "turn",
        "filters": {"min_length": 10},
        "graph_config": {
            "entity_resolution": True,
            "co_occurrence_threshold": 2,
            "domain_inference": True,
            "context_units": True,
        },
        "stages": [
            {"name": "filter", "type": "filter_content", "config": {}},
            {"name": "signals", "type": "detect_signals", "config": {}},
            {"name": "entities", "type": "extract_entities", "config": {}},
            {"name": "store", "type": "store_documents", "config": {}},
            {"name": "resolve", "type": "resolve_entities", "config": {}},
            {"name": "edges", "type": "build_edges", "config": {}},
            {"name": "domains", "type": "infer_domains", "config": {}},
            {"name": "topics", "type": "cluster_topics", "config": {}},
            {"name": "xref", "type": "link_cross_reference", "config": {}},
            {"name": "validate", "type": "validate_gate", "config": {}},
            {"name": "dedup", "type": "deduplicate", "config": {}},
            {"name": "cu", "type": "synthesize_cu", "config": {}},
            {"name": "embed", "type": "EMBED", "config": {}},
            {"name": "index", "type": "INDEX_BM25", "config": {}},
        ],
        "default_params": {"llm_model": "openai:gpt-4o-mini", "embedding_model": "nomic-embed-text"},
        "aiql_template": "",
        "governance": {"owner": "system", "classification": "public", "approved_by": "system"},
        "tags": ["conversation", "chat", "claude", "chatgpt", "import"],
        "builtin": True,
    },
    {
        "id": "builtin:sdlc-quickscan",
        "name": "SDLC Quick Scan (No Clone)",
        "description": "Quick scan via GitHub API — no clone needed. Fetches README, issues, PRs, contributors, metadata in ~5 seconds.",
        "explanation": "**What it does:** Uses GitHub REST API to fetch:\n- README → Requirements\n- Open + closed issues → Requirements / KnownIssues\n- Pull requests → ChangeRecords (what's in progress)\n- Contributors → who built what\n- Repo metadata → stars, forks, branches, tags, language\n\n**When to use:** Quick overview of any GitHub repository. No clone, no disk usage, instant.\n\n**What you DON'T get:** Source code analysis, test file scanning, API route detection, import analysis.\n\n**Result:** Intent + verify + evolution layers populated. Build layer empty (use Full Scan for that).",
        "version": 1,
        "category": "ingestion",
        "schema_name": "",
        "parser": "",
        "filters": {},
        "graph_config": {},
        "stages": [
            {"name": "sdlc_scan", "type": "SDLC_SCAN", "config": {}},
        ],
        "default_params": {
            "scan_mode": "quick",
            "max_cus": 8,
        },
        "aiql_template": "",
        "governance": {"owner": "system", "classification": "public", "approved_by": "system"},
        "tags": ["sdlc", "repo", "quick", "api", "no-clone", "recommended", "default"],
        "builtin": True,
    },
    {
        "id": "builtin:sdlc-fullscan",
        "name": "SDLC Full Scan (Clone)",
        "description": "Full repository scan — clones the repo and analyzes source code, tests, docs, API routes, plus GitHub API data. Resumable — restart from any failed step.",
        "explanation": "**What it does:** Everything from Quick Scan PLUS:\n- Source code → CodeModules with summaries\n- Tests → TestCases\n- Docs → ArchDecisions\n- API routes → APIContracts\n- Import analysis → DEPENDS_ON edges\n- Git history → high-churn ChangeRecords\n- Optional LLM enrichment for deep code understanding\n\n**Resumable:** 5 sub-stages run independently. If stage 3 fails, restart from stage 3 — stages 1-2 are preserved.\n\n**When to use:** When agents need to read/write code, run tests, or do gap analysis between spec and implementation.\n\n**Result:** All 5 SDLC layers populated. Full edge inference. Coverage scoring.",
        "version": 2,
        "category": "ingestion",
        "schema_name": "",
        "parser": "",
        "filters": {},
        "graph_config": {},
        "stages": [
            {"name": "scan_files", "type": "SDLC_SCAN_FILES", "config": {}},
            {"name": "scan_github", "type": "SDLC_SCAN_GITHUB", "config": {}},
            {"name": "scan_git", "type": "SDLC_SCAN_GIT", "config": {}},
            {"name": "infer_edges", "type": "SDLC_SCAN_EDGES", "config": {}},
            {"name": "context_units", "type": "SDLC_SCAN_CU", "config": {}},
        ],
        "default_params": {
            "scan_mode": "full",
            "max_cus": 8,
            "skip_llm": True,
            "file_extensions": ".py,.ts,.js,.go,.rs,.java,.tsx,.jsx",
        },
        "aiql_template": "",
        "governance": {"owner": "system", "classification": "public", "approved_by": "system"},
        "tags": ["sdlc", "repo", "full", "clone", "code", "scan"],
        "builtin": True,
    },
]


@dataclass
class AccuracyScore:
    """Extraction accuracy breakdown for a pipeline run."""
    overall: float = 0.0                    # 0.0 - 1.0
    entity_confidence_avg: float = 0.0      # avg LLM confidence on entities
    relationship_coverage: float = 0.0      # % chunks with ≥1 relationship
    chunk_quality: float = 0.0              # avg chunk length / target ratio
    embedding_coverage: float = 0.0         # % nodes with embeddings
    schema_compliance: float = 0.0          # % entities matching schema types
    entity_count: int = 0
    relationship_count: int = 0
    chunk_count: int = 0
    embedded_count: int = 0

    def compute_overall(self):
        """Weighted average of sub-scores."""
        weights = {
            "entity_confidence_avg": 0.3,
            "relationship_coverage": 0.2,
            "chunk_quality": 0.2,
            "embedding_coverage": 0.15,
            "schema_compliance": 0.15,
        }
        total_w = 0.0
        total_s = 0.0
        for key, w in weights.items():
            val = getattr(self, key, 0.0)
            if val > 0:
                total_s += val * w
                total_w += w
        self.overall = round(total_s / total_w, 4) if total_w > 0 else 0.0
        return self.overall


@dataclass
class StageLog:
    """Per-stage execution record."""
    name: str
    stage_type: str
    status: str = "pending"          # pending | running | completed | failed | skipped
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    duration_ms: int = 0
    items_processed: int = 0
    items_created: int = 0
    error: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class PipelineRun:
    """Execution record for a pipeline run."""
    run_id: str
    pipeline_id: str
    pipeline_name: str
    tenant_id: str
    user_id: str
    graph: str
    source: str
    source_type: str                        # text | file | url
    params_used: Dict[str, Any] = field(default_factory=dict)
    source_hash: str = ""                   # SHA-256 of source content (for dedup)
    status: str = "pending"                 # pending | running | completed | failed
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    duration_ms: int = 0
    stages: List[StageLog] = field(default_factory=list)
    nodes_created: int = 0
    edges_created: int = 0
    entities_extracted: int = 0
    accuracy: Optional[AccuracyScore] = None
    governance_tags: List[str] = field(default_factory=list)
    data_classification: str = "internal"   # public | internal | confidential | restricted
    error: Optional[str] = None


# ---------------------------------------------------------------------------
# Pipeline Store (SQLite-backed)
# ---------------------------------------------------------------------------

class PipelineStore:
    """
    Persistent store for pipeline definitions and run history.

    Pipelines are stored procedures: named, versioned AIQL templates
    with metadata, governance info, and execution tracking.
    """

    def __init__(self, db_path: Optional[str] = None):
        if db_path is None:
            db_path = str(Path(__file__).resolve().parents[1] / "contextcore_data" / "pipelines.db")
        self._db_path = db_path
        Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
        self._init_tables()
        self._seed_builtins()

    def _get_conn(self):
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _init_tables(self):
        with self._get_conn() as conn:
            conn.executescript("""
                -- Pipeline definitions (stored procedures)
                CREATE TABLE IF NOT EXISTS pipelines (
                    id              TEXT PRIMARY KEY,
                    tenant_id       TEXT NOT NULL DEFAULT 'system',
                    name            TEXT NOT NULL,
                    description     TEXT DEFAULT '',
                    version         INTEGER DEFAULT 1,
                    category        TEXT DEFAULT 'ingestion',
                    stages          TEXT NOT NULL,          -- JSON array of stage defs
                    default_params  TEXT DEFAULT '{}',      -- JSON defaults
                    aiql_template   TEXT NOT NULL,          -- parameterized AIQL
                    governance      TEXT DEFAULT '{}',      -- JSON {owner, classification, approved_by, approved_at}
                    tags            TEXT DEFAULT '[]',      -- JSON array of strings
                    builtin         INTEGER DEFAULT 0,
                    enabled         INTEGER DEFAULT 1,
                    created_at      TEXT NOT NULL,
                    updated_at      TEXT NOT NULL,
                    created_by      TEXT DEFAULT 'system'
                );
                CREATE INDEX IF NOT EXISTS idx_pipelines_tenant ON pipelines(tenant_id);
                CREATE INDEX IF NOT EXISTS idx_pipelines_category ON pipelines(category);

                -- Pipeline run history
                CREATE TABLE IF NOT EXISTS pipeline_runs (
                    run_id              TEXT PRIMARY KEY,
                    pipeline_id         TEXT NOT NULL,
                    pipeline_name       TEXT NOT NULL,
                    tenant_id           TEXT NOT NULL,
                    user_id             TEXT NOT NULL,
                    graph               TEXT NOT NULL,
                    source              TEXT DEFAULT '',
                    source_type         TEXT DEFAULT 'text',
                    source_hash         TEXT DEFAULT '',
                    params_used         TEXT DEFAULT '{}',
                    status              TEXT DEFAULT 'pending',
                    started_at          TEXT,
                    completed_at        TEXT,
                    duration_ms         INTEGER DEFAULT 0,
                    stages_log          TEXT DEFAULT '[]',   -- JSON array of StageLog
                    nodes_created       INTEGER DEFAULT 0,
                    edges_created       INTEGER DEFAULT 0,
                    entities_extracted  INTEGER DEFAULT 0,
                    accuracy_score      REAL DEFAULT 0.0,
                    accuracy_breakdown  TEXT DEFAULT '{}',   -- JSON AccuracyScore
                    governance_tags     TEXT DEFAULT '[]',
                    data_classification TEXT DEFAULT 'internal',
                    error               TEXT,
                    FOREIGN KEY (pipeline_id) REFERENCES pipelines(id)
                );
                CREATE INDEX IF NOT EXISTS idx_runs_tenant ON pipeline_runs(tenant_id);
                CREATE INDEX IF NOT EXISTS idx_runs_pipeline ON pipeline_runs(pipeline_id);
                CREATE INDEX IF NOT EXISTS idx_runs_status ON pipeline_runs(status);
                CREATE INDEX IF NOT EXISTS idx_runs_graph ON pipeline_runs(graph);
            """)

        # Migration: add source_hash column if missing (existing DBs)
        with self._get_conn() as conn:
            cols = [r["name"] for r in conn.execute("PRAGMA table_info(pipeline_runs)").fetchall()]
            if "source_hash" not in cols:
                conn.execute("ALTER TABLE pipeline_runs ADD COLUMN source_hash TEXT DEFAULT ''")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_runs_source_hash ON pipeline_runs(source_hash, graph)")

        # Migration: add extended pipeline columns (schema_name, parser, filters, graph_config)
        with self._get_conn() as conn:
            for col, default in [("schema_name", "''"), ("parser", "''"), ("filters", "'{}'"), ("graph_config", "'{}'")]:
                try:
                    conn.execute(f"ALTER TABLE pipelines ADD COLUMN {col} TEXT DEFAULT {default}")
                except sqlite3.OperationalError:
                    pass

    def _seed_builtins(self):
        """Insert/update built-in pipeline templates."""
        now = datetime.now(timezone.utc).isoformat()
        with self._get_conn() as conn:
            for p in BUILTIN_PIPELINES:
                existing = conn.execute("SELECT id, version FROM pipelines WHERE id = ?", (p["id"],)).fetchone()
                if not existing:
                    conn.execute(
                        """INSERT INTO pipelines
                           (id, tenant_id, name, description, version, category,
                            stages, default_params, aiql_template, governance, tags,
                            builtin, enabled, created_at, updated_at, created_by)
                           VALUES (?, 'system', ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, 1, ?, ?, 'system')""",
                        (
                            p["id"], p["name"], p["description"], p["version"], p["category"],
                            json.dumps(p["stages"]), json.dumps(p.get("default_params", {})),
                            p.get("aiql_template", ""), json.dumps(p.get("governance", {})),
                            json.dumps(p.get("tags", [])), now, now,
                        ),
                    )
                elif existing["version"] < p["version"]:
                    conn.execute(
                        """UPDATE pipelines SET name=?, description=?, version=?, stages=?,
                           default_params=?, aiql_template=?, governance=?, tags=?, updated_at=?
                           WHERE id=?""",
                        (
                            p["name"], p["description"], p["version"],
                            json.dumps(p["stages"]), json.dumps(p.get("default_params", {})),
                            p.get("aiql_template", ""), json.dumps(p.get("governance", {})),
                            json.dumps(p.get("tags", [])), now, p["id"],
                        ),
                    )

    # ── Pipeline CRUD ──────────────────────────────────────────────────

    def list_pipelines(self, tenant_id: str = "system", include_builtins: bool = True) -> List[Dict]:
        """List available pipelines for a tenant (includes builtins)."""
        with self._get_conn() as conn:
            if include_builtins:
                rows = conn.execute(
                    """SELECT * FROM pipelines
                       WHERE (tenant_id = ? OR tenant_id = 'system') AND enabled = 1
                       ORDER BY builtin DESC, name ASC""",
                    (tenant_id,),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM pipelines WHERE tenant_id = ? AND enabled = 1 ORDER BY name",
                    (tenant_id,),
                ).fetchall()
        return [self._row_to_pipeline(r) for r in rows]

    def get_pipeline(self, pipeline_id: str) -> Optional[Dict]:
        """Get a single pipeline definition."""
        with self._get_conn() as conn:
            row = conn.execute("SELECT * FROM pipelines WHERE id = ?", (pipeline_id,)).fetchone()
        return self._row_to_pipeline(row) if row else None

    def create_pipeline(
        self,
        tenant_id: str,
        name: str,
        description: str,
        stages: List[Dict],
        aiql_template: str,
        default_params: Optional[Dict] = None,
        governance: Optional[Dict] = None,
        tags: Optional[List[str]] = None,
        category: str = "ingestion",
        created_by: str = "user",
        schema_name: str = "",
        parser: str = "",
        filters: Optional[Dict] = None,
        graph_config: Optional[Dict] = None,
    ) -> Dict:
        """Create a custom pipeline definition."""
        pipeline_id = f"custom:{secrets.token_hex(8)}"
        now = datetime.now(timezone.utc).isoformat()
        gov = governance or {"owner": created_by, "classification": "internal"}

        with self._get_conn() as conn:
            conn.execute(
                """INSERT INTO pipelines
                   (id, tenant_id, name, description, version, category,
                    stages, default_params, aiql_template, governance, tags,
                    builtin, enabled, created_at, updated_at, created_by,
                    schema_name, parser, filters, graph_config)
                   VALUES (?, ?, ?, ?, 1, ?, ?, ?, ?, ?, ?, 0, 1, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    pipeline_id, tenant_id, name, description, category,
                    json.dumps(stages), json.dumps(default_params or {}),
                    aiql_template, json.dumps(gov), json.dumps(tags or []),
                    now, now, created_by,
                    schema_name, parser,
                    json.dumps(filters or {}), json.dumps(graph_config or {}),
                ),
            )
        return self.get_pipeline(pipeline_id)

    def update_pipeline(self, pipeline_id: str, **updates) -> Optional[Dict]:
        """Update a custom pipeline (cannot update builtins)."""
        pipeline = self.get_pipeline(pipeline_id)
        if not pipeline or pipeline.get("builtin"):
            return None

        now = datetime.now(timezone.utc).isoformat()
        allowed = {"name", "description", "stages", "aiql_template", "default_params", "governance", "tags", "category", "enabled", "schema_name", "parser", "filters", "graph_config"}
        sets = ["updated_at = ?", "version = version + 1"]
        vals = [now]

        for key, val in updates.items():
            if key not in allowed:
                continue
            if key in ("stages", "default_params", "governance", "tags", "filters", "graph_config"):
                val = json.dumps(val)
            sets.append(f"{key} = ?")
            vals.append(val)

        vals.append(pipeline_id)
        with self._get_conn() as conn:
            conn.execute(f"UPDATE pipelines SET {', '.join(sets)} WHERE id = ?", vals)
        return self.get_pipeline(pipeline_id)

    def delete_pipeline(self, pipeline_id: str) -> bool:
        """Delete a custom pipeline (cannot delete builtins)."""
        with self._get_conn() as conn:
            result = conn.execute(
                "DELETE FROM pipelines WHERE id = ? AND builtin = 0", (pipeline_id,)
            )
            return result.rowcount > 0

    # ── Pipeline Runs ──────────────────────────────────────────────────

    def find_duplicate(self, graph: str, source_hash: str, tenant_id: str) -> Optional[Dict]:
        """Check if a file with the same content hash was already ingested into this graph."""
        if not source_hash:
            return None
        with self._get_conn() as conn:
            row = conn.execute(
                """SELECT run_id, source, status, started_at
                   FROM pipeline_runs
                   WHERE graph = ? AND source_hash = ? AND tenant_id = ?
                     AND status IN ('completed', 'running', 'pending')
                   ORDER BY started_at DESC LIMIT 1""",
                (graph, source_hash, tenant_id),
            ).fetchone()
        return dict(row) if row else None

    def create_run(
        self,
        pipeline_id: str,
        tenant_id: str,
        user_id: str,
        graph: str,
        source: str = "",
        source_type: str = "text",
        source_hash: str = "",
        params: Optional[Dict] = None,
        governance_tags: Optional[List[str]] = None,
        data_classification: str = "internal",
    ) -> PipelineRun:
        """Create a new pipeline run record."""
        pipeline = self.get_pipeline(pipeline_id)
        if not pipeline:
            raise ValueError(f"Pipeline '{pipeline_id}' not found")

        run_id = f"run_{secrets.token_hex(10)}"
        now = datetime.now(timezone.utc).isoformat()

        # Merge default params with runtime overrides
        merged_params = {**(pipeline.get("default_params") or {}), **(params or {})}

        # Initialize stage logs from pipeline definition
        stage_logs = []
        for stage_def in pipeline.get("stages", []):
            stage_logs.append(StageLog(
                name=stage_def["name"],
                stage_type=stage_def["type"],
            ))

        gov_tags = governance_tags or pipeline.get("governance", {}).get("tags", [])

        run = PipelineRun(
            run_id=run_id,
            pipeline_id=pipeline_id,
            pipeline_name=pipeline.get("name", ""),
            tenant_id=tenant_id,
            user_id=user_id,
            graph=graph,
            source=source,
            source_type=source_type,
            source_hash=source_hash,
            params_used=merged_params,
            status="pending",
            started_at=now,
            stages=[asdict(s) for s in stage_logs],
            governance_tags=gov_tags,
            data_classification=data_classification,
        )

        with self._get_conn() as conn:
            conn.execute(
                """INSERT INTO pipeline_runs
                   (run_id, pipeline_id, pipeline_name, tenant_id, user_id,
                    graph, source, source_type, source_hash, params_used, status, started_at,
                    stages_log, governance_tags, data_classification)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?, ?)""",
                (
                    run_id, pipeline_id, run.pipeline_name, tenant_id, user_id,
                    graph, source, source_type, source_hash, json.dumps(merged_params), now,
                    json.dumps([asdict(s) for s in stage_logs]),
                    json.dumps(gov_tags), data_classification,
                ),
            )
        return run

    def update_run_status(self, run_id: str, status: str, error: Optional[str] = None):
        """Update overall run status."""
        now = datetime.now(timezone.utc).isoformat()
        with self._get_conn() as conn:
            if status in ("completed", "failed"):
                # Calculate duration
                row = conn.execute("SELECT started_at FROM pipeline_runs WHERE run_id = ?", (run_id,)).fetchone()
                duration_ms = 0
                if row and row["started_at"]:
                    try:
                        started = datetime.fromisoformat(row["started_at"])
                        duration_ms = int((datetime.now(timezone.utc) - started).total_seconds() * 1000)
                    except Exception:
                        pass
                conn.execute(
                    """UPDATE pipeline_runs
                       SET status=?, completed_at=?, duration_ms=?, error=?
                       WHERE run_id=?""",
                    (status, now, duration_ms, error, run_id),
                )
            else:
                conn.execute(
                    "UPDATE pipeline_runs SET status=?, error=? WHERE run_id=?",
                    (status, error, run_id),
                )

    def update_stage(
        self,
        run_id: str,
        stage_name: str,
        status: str,
        items_processed: int = 0,
        items_created: int = 0,
        error: Optional[str] = None,
        metadata: Optional[Dict] = None,
    ):
        """Update a specific stage within a run."""
        now = datetime.now(timezone.utc).isoformat()
        with self._get_conn() as conn:
            row = conn.execute("SELECT stages_log FROM pipeline_runs WHERE run_id = ?", (run_id,)).fetchone()
            if not row:
                return
            stages = json.loads(row["stages_log"])
            for stage in stages:
                if stage["name"] == stage_name:
                    stage["status"] = status
                    if status == "running" and not stage.get("started_at"):
                        stage["started_at"] = now
                    if status in ("completed", "failed", "skipped"):
                        stage["completed_at"] = now
                        if stage.get("started_at"):
                            try:
                                s = datetime.fromisoformat(stage["started_at"])
                                stage["duration_ms"] = int((datetime.now(timezone.utc) - s).total_seconds() * 1000)
                            except Exception:
                                pass
                    stage["items_processed"] = items_processed or stage.get("items_processed", 0)
                    stage["items_created"] = items_created or stage.get("items_created", 0)
                    if error:
                        stage["error"] = error
                    if metadata:
                        stage["metadata"] = {**stage.get("metadata", {}), **metadata}
                    break

            conn.execute(
                "UPDATE pipeline_runs SET stages_log = ? WHERE run_id = ?",
                (json.dumps(stages), run_id),
            )

    def sync_stages_log(self, run_id: str, stage_results: list):
        """Overwrite stages_log with the full list from PipelineContext.stage_results."""
        with self._get_conn() as conn:
            conn.execute(
                "UPDATE pipeline_runs SET stages_log = ? WHERE run_id = ?",
                (json.dumps(stage_results, default=str), run_id),
            )

    def update_run_counts(self, run_id: str, nodes: int = 0, edges: int = 0, entities: int = 0):
        """Update aggregate counts for a run."""
        with self._get_conn() as conn:
            conn.execute(
                """UPDATE pipeline_runs
                   SET nodes_created = ?, edges_created = ?, entities_extracted = ?
                   WHERE run_id = ?""",
                (nodes, edges, entities, run_id),
            )

    def set_accuracy(self, run_id: str, accuracy: AccuracyScore):
        """Set extraction accuracy scores for a run."""
        accuracy.compute_overall()
        with self._get_conn() as conn:
            conn.execute(
                """UPDATE pipeline_runs
                   SET accuracy_score = ?, accuracy_breakdown = ?
                   WHERE run_id = ?""",
                (accuracy.overall, json.dumps(asdict(accuracy)), run_id),
            )

    def get_run(self, run_id: str) -> Optional[Dict]:
        """Get a single run record."""
        with self._get_conn() as conn:
            row = conn.execute("SELECT * FROM pipeline_runs WHERE run_id = ?", (run_id,)).fetchone()
        return self._row_to_run(row) if row else None

    def list_runs(
        self,
        tenant_id: str,
        pipeline_id: Optional[str] = None,
        graph: Optional[str] = None,
        status: Optional[str] = None,
        limit: int = 50,
    ) -> List[Dict]:
        """List pipeline runs with optional filters."""
        query = "SELECT * FROM pipeline_runs WHERE tenant_id = ?"
        params = [tenant_id]

        if pipeline_id:
            query += " AND pipeline_id = ?"
            params.append(pipeline_id)
        if graph:
            query += " AND graph = ?"
            params.append(graph)
        if status:
            query += " AND status = ?"
            params.append(status)

        query += " ORDER BY started_at DESC LIMIT ?"
        params.append(limit)

        with self._get_conn() as conn:
            rows = conn.execute(query, params).fetchall()
        return [self._row_to_run(r) for r in rows]

    def get_pipeline_stats(self, pipeline_id: str, tenant_id: str) -> Dict:
        """Get aggregate stats for a pipeline."""
        with self._get_conn() as conn:
            row = conn.execute(
                """SELECT
                     COUNT(*) as total_runs,
                     SUM(CASE WHEN status='completed' THEN 1 ELSE 0 END) as successful,
                     SUM(CASE WHEN status='failed' THEN 1 ELSE 0 END) as failed,
                     AVG(CASE WHEN status='completed' THEN duration_ms END) as avg_duration_ms,
                     AVG(CASE WHEN status='completed' THEN accuracy_score END) as avg_accuracy,
                     SUM(nodes_created) as total_nodes,
                     SUM(edges_created) as total_edges,
                     SUM(entities_extracted) as total_entities,
                     MAX(started_at) as last_run_at
                   FROM pipeline_runs
                   WHERE pipeline_id = ? AND tenant_id = ?""",
                (pipeline_id, tenant_id),
            ).fetchone()
        return dict(row) if row else {}

    # ── Helpers ────────────────────────────────────────────────────────

    def _row_to_pipeline(self, row) -> Dict:
        d = dict(row)
        for key in ("stages", "default_params", "governance", "tags", "filters", "graph_config"):
            if key in d and isinstance(d[key], str):
                d[key] = json.loads(d[key])
        d["builtin"] = bool(d.get("builtin", 0))
        d["enabled"] = bool(d.get("enabled", 1))
        # Ensure extended fields have defaults even if missing from older rows
        d.setdefault("schema_name", "")
        d.setdefault("parser", "")
        d.setdefault("filters", {})
        d.setdefault("graph_config", {})
        return d

    def _row_to_run(self, row) -> Dict:
        d = dict(row)
        for key in ("params_used", "stages_log", "accuracy_breakdown", "governance_tags"):
            if key in d and isinstance(d[key], str):
                try:
                    d[key] = json.loads(d[key])
                except Exception:
                    pass
        # Rename stages_log → stages for API consistency
        if "stages_log" in d:
            d["stages"] = d.pop("stages_log")
        return d

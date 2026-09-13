# Data Storage Formats in AIContextDB

## Overview

AIContextDB uses multiple storage formats optimized for different data types and use cases.

## Storage Architecture

```
contextsynapse_data/
├── namespaces/
│   └── {namespace}/
│       ├── graph.h5 (or graph.json)          # Main graph storage
│       ├── collections/
│       │   └── {collection}/
│       │       ├── nodes/
│       │       │   └── {NodeType}/
│       │       │       └── {node_id}.json     # Node data
│       │       ├── edges/
│       │       │   └── {EdgeType}/
│       │       │       └── {edge_id}.json     # Edge data
│       │       ├── parquet/
│       │       │   └── {table_id}.parquet     # Table data
│       │       ├── vector/                    # Vector embeddings
│       │       └── hdf5/                      # Binary storage
│       └── pipelines/
│           └── {pipeline}/
│               ├── config.json               # Pipeline config
│               └── checkpoints/
│                   └── {checkpoint}.json      # Checkpoint data
```

## Storage Formats by Data Type

### 1. Graph Structure (Main Storage)

#### Format: HDF5 (Default) or JSON
**Location**: `contextsynapse_data/namespaces/{namespace}/graph.h5` or `graph.json`

**HDF5 Format** (Single File - Recommended):
- **File**: `graph.h5`
- **Format**: Hierarchical Data Format 5 (HDF5)
- **Contents**:
  - CSR (Compressed Sparse Row) graph structure
  - Node properties
  - Edge properties
  - Indexes
- **Advantages**:
  - Single file (easy backup/transfer)
  - Fast binary I/O
  - Efficient for large graphs
  - Supports compression
- **When Used**: `storage_strategy='single_file'` (default)

**JSON Format** (Legacy):
- **File**: `graph.json`
- **Format**: JSON
- **Contents**: Full graph structure in JSON
- **Advantages**: Human-readable, easy to inspect
- **Disadvantages**: Slower, larger file size
- **When Used**: `storage_strategy='json'` or legacy mode

**Code Location**: `contextsynapse/core/hybrid_graph_storage.py::save()`

---

### 2. Node Storage

#### Format: JSON Files
**Location**: `contextsynapse_data/namespaces/{namespace}/collections/{collection}/nodes/{NodeType}/{node_id}.json`

**Structure**:
```json
{
  "id": "doc_123",
  "label": "Document",
  "properties": {
    "file_path": "input-doc/report.pdf",
    "file_name": "report.pdf",
    "content": "...",  // Full text (or pointer in AI mode)
    "page_count": 15,
    "namespace": "my_docs",
    "extraction_method": "llm"
  },
  "created_at": "2025-01-10T12:00:00",
  "namespace": "my_docs",
  "collection": "raw_documents"
}
```

**AI Mode** (Lightweight):
- Stores pointers instead of large content:
```json
{
  "id": "doc_123",
  "label": "Document",
  "properties": {
    "_storage": {
      "content_pointer": "document_store://namespace/collection/doc_123"
    },
    "_storage_mode": "ai",
    "file_name": "report.pdf"
  }
}
```

**Code Location**: `contextsynapse/aiql/engine/executor.py::_store_node_in_collection()`

---

### 3. Edge Storage

#### Format: JSON Files
**Location**: `contextsynapse_data/namespaces/{namespace}/collections/{collection}/edges/{EdgeType}/{edge_id}.json`

**Structure**:
```json
{
  "id": "edge_123",
  "source": "entity_1",
  "target": "entity_2",
  "label": "Relationship",
  "properties": {
    "type": "WORKS_FOR",
    "confidence": 0.95,
    "context": "..."
  },
  "created_at": "2025-01-10T12:00:00",
  "namespace": "my_docs",
  "collection": "processed_documents"
}
```

**Code Location**: `contextsynapse/aiql/engine/executor.py::_store_edge_in_collection()`

---

### 4. Table Storage

#### Format: Parquet Files
**Location**: `contextsynapse_data/namespaces/{namespace}/collections/{collection}/parquet/{table_id}.parquet`

**Format**: Apache Parquet (columnar storage)
**Contents**: Table data (headers, rows) in columnar format
**Advantages**:
- Efficient for tabular data
- Columnar compression
- Fast queries on columns
- Supports large tables

**Code Location**: `contextsynapse/aiql/engine/executor.py::_store_table_in_parquet()`

---

### 5. Document Storage (Large Content)

#### Format: Document Store (TinyDB, SQLite, or CouchDB)
**Location**: `contextsynapse_data/namespaces/{namespace}/collections/{collection}/document_store/`

**Backends**:
- **TinyDB** (Default): JSON-based document database
- **SQLite**: SQL database
- **CouchDB**: NoSQL database (remote)

**Structure**:
- Documents stored separately from graph
- Graph nodes contain pointers: `document_store://namespace/collection/doc_id`
- Enables efficient storage of large text content

**When Used**: 
- `storage_mode='ai'` (AI mode)
- Document nodes with large content
- Configurable via `document_store_config`

**Code Location**: `contextsynapse/storage/document_store.py`

---

### 6. Vector Embeddings

#### Format: Vector Database (FAISS, Chroma, Qdrant, or Custom)
**Location**: `contextsynapse_data/namespaces/{namespace}/collections/{collection}/vector/`

**Backends**:
- **FAISS**: Facebook AI Similarity Search (local)
- **Chroma**: Vector database
- **Qdrant**: Vector database (local or remote)
- **Custom**: In-memory with HDF5 backup

**Storage**:
- Embeddings stored in vector DB
- Graph nodes contain pointers: `vector_db://namespace/collection/chunk_id`
- Enables fast similarity search

**Code Location**: `contextsynapse/vector/vector_db_manager.py`

---

### 7. Pipeline Configuration

#### Format: JSON
**Location**: `contextsynapse_data/namespaces/{namespace}/pipelines/{pipeline_name}/config.json`

**Structure**:
```json
{
  "pipeline_name": "document_ingestion",
  "namespace": "my_docs",
  "source_collection": "raw_documents",
  "target_collection": "processed_documents",
  "description": "...",
  "stages": [
    {
      "type": "EXTRACT",
      "step_name": "extract",
      "from_file": "{file_path}",
      ...
    }
  ]
}
```

---

### 8. Checkpoints

#### Format: JSON
**Location**: `contextsynapse_data/namespaces/{namespace}/pipelines/{pipeline_name}/checkpoints/{checkpoint_name}_stage_data.json`

**Structure**:
```json
{
  "completed_stages": 3,
  "last_completed_step": "chunk",
  "stage_data": {
    "extract": {
      "status": "completed",
      "documents_created": 1
    },
    "chunk": {
      "status": "completed",
      "chunks_created": 25
    }
  },
  "timestamp": "2025-01-10T12:00:00"
}
```

---

## Storage Modes

### 1. Pure Graph Mode
- **Graph**: HDF5 or JSON
- **Nodes/Edges**: Full data in JSON files
- **Use Case**: Small to medium graphs, full data access

### 2. AI Mode (Recommended)
- **Graph**: HDF5 (lightweight structure)
- **Nodes/Edges**: Pointers + metadata in JSON
- **Large Content**: Document Store
- **Embeddings**: Vector DB
- **Tables**: Parquet
- **Use Case**: Large graphs, efficient storage, fast queries

### 3. Hybrid Mode
- **Graph**: HDF5
- **Nodes/Edges**: Mix of full data and pointers
- **Use Case**: Balanced approach

---

## Configuration

### Storage Strategy
Set in `config/core/database.yaml`:
```yaml
storage:
  strategy: "single_file"  # or "json"
  format: "hdf5"           # or "json"
```

### Storage Mode
Set per namespace:
```yaml
namespaces:
  my_docs:
    storage_mode: "ai"  # or "pure_graph", "hybrid"
```

### Document Store
```yaml
document_store:
  enabled: true
  backend: "tinydb"  # or "sqlite", "couchdb"
```

---

## File Size Comparison

| Format | Size | Speed | Use Case |
|--------|------|-------|----------|
| **HDF5** | Small | Fast | Main graph (default) |
| **JSON** | Large | Slow | Human-readable, debugging |
| **Parquet** | Small | Fast | Tables |
| **TinyDB** | Medium | Medium | Documents (AI mode) |
| **Vector DB** | Medium | Very Fast | Embeddings |

---

## Best Practices

1. **Use HDF5 for main graph** (default, single file)
2. **Use AI mode for large documents** (pointers + document store)
3. **Use Parquet for tables** (automatic for Table nodes)
4. **Use Vector DB for embeddings** (automatic for Chunk nodes)
5. **Keep JSON for small metadata** (node/edge properties)

---

## Migration

To change storage format:
1. Update `config/core/database.yaml`
2. Reload namespace: `USE NAMESPACE my_docs;`
3. Save graph: `SAVE NAMESPACE my_docs;` (converts format)

---

## Code References

- **Graph Storage**: `contextsynapse/core/hybrid_graph_storage.py`
- **Single File Storage**: `contextsynapse/storage/single_file_storage.py`
- **Document Store**: `contextsynapse/storage/document_store.py`
- **Vector DB**: `contextsynapse/vector/vector_db_manager.py`
- **Collection Storage**: `contextsynapse/aiql/engine/executor.py::_store_node_in_collection()`

































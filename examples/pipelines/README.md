# Document Ingestion Pipelines

This directory contains pre-configured pipelines for document ingestion.

## Available Pipelines

### 1. `document_ingestion_pipeline.aiql` (Complete)
**Full-featured pipeline with all stages**

Stages:
- ✅ Extract (text, tables, images)
- ✅ Chunk (semantic chunking with LLM)
- ✅ Embed (vector embeddings)
- ✅ Extract Entities (named entity recognition)
- ✅ Extract Relationships (knowledge graph building)
- ✅ Index (search indexes)

**Best for:** Complete knowledge graph creation with full entity and relationship extraction

**Usage:**
```aiql
CREATE NAMESPACE my_docs;
USE NAMESPACE my_docs;

-- Copy and paste the pipeline definition from document_ingestion_pipeline.aiql
CREATE PIPELINE document_ingestion ...;

-- Run with a document
RUN PIPELINE document_ingestion WITH file_path="input-doc/my_document.pdf";
```

---

### 2. `simple_ingestion_pipeline.aiql` (Minimal)
**Basic pipeline - extract text only**

Stages:
- ✅ Extract (text only)

**Best for:** Quick document storage without processing

**Usage:**
```aiql
CREATE PIPELINE simple_ingestion ...;
RUN PIPELINE simple_ingestion WITH file_path="input-doc/my_document.pdf";
```

---

### 3. `fast_ingestion_pipeline.aiql` (Balanced)
**Fast pipeline with chunking and embedding**

Stages:
- ✅ Extract (text, tables)
- ✅ Chunk (fixed-size chunks)
- ✅ Embed (vector embeddings)

**Best for:** Fast ingestion with search capability (no entity extraction)

**Usage:**
```aiql
CREATE PIPELINE fast_ingestion ...;
RUN PIPELINE fast_ingestion WITH file_path="input-doc/my_document.pdf";
```

---

## Pipeline Selection Guide

| Pipeline | Speed | Completeness | Use Case |
|----------|-------|--------------|----------|
| **simple_ingestion** | ⚡⚡⚡ Fast | ⭐ Basic | Quick document storage |
| **fast_ingestion** | ⚡⚡ Medium | ⭐⭐ Good | Search-enabled documents |
| **document_ingestion** | ⚡ Slower | ⭐⭐⭐ Complete | Full knowledge graph |

## How to Use

### Step 1: Create Namespace
```aiql
CREATE NAMESPACE my_documents;
```

### Step 2: Use Namespace
```aiql
USE NAMESPACE my_documents;
```

### Step 3: Create Pipeline
Copy the pipeline definition from one of the `.aiql` files and execute it:
```aiql
CREATE PIPELINE document_ingestion
IN NAMESPACE my_documents
SOURCE COLLECTION raw_documents
TARGET COLLECTION processed_documents
DESCRIPTION "Complete document ingestion pipeline"
STAGES = [
  STEP extract ...
  STEP chunk ...
  ...
];
```

### Step 4: Run Pipeline
```aiql
-- Run entire pipeline
RUN PIPELINE document_ingestion WITH file_path="input-doc/my_document.pdf";

-- Or run step-by-step
RUN PIPELINE document_ingestion FROM STEP extract;
RUN PIPELINE document_ingestion FROM STEP chunk;
-- etc.
```

### Step 5: Verify Results
```aiql
-- Check what was created
SELECT COUNT(*) FROM Document;
SELECT COUNT(*) FROM Chunk;
SELECT COUNT(*) FROM Entity;
SELECT COUNT(*) FROM Edge WHERE type='Relationship';

-- View sample data
SELECT * FROM Document LIMIT 3;
SELECT * FROM Chunk LIMIT 5;
SELECT * FROM Entity LIMIT 10;
```

## Customization

### Change Models
Edit the `USING MODEL` or `USING LLM` clauses:
```aiql
USING MODEL "gpt-3.5-turbo"  -- Faster, cheaper
USING MODEL "gpt-4"          -- More accurate
```

### Change Embedding Model
Edit the `EMBED` stage:
```aiql
PARAMETERS (
    model="text-embedding-3-small",  -- Faster, 1536 dims
    dimensions=1536
)
-- OR
PARAMETERS (
    model="text-embedding-3-large",  -- Better quality, 3072 dims
    dimensions=3072
)
```

### Adjust Chunking
For semantic chunking:
```aiql
CHUNK BY semantic
PARAMETERS (
    max_tokens=1000,
    overlap=200,
    strategy="semantic"
)
```

For fixed-size chunking:
```aiql
CHUNK BY fixed
PARAMETERS (
    chunk_size=500,
    overlap=50
)
```

## Troubleshooting

### Pipeline Not Found
- Ensure you're in the correct namespace: `USE NAMESPACE my_documents;`
- Check pipeline exists: `SHOW PIPELINES;`

### File Not Found
- Use relative path: `"input-doc/my_document.pdf"`
- Or absolute path: `"C:/path/to/my_document.pdf"`
- Ensure file exists before running

### LLM Errors
- Check OpenAI API key is set: `OPENAI_API_KEY` environment variable
- Verify model name is correct: `"gpt-4"`, `"gpt-3.5-turbo"`, etc.
- Check API quota/limits

### No Data Created
- Check pipeline execution logs
- Verify each stage completed: `SHOW PIPELINES;` shows execution_count
- Run step-by-step to identify failing stage

## Examples

### Ingest a PDF
```aiql
USE NAMESPACE my_docs;
RUN PIPELINE document_ingestion WITH file_path="input-doc/report.pdf";
```

### Ingest Multiple Documents
```aiql
USE NAMESPACE my_docs;
RUN PIPELINE document_ingestion WITH file_path="input-doc/doc1.pdf";
RUN PIPELINE document_ingestion WITH file_path="input-doc/doc2.pdf";
RUN PIPELINE document_ingestion WITH file_path="input-doc/doc3.pdf";
```

### Resume from Checkpoint
```aiql
USE NAMESPACE my_docs;
RUN PIPELINE document_ingestion FROM STEP chunk;  -- Resume from chunk stage
```

































-- SQLite Schema for Metadata Collection
-- Can be adapted for PostgreSQL

-- ============================================================================
-- METADATA TABLES
-- ============================================================================

-- General metadata storage for nodes and edges
CREATE TABLE IF NOT EXISTS metadata (
    id TEXT PRIMARY KEY,
    namespace TEXT NOT NULL,
    collection TEXT,
    node_type TEXT,
    entity_type TEXT NOT NULL CHECK(entity_type IN ('node', 'edge')),
    metadata JSON,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_metadata_namespace ON metadata(namespace);
CREATE INDEX IF NOT EXISTS idx_metadata_collection ON metadata(collection);
CREATE INDEX IF NOT EXISTS idx_metadata_node_type ON metadata(node_type);
CREATE INDEX IF NOT EXISTS idx_metadata_entity_type ON metadata(entity_type);

-- ============================================================================
-- STATISTICS TABLES
-- ============================================================================

-- Collection and namespace statistics
CREATE TABLE IF NOT EXISTS statistics (
    namespace TEXT NOT NULL,
    collection TEXT,
    node_count INTEGER DEFAULT 0,
    edge_count INTEGER DEFAULT 0,
    total_size_bytes INTEGER DEFAULT 0,
    last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (namespace, collection)
);

CREATE INDEX IF NOT EXISTS idx_statistics_namespace ON statistics(namespace);

-- Node type statistics per collection
CREATE TABLE IF NOT EXISTS node_type_stats (
    namespace TEXT NOT NULL,
    collection TEXT NOT NULL,
    node_type TEXT NOT NULL,
    count INTEGER DEFAULT 0,
    last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (namespace, collection, node_type)
);

-- Edge type statistics
CREATE TABLE IF NOT EXISTS edge_type_stats (
    namespace TEXT NOT NULL,
    collection TEXT,
    edge_type TEXT NOT NULL,
    count INTEGER DEFAULT 0,
    last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (namespace, collection, edge_type)
);

-- ============================================================================
-- INDEX METADATA
-- ============================================================================

-- Index metadata and status
CREATE TABLE IF NOT EXISTS indexes (
    index_name TEXT PRIMARY KEY,
    index_type TEXT NOT NULL CHECK(index_type IN ('vector', 'bm25', 'graph', 'fulltext', 'parquet')),
    namespace TEXT NOT NULL,
    collection TEXT,
    metadata JSON,
    status TEXT DEFAULT 'active' CHECK(status IN ('active', 'building', 'failed', 'disabled')),
    last_built TIMESTAMP,
    build_time_seconds REAL,
    index_size_bytes INTEGER
);

CREATE INDEX IF NOT EXISTS idx_indexes_namespace ON indexes(namespace);
CREATE INDEX IF NOT EXISTS idx_indexes_collection ON indexes(collection);
CREATE INDEX IF NOT EXISTS idx_indexes_type ON indexes(index_type);

-- ============================================================================
-- SCHEMA DEFINITIONS
-- ============================================================================

-- Graph schema definitions
CREATE TABLE IF NOT EXISTS schemas (
    schema_name TEXT PRIMARY KEY,
    namespace TEXT,
    version TEXT,
    definition JSON,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_schemas_namespace ON schemas(namespace);

-- ============================================================================
-- PIPELINE EXECUTION
-- ============================================================================

-- Pipeline execution history
CREATE TABLE IF NOT EXISTS pipeline_runs (
    run_id TEXT PRIMARY KEY,
    pipeline_name TEXT NOT NULL,
    namespace TEXT NOT NULL,
    status TEXT CHECK(status IN ('running', 'completed', 'failed', 'cancelled')),
    started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    completed_at TIMESTAMP,
    stages_completed INTEGER DEFAULT 0,
    total_stages INTEGER,
    metadata JSON,
    error_message TEXT
);

CREATE INDEX IF NOT EXISTS idx_pipeline_runs_namespace ON pipeline_runs(namespace);
CREATE INDEX IF NOT EXISTS idx_pipeline_runs_status ON pipeline_runs(status);
CREATE INDEX IF NOT EXISTS idx_pipeline_runs_started ON pipeline_runs(started_at);

-- Pipeline stage execution details
CREATE TABLE IF NOT EXISTS pipeline_stages (
    stage_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    stage_name TEXT NOT NULL,
    stage_type TEXT NOT NULL,
    status TEXT CHECK(status IN ('pending', 'running', 'completed', 'failed')),
    started_at TIMESTAMP,
    completed_at TIMESTAMP,
    execution_time_seconds REAL,
    metadata JSON,
    FOREIGN KEY (run_id) REFERENCES pipeline_runs(run_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_pipeline_stages_run_id ON pipeline_stages(run_id);

-- ============================================================================
-- QUERY PERFORMANCE
-- ============================================================================

-- Query performance metrics
CREATE TABLE IF NOT EXISTS query_metrics (
    query_id TEXT PRIMARY KEY,
    query_text TEXT NOT NULL,
    namespace TEXT NOT NULL,
    query_type TEXT,  -- 'select', 'match', 'rag', 'graph'
    execution_time_ms REAL,
    rows_returned INTEGER,
    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    metadata JSON
);

CREATE INDEX IF NOT EXISTS idx_query_metrics_namespace ON query_metrics(namespace);
CREATE INDEX IF NOT EXISTS idx_query_metrics_timestamp ON query_metrics(timestamp);
CREATE INDEX IF NOT EXISTS idx_query_metrics_type ON query_metrics(query_type);

-- ============================================================================
-- COLLECTION METADATA
-- ============================================================================

-- Collection metadata and configuration
CREATE TABLE IF NOT EXISTS collections (
    collection_name TEXT NOT NULL,
    namespace TEXT NOT NULL,
    collection_type TEXT CHECK(collection_type IN ('raw', 'structured', 'media', 'processed', 'graph', 'metadata')),
    description TEXT,
    schema JSON,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (namespace, collection_name)
);

CREATE INDEX IF NOT EXISTS idx_collections_namespace ON collections(namespace);

-- ============================================================================
-- UTILITY FUNCTIONS
-- ============================================================================

-- Trigger to update updated_at timestamp
CREATE TRIGGER IF NOT EXISTS update_metadata_timestamp 
AFTER UPDATE ON metadata
BEGIN
    UPDATE metadata SET updated_at = CURRENT_TIMESTAMP WHERE id = NEW.id;
END;

-- Trigger to update statistics when metadata changes
CREATE TRIGGER IF NOT EXISTS update_statistics_on_metadata
AFTER INSERT ON metadata
BEGIN
    INSERT OR REPLACE INTO statistics (namespace, collection, node_count, edge_count, last_updated)
    SELECT 
        namespace,
        collection,
        SUM(CASE WHEN entity_type = 'node' THEN 1 ELSE 0 END) as node_count,
        SUM(CASE WHEN entity_type = 'edge' THEN 1 ELSE 0 END) as edge_count,
        CURRENT_TIMESTAMP
    FROM metadata
    WHERE namespace = NEW.namespace AND (collection = NEW.collection OR NEW.collection IS NULL)
    GROUP BY namespace, collection;
END;

-- ============================================================================
-- VIEWS
-- ============================================================================

-- View for namespace summary
CREATE VIEW IF NOT EXISTS namespace_summary AS
SELECT 
    namespace,
    COUNT(DISTINCT collection) as collection_count,
    SUM(CASE WHEN entity_type = 'node' THEN 1 ELSE 0 END) as total_nodes,
    SUM(CASE WHEN entity_type = 'edge' THEN 1 ELSE 0 END) as total_edges,
    MAX(created_at) as last_activity
FROM metadata
GROUP BY namespace;

-- View for collection summary
CREATE VIEW IF NOT EXISTS collection_summary AS
SELECT 
    namespace,
    collection,
    COUNT(CASE WHEN entity_type = 'node' THEN 1 END) as node_count,
    COUNT(CASE WHEN entity_type = 'edge' THEN 1 END) as edge_count,
    COUNT(DISTINCT node_type) as node_types,
    MAX(created_at) as last_updated
FROM metadata
GROUP BY namespace, collection;


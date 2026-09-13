"""SQL-based metadata storage for namespaces."""

import sqlite3
import json
import logging
from pathlib import Path
from typing import Dict, Any, Optional, List
from datetime import datetime
# Optional PostgreSQL support - only import if needed
try:
    import psycopg2
    from psycopg2.extras import RealDictCursor
    PSYCOPG2_AVAILABLE = True
except ImportError:
    PSYCOPG2_AVAILABLE = False
    psycopg2 = None
    RealDictCursor = None

logger = logging.getLogger(__name__)


class MetadataDB:
    """SQL-based metadata database."""
    
    def __init__(self, config: Dict[str, Any]):
        """Initialize metadata database.
        
        Args:
            config: Database configuration with 'database' ('sqlite' or 'postgresql'),
                    'path' (for SQLite), or 'connection_string' (for PostgreSQL)
        """
        self.db_type = config.get('database', 'sqlite')
        self.conn = None
        self._init_connection(config)
        self._init_schema()
    
    def _init_connection(self, config: Dict[str, Any]):
        """Initialize database connection."""
        try:
            if self.db_type == 'sqlite':
                db_path = Path(config.get('path', 'metadata.db'))
                db_path.parent.mkdir(parents=True, exist_ok=True)
                self.conn = sqlite3.connect(str(db_path), check_same_thread=False)
                self.conn.row_factory = sqlite3.Row  # Enable column access by name
            elif self.db_type == 'postgresql':
                conn_str = config.get('connection_string')
                if not conn_str:
                    raise ValueError("PostgreSQL requires 'connection_string' in config")
                if not PSYCOPG2_AVAILABLE:
                    raise ImportError("psycopg2 is required for PostgreSQL connections. Install with: pip install psycopg2-binary")
                self.conn = psycopg2.connect(conn_str)
            else:
                raise ValueError(f"Unsupported database type: {self.db_type}")
            logger.info(f"[EMOJI] Connected to {self.db_type} metadata database")
        except Exception as e:
            logger.error(f"[EMOJI] Failed to connect to metadata database: {e}")
            raise
    
    def _init_schema(self):
        """Initialize database schema."""
        schema_file = Path(__file__).parent / "metadata_schema.sql"
        if schema_file.exists():
            try:
                with open(schema_file, 'r') as f:
                    schema_sql = f.read()
                
                # Execute schema (SQLite uses different syntax)
                if self.db_type == 'sqlite':
                    self.conn.executescript(schema_sql)
                else:
                    # PostgreSQL - split by semicolons
                    for statement in schema_sql.split(';'):
                        if statement.strip():
                            self.conn.cursor().execute(statement)
                    self.conn.commit()
                
                logger.info("[EMOJI] Metadata database schema initialized")
            except Exception as e:
                logger.warning(f"[EMOJI][EMOJI] Failed to initialize schema (may already exist): {e}")
                if self.db_type == 'sqlite':
                    self.conn.rollback()
                else:
                    self.conn.rollback()
        else:
            logger.warning(f"[EMOJI][EMOJI] Schema file not found: {schema_file}")
    
    def store_metadata(self, entity_id: str, namespace: str, collection: Optional[str],
                      node_type: Optional[str], entity_type: str, metadata: Dict[str, Any]):
        """Store metadata for a node or edge.
        
        Args:
            entity_id: Unique identifier for the entity
            namespace: Namespace name
            collection: Collection name (optional)
            node_type: Node type (e.g., 'Table', 'Image') - optional for edges
            entity_type: 'node' or 'edge'
            metadata: Metadata dictionary
        """
        try:
            metadata_json = json.dumps(metadata) if metadata else None
            
            if self.db_type == 'sqlite':
                query = """
                INSERT OR REPLACE INTO metadata 
                (id, namespace, collection, node_type, entity_type, metadata, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """
                self.conn.execute(query, (
                    entity_id, namespace, collection, node_type, entity_type,
                    metadata_json, datetime.now(), datetime.now()
                ))
            else:
                query = """
                INSERT INTO metadata 
                (id, namespace, collection, node_type, entity_type, metadata, created_at, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (id) DO UPDATE SET
                    metadata = EXCLUDED.metadata,
                    updated_at = EXCLUDED.updated_at
                """
                self.conn.cursor().execute(query, (
                    entity_id, namespace, collection, node_type, entity_type,
                    json.dumps(metadata) if metadata else None,
                    datetime.now(), datetime.now()
                ))
            
            self.conn.commit()
            logger.debug(f"[EMOJI] Stored metadata for {entity_id}")
        except Exception as e:
            logger.error(f"[EMOJI] Failed to store metadata for {entity_id}: {e}")
            self.conn.rollback()
            raise
    
    def get_statistics(self, namespace: str, collection: Optional[str] = None) -> Dict[str, Any]:
        """Get statistics for namespace or collection.
        
        Args:
            namespace: Namespace name
            collection: Collection name (optional)
        
        Returns:
            Dictionary with statistics
        """
        try:
            if collection:
                query = """
                SELECT * FROM statistics 
                WHERE namespace = ? AND collection = ?
                """
                params = (namespace, collection)
            else:
                query = """
                SELECT namespace, 
                       SUM(node_count) as total_nodes,
                       SUM(edge_count) as total_edges,
                       MAX(last_updated) as last_updated
                FROM statistics
                WHERE namespace = ?
                GROUP BY namespace
                """
                params = (namespace,)
            
            if self.db_type == 'sqlite':
                cursor = self.conn.execute(query, params)
                row = cursor.fetchone()
                if row:
                    return dict(row)
            else:
                cursor = self.conn.cursor(cursor_factory=RealDictCursor)
                cursor.execute(query, params)
                row = cursor.fetchone()
                if row:
                    return dict(row)
            
            return {}
        except Exception as e:
            logger.error(f"[EMOJI] Failed to get statistics: {e}")
            return {}
    
    def update_statistics(self, namespace: str, collection: str, 
                         node_count: int = 0, edge_count: int = 0):
        """Update statistics for a collection.
        
        Args:
            namespace: Namespace name
            collection: Collection name
            node_count: Number of nodes to add
            edge_count: Number of edges to add
        """
        try:
            if self.db_type == 'sqlite':
                query = """
                INSERT OR REPLACE INTO statistics 
                (namespace, collection, node_count, edge_count, last_updated)
                VALUES (?, ?, 
                    COALESCE((SELECT node_count FROM statistics WHERE namespace = ? AND collection = ?), 0) + ?,
                    COALESCE((SELECT edge_count FROM statistics WHERE namespace = ? AND collection = ?), 0) + ?,
                    ?)
                """
                self.conn.execute(query, (
                    namespace, collection, namespace, collection, node_count,
                    namespace, collection, edge_count, datetime.now()
                ))
            else:
                query = """
                INSERT INTO statistics (namespace, collection, node_count, edge_count, last_updated)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (namespace, collection) DO UPDATE SET
                    node_count = statistics.node_count + EXCLUDED.node_count,
                    edge_count = statistics.edge_count + EXCLUDED.edge_count,
                    last_updated = EXCLUDED.last_updated
                """
                self.conn.cursor().execute(query, (
                    namespace, collection, node_count, edge_count, datetime.now()
                ))
            
            self.conn.commit()
        except Exception as e:
            logger.error(f"[EMOJI] Failed to update statistics: {e}")
            self.conn.rollback()
    
    def record_query(self, query_id: str, query_text: str, namespace: str,
                     query_type: Optional[str], execution_time_ms: float,
                     rows_returned: int, metadata: Optional[Dict] = None):
        """Record query performance metrics.
        
        Args:
            query_id: Unique query identifier
            query_text: Query text
            namespace: Namespace name
            query_type: Type of query ('select', 'match', 'rag', etc.)
            execution_time_ms: Execution time in milliseconds
            rows_returned: Number of rows returned
            metadata: Additional metadata
        """
        try:
            metadata_json = json.dumps(metadata) if metadata else None
            
            if self.db_type == 'sqlite':
                query = """
                INSERT INTO query_metrics 
                (query_id, query_text, namespace, query_type, execution_time_ms, rows_returned, metadata)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """
                self.conn.execute(query, (
                    query_id, query_text, namespace, query_type,
                    execution_time_ms, rows_returned, metadata_json
                ))
            else:
                query = """
                INSERT INTO query_metrics 
                (query_id, query_text, namespace, query_type, execution_time_ms, rows_returned, metadata)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                """
                self.conn.cursor().execute(query, (
                    query_id, query_text, namespace, query_type,
                    execution_time_ms, rows_returned, json.dumps(metadata) if metadata else None
                ))
            
            self.conn.commit()
        except Exception as e:
            logger.warning(f"[EMOJI][EMOJI] Failed to record query metrics: {e}")
            self.conn.rollback()
    
    def close(self):
        """Close database connection."""
        if self.conn:
            self.conn.close()
            logger.info("[EMOJI] Metadata database connection closed")




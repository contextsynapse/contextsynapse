"""
Metadata Tracker - Tracks queries, pipelines, and sessions with complete context

This module provides a comprehensive metadata tracking system for:
- All queries with full session context
- Pipeline executions with stage-level details
- Evaluation results linked to runs
- Session-level aggregations
"""

import json
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Dict, List, Any, Optional
import logging

logger = logging.getLogger(__name__)


class MetadataTracker:
    """Comprehensive metadata tracking for queries, pipelines, and sessions."""
    
    def __init__(self, base_dir: str = "metadata_store"):
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.config = self._load_config()
        
        # Initialize directories
        self._init_directories()
    
    def _load_config(self) -> Dict:
        """Load metadata schema configuration."""
        config_file = Path("config/metadata_schema.yaml")
        if config_file.exists():
            try:
                import yaml
                with open(config_file) as f:
                    return yaml.safe_load(f)
            except:
                pass
        return {}
    
    def _init_directories(self):
        """Create subdirectories."""
        # Create subdirectories
        self.queries_dir = self.base_dir / "queries"
        self.pipelines_dir = self.base_dir / "pipelines"
        self.sessions_dir = self.base_dir / "sessions"
        self.evaluations_dir = self.base_dir / "evaluations"
        
        for dir_path in [self.queries_dir, self.pipelines_dir, 
                        self.sessions_dir, self.evaluations_dir]:
            dir_path.mkdir(parents=True, exist_ok=True)
        
        # Initialize SQLite index
        self.index_db = self.base_dir / "metadata_index.db"
        self._init_database()
    
    def _create_subdirs(self):
        """Create subdirectories if needed."""
        self._init_directories()
    
    def _init_database(self):
        """Initialize SQLite database for fast queries."""
        conn = sqlite3.connect(self.index_db)
        cursor = conn.cursor()
        
        # Query index
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS query_index (
                query_id TEXT PRIMARY KEY,
                session_id TEXT,
                timestamp REAL,
                query_type TEXT,
                namespace TEXT,
                success INTEGER,
                execution_time_ms REAL,
                file_path TEXT,
                query_text TEXT
            )
        """)
        
        # Pipeline index
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS pipeline_index (
                pipeline_id TEXT,
                run_id TEXT,
                started_at REAL,
                status TEXT,
                total_duration REAL,
                file_path TEXT,
                PRIMARY KEY (pipeline_id, run_id)
            )
        """)
        
        # Session storage table (stores full session data, not just index)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS sessions (
                session_id TEXT PRIMARY KEY,
                user_id TEXT,
                namespace TEXT,
                created_at REAL,
                last_accessed REAL,
                query_count INTEGER,
                error_count INTEGER,
                data TEXT,  -- JSONB-like storage (TEXT with JSON)
                timeout REAL,
                status TEXT,
                active_namespace TEXT,
                active_collection TEXT
            )
        """)
        
        # Session index (for fast lookups)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS session_index (
                session_id TEXT PRIMARY KEY,
                created_at REAL,
                last_accessed REAL,
                namespace TEXT,
                query_count INTEGER,
                error_count INTEGER
            )
        """)
        
        # Indexes for fast queries
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_sessions_namespace ON sessions(namespace)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_sessions_timestamp ON sessions(last_accessed)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_sessions_status ON sessions(status)")
        
        conn.commit()
        conn.close()
    
    def _sanitize_session(self, session_data: Dict) -> Dict:
        """Remove non-serializable objects from session."""
        sanitized = {}
        for key, value in session_data.items():
            if key in ['contextcore', 'loaded_graph']:
                # Skip graph instances
                continue
            elif isinstance(value, dict):
                sanitized[key] = self._sanitize_session(value)
            else:
                try:
                    json.dumps(value)  # Test if serializable
                    sanitized[key] = value
                except:
                    # Skip non-serializable items
                    sanitized[key] = str(type(value))
        return sanitized
    
    def track_query(self, session_data: Dict, query_data: Dict, execution_result: Dict) -> str:
        """Track a query execution with full session context."""
        query_id = str(uuid.uuid4())
        timestamp = time.time()
        
        # Sanitize session data to remove non-serializable objects
        sanitized_session = self._sanitize_session(session_data)
        
        # Build comprehensive query metadata
        query_metadata = {
            'query_id': query_id,
            'session_id': session_data.get('session_id'),
            'timestamp': timestamp,
            'query_text': query_data.get('query', ''),
            'query_type': query_data.get('query_type', 'UNKNOWN'),
            'success': execution_result.get('success', False),
            'error_message': execution_result.get('error'),
            
            # Full session context
            'session': {
                'active_namespace': session_data.get('active_namespace'),
                'active_collection': session_data.get('active_collection'),
                'query_count': session_data.get('query_count', 0),
                'error_count': session_data.get('error_count', 0),
                'created_at': session_data.get('created_at'),
                'last_accessed': session_data.get('last_accessed', timestamp)
            },
            
            # Performance
            'execution_time_ms': execution_result.get('performance', {}).get('query_time_ms', 0),
            'nodes_returned': len(execution_result.get('nodes', [])),
            'edges_returned': len(execution_result.get('edges', [])),
            'cache_hit': execution_result.get('cache_hit', False),
            
            # Session performance (cumulative)
            'session_performance': sanitized_session.get('performance', {}),
            
            # Session security
            'session_security': sanitized_session.get('security', {}),
            
            # Session monitoring
            'session_monitoring': sanitized_session.get('monitoring', {}),
            
            # Client info
            'client_info': sanitized_session.get('client_info', {}),
            
            'created_at': timestamp,
            'completed_at': time.time()
        }
        
        # Save to JSONL file (partitioned by date)
        today = time.strftime('%Y-%m-%d')
        query_file = self.queries_dir / f"queries_{today}.jsonl"
        
        with open(query_file, 'a') as f:
            f.write(json.dumps(query_metadata) + '\n')
        
        # Update SQLite index
        conn = sqlite3.connect(self.index_db)
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO query_index 
            (query_id, session_id, timestamp, query_type, namespace, success, execution_time_ms, file_path, query_text)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            query_id,
            session_data.get('session_id'),
            timestamp,
            query_data.get('query_type', 'UNKNOWN'),
            session_data.get('active_namespace'),
            1 if execution_result.get('success') else 0,
            execution_result.get('performance', {}).get('query_time_ms', 0) if isinstance(execution_result.get('performance'), dict) else 0,
            str(query_file),
            query_data.get('query', '')
        ))
        conn.commit()
        conn.close()
        
        return query_id
    
    def track_session_update(self, session_data: Dict):
        """Track session metadata updates - now using SQLite database instead of JSON files."""
        session_id = session_data.get('session_id')
        if not session_id:
            return
        
        # Sanitize session data to remove non-serializable objects
        sanitized_session = self._sanitize_session(session_data)
        
        # Save to SQLite database (better than JSON files)
        conn = sqlite3.connect(self.index_db)
        cursor = conn.cursor()
        
        # Store full session data in sessions table
        cursor.execute("""
            INSERT OR REPLACE INTO sessions 
            (session_id, user_id, namespace, created_at, last_accessed, query_count, error_count,
             data, timeout, status, active_namespace, active_collection)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            session_id,
            sanitized_session.get('user_id'),
            sanitized_session.get('active_namespace'),
            sanitized_session.get('created_at', time.time()),
            sanitized_session.get('last_accessed', time.time()),
            sanitized_session.get('query_count', 0),
            sanitized_session.get('error_count', 0),
            json.dumps(sanitized_session),  # Store full session as JSON string
            sanitized_session.get('timeout', 3600),
            sanitized_session.get('status', 'active'),
            sanitized_session.get('active_namespace'),
            sanitized_session.get('active_collection')
        ))
        
        # Update index for fast lookups
        cursor.execute("""
            INSERT OR REPLACE INTO session_index 
            (session_id, created_at, last_accessed, namespace, query_count, error_count)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (
            session_id,
            sanitized_session.get('created_at', time.time()),
            sanitized_session.get('last_accessed', time.time()),
            sanitized_session.get('active_namespace'),
            sanitized_session.get('query_count', 0),
            sanitized_session.get('error_count', 0)
        ))
        
        conn.commit()
        conn.close()
        
        logger.debug(f"Session {session_id} saved to database")
    
    def get_session(self, session_id: str) -> Optional[Dict]:
        """Get session data from database."""
        conn = sqlite3.connect(self.index_db)
        cursor = conn.cursor()
        cursor.execute("SELECT data FROM sessions WHERE session_id = ?", (session_id,))
        row = cursor.fetchone()
        conn.close()
        
        if row:
            return json.loads(row[0])
        return None
    
    def cleanup_old_sessions(self, days: int = 30):
        """Clean up sessions older than specified days."""
        cutoff_time = time.time() - (days * 24 * 60 * 60)
        conn = sqlite3.connect(self.index_db)
        cursor = conn.cursor()
        cursor.execute("DELETE FROM sessions WHERE last_accessed < ?", (cutoff_time,))
        cursor.execute("DELETE FROM session_index WHERE last_accessed < ?", (cutoff_time,))
        deleted = cursor.rowcount
        conn.commit()
        conn.close()
        logger.info(f"Cleaned up {deleted} old sessions (older than {days} days)")
        return deleted
    
    def track_pipeline_run(self, pipeline_id: str, run_id: str, pipeline_data: Dict):
        """Track pipeline execution."""
        pipeline_file = self.pipelines_dir / f"pipeline_{pipeline_id}_run_{run_id}.json"
        with open(pipeline_file, 'w') as f:
            json.dump(pipeline_data, f, indent=2)
        
        # Update index
        conn = sqlite3.connect(self.index_db)
        cursor = conn.cursor()
        cursor.execute("""
            INSERT OR REPLACE INTO pipeline_index 
            (pipeline_id, run_id, started_at, status, total_duration, file_path)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (
            pipeline_id,
            run_id,
            pipeline_data.get('started_at'),
            pipeline_data.get('status'),
            pipeline_data.get('total_duration', 0),
            str(pipeline_file)
        ))
        conn.commit()
        conn.close()
    
    def track_evaluation(self, evaluation_id: str, evaluation_data: Dict):
        """Track evaluation results."""
        eval_file = self.evaluations_dir / f"evaluation_{evaluation_id}.json"
        with open(eval_file, 'w') as f:
            json.dump(evaluation_data, f, indent=2)
    
    def get_session_history(self, session_id: str) -> List[Dict]:
        """Get all queries for a session."""
        conn = sqlite3.connect(self.index_db)
        cursor = conn.cursor()
        cursor.execute("""
            SELECT file_path FROM query_index 
            WHERE session_id = ?
            ORDER BY timestamp DESC
        """, (session_id,))
        
        results = []
        for row in cursor.fetchall():
            file_path = Path(row[0])
            if file_path.exists():
                with open(file_path, 'r') as f:
                    for line in f:
                        if line.strip():
                            query = json.loads(line)
                            if query.get('session_id') == session_id:
                                results.append(query)
        
        conn.close()
        return results
    
    def get_session_stats(self, session_id: str) -> Dict:
        """Get aggregated statistics for a session."""
        history = self.get_session_history(session_id)
        
        if not history:
            return {}
        
        return {
            'total_queries': len(history),
            'successful_queries': sum(1 for q in history if q.get('success')),
            'failed_queries': sum(1 for q in history if not q.get('success')),
            'avg_execution_time': sum(q.get('execution_time_ms', 0) for q in history) / len(history),
            'total_execution_time': sum(q.get('execution_time_ms', 0) for q in history),
            'query_types': {}
        }
    
    def get_namespace_analytics(self, namespace: str, days: int = 7) -> Dict:
        """Get analytics for a namespace."""
        conn = sqlite3.connect(self.index_db)
        cursor = conn.cursor()
        
        cutoff = time.time() - (days * 24 * 60 * 60)
        cursor.execute("""
            SELECT * FROM query_index 
            WHERE namespace = ? AND timestamp > ?
            ORDER BY timestamp DESC
        """, (namespace, cutoff))
        
        queries = []
        for row in cursor.fetchall():
            queries.append({
                'query_id': row[0],
                'timestamp': row[2],
                'query_type': row[3],
                'success': bool(row[5]),
                'execution_time_ms': row[6]
            })
        
        conn.close()
        
        return {
            'namespace': namespace,
            'total_queries': len(queries),
            'avg_execution_time': sum(q['execution_time_ms'] for q in queries) / max(len(queries), 1),
            'success_rate': sum(1 for q in queries if q['success']) / max(len(queries), 1),
            'most_common_types': {}
        }


# Global instance
_metadata_tracker = None

def get_metadata_tracker() -> MetadataTracker:
    """Get the global metadata tracker instance."""
    global _metadata_tracker
    if _metadata_tracker is None:
        _metadata_tracker = MetadataTracker()
    return _metadata_tracker


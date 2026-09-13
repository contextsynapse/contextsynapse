"""
Metadata Query Tool - Extract and view metadata with various queries
"""

import json
import sqlite3
from pathlib import Path
from typing import Dict, List, Any, Optional
from datetime import datetime
import yaml
import logging

logger = logging.getLogger(__name__)


class MetadataQueryTool:
    """Tool to query and extract metadata."""
    
    def __init__(self, base_dir: str = "metadata_store"):
        self.base_dir = Path(base_dir)
        self.config = self._load_config()
    
    def _load_config(self) -> Dict:
        """Load metadata schema configuration."""
        config_file = Path("config/metadata_schema.yaml")
        if config_file.exists():
            with open(config_file) as f:
                return yaml.safe_load(f)
        return {}
    
    # Query operations
    def get_session_history(self, session_id: str) -> List[Dict]:
        """Get all queries for a session."""
        conn = sqlite3.connect(self.base_dir / "metadata_index.db")
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT file_path, query_id, timestamp, query_text, success, execution_time_ms
            FROM query_index 
            WHERE session_id = ?
            ORDER BY timestamp ASC
        """, (session_id,))
        
        results = []
        for row in cursor.fetchall():
            file_path, query_id, timestamp, query_text, success, exec_time = row
            results.append({
                'query_id': query_id,
                'timestamp': timestamp,
                'query_text': query_text,
                'success': bool(success),
                'execution_time_ms': exec_time,
                'file_path': file_path
            })
        
        conn.close()
        return results
    
    def get_namespace_queries(self, namespace: str, days: int = 7) -> List[Dict]:
        """Get queries for a namespace within date range."""
        cutoff = datetime.now().timestamp() - (days * 24 * 60 * 60)
        
        conn = sqlite3.connect(self.base_dir / "metadata_index.db")
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT query_id, session_id, timestamp, query_text, success, execution_time_ms
            FROM query_index 
            WHERE namespace = ? AND timestamp > ?
            ORDER BY timestamp DESC
        """, (namespace, cutoff))
        
        results = []
        for row in cursor.fetchall():
            query_id, session_id, timestamp, query_text, success, exec_time = row
            results.append({
                'query_id': query_id,
                'session_id': session_id,
                'timestamp': timestamp,
                'query_text': query_text,
                'success': bool(success),
                'execution_time_ms': exec_time
            })
        
        conn.close()
        return results
    
    def get_session_stats(self, session_id: str) -> Dict:
        """Get aggregated statistics for a session."""
        history = self.get_session_history(session_id)
        
        if not history:
            return {}
        
        successful = [q for q in history if q.get('success')]
        failed = [q for q in history if not q.get('success')]
        exec_times = [q.get('execution_time_ms', 0) for q in history]
        
        return {
            'session_id': session_id,
            'total_queries': len(history),
            'successful_queries': len(successful),
            'failed_queries': len(failed),
            'success_rate': len(successful) / len(history) if history else 0,
            'avg_execution_time_ms': sum(exec_times) / len(exec_times) if exec_times else 0,
            'min_execution_time_ms': min(exec_times) if exec_times else 0,
            'max_execution_time_ms': max(exec_times) if exec_times else 0,
            'total_execution_time_ms': sum(exec_times)
        }
    
    def get_namespace_stats(self, namespace: str, days: int = 7) -> Dict:
        """Get namespace analytics."""
        queries = self.get_namespace_queries(namespace, days)
        
        if not queries:
            return {'namespace': namespace, 'total_queries': 0}
        
        successful = [q for q in queries if q.get('success')]
        exec_times = [q.get('execution_time_ms', 0) for q in queries]
        
        return {
            'namespace': namespace,
            'period_days': days,
            'total_queries': len(queries),
            'successful_queries': len(successful),
            'failed_queries': len(queries) - len(successful),
            'success_rate': len(successful) / len(queries),
            'avg_execution_time_ms': sum(exec_times) / len(exec_times) if exec_times else 0,
            'unique_sessions': len(set(q.get('session_id') for q in queries))
        }
    
    def get_slow_queries(self, threshold_ms: float = 1000, limit: int = 100) -> List[Dict]:
        """Get queries slower than threshold."""
        conn = sqlite3.connect(self.base_dir / "metadata_index.db")
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT query_id, session_id, timestamp, query_text, namespace, execution_time_ms
            FROM query_index 
            WHERE execution_time_ms > ?
            ORDER BY execution_time_ms DESC
            LIMIT ?
        """, (threshold_ms, limit))
        
        results = []
        for row in cursor.fetchall():
            results.append({
                'query_id': row[0],
                'session_id': row[1],
                'timestamp': row[2],
                'query_text': row[3],
                'namespace': row[4],
                'execution_time_ms': row[5]
            })
        
        conn.close()
        return results
    
    def get_failed_queries(self, limit: int = 100) -> List[Dict]:
        """Get failed queries."""
        conn = sqlite3.connect(self.base_dir / "metadata_index.db")
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT query_id, session_id, timestamp, query_text, namespace
            FROM query_index 
            WHERE success = 0
            ORDER BY timestamp DESC
            LIMIT ?
        """, (limit,))
        
        results = []
        for row in cursor.fetchall():
            results.append({
                'query_id': row[0],
                'session_id': row[1],
                'timestamp': row[2],
                'query_text': row[3],
                'namespace': row[4]
            })
        
        conn.close()
        return results
    
    def get_query_by_id(self, query_id: str) -> Optional[Dict]:
        """Get full details for a specific query."""
        conn = sqlite3.connect(self.base_dir / "metadata_index.db")
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT file_path FROM query_index WHERE query_id = ?
        """, (query_id,))
        
        row = cursor.fetchone()
        conn.close()
        
        if not row:
            return None
        
        file_path = Path(row[0])
        if not file_path.exists():
            return None
        
        # Read from JSONL file
        with open(file_path) as f:
            for line in f:
                if line.strip():
                    query = json.loads(line)
                    if query.get('query_id') == query_id:
                        return query
        
        return None
    
    # Pretty print methods
    def print_session_history(self, session_id: str):
        """Print formatted session history."""
        history = self.get_session_history(session_id)
        stats = self.get_session_stats(session_id)
        
        print(f"\n{'='*70}")
        print(f"Session History: {session_id}")
        print(f"{'='*70}")
        print(f"Total Queries: {stats.get('total_queries', 0)}")
        print(f"Success Rate: {stats.get('success_rate', 0)*100:.1f}%")
        print(f"Avg Time: {stats.get('avg_execution_time_ms', 0):.2f}ms")
        print(f"\n{'Query':<5} {'Time (ms)':<12} {'Status':<10} {'Query'}")
        print(f"{'-'*70}")
        
        for i, query in enumerate(history, 1):
            status = "[EMOJI] SUCCESS" if query.get('success') else "[EMOJI] FAILED"
            query_text = query.get('query_text', '')[:50]
            print(f"{i:<5} {query.get('execution_time_ms', 0):<12.2f} {status:<10} {query_text}")
    
    def print_namespace_stats(self, namespace: str, days: int = 7):
        """Print formatted namespace statistics."""
        stats = self.get_namespace_stats(namespace, days)
        
        print(f"\n{'='*70}")
        print(f"Namespace Statistics: {namespace} (Last {days} days)")
        print(f"{'='*70}")
        print(f"Total Queries: {stats.get('total_queries', 0)}")
        print(f"Successful: {stats.get('successful_queries', 0)}")
        print(f"Failed: {stats.get('failed_queries', 0)}")
        print(f"Success Rate: {stats.get('success_rate', 0)*100:.1f}%")
        print(f"Avg Time: {stats.get('avg_execution_time_ms', 0):.2f}ms")
        print(f"Unique Sessions: {stats.get('unique_sessions', 0)}")


# CLI tool
def main():
    """Command-line interface for metadata queries."""
    import sys
    tool = MetadataQueryTool()
    
    if len(sys.argv) < 2:
        print("Usage: metadata_query_tool.py <command> [args]")
        print("\nCommands:")
        print("  session <session_id>          - Get session history")
        print("  namespace <namespace>         - Get namespace stats")
        print("  slow [threshold_ms]           - Get slow queries")
        print("  failed                        - Get failed queries")
        return
    
    command = sys.argv[1]
    
    if command == 'session' and len(sys.argv) >= 3:
        tool.print_session_history(sys.argv[2])
    
    elif command == 'namespace' and len(sys.argv) >= 3:
        tool.print_namespace_stats(sys.argv[2])
    
    elif command == 'slow':
        threshold = float(sys.argv[2]) if len(sys.argv) >= 3 else 1000
        queries = tool.get_slow_queries(threshold_ms=threshold)
        print(f"\nFound {len(queries)} slow queries (> {threshold}ms)")
        for q in queries[:10]:
            print(f"  {q.get('execution_time_ms', 0):.2f}ms: {q.get('query_text', '')[:60]}")
    
    elif command == 'failed':
        queries = tool.get_failed_queries()
        print(f"\nFound {len(queries)} failed queries")
        for q in queries[:10]:
            print(f"  {q.get('query_text', '')[:60]}")
    
    else:
        print(f"Unknown command: {command}")

if __name__ == "__main__":
    main()












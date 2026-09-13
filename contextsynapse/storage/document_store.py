"""
Document Store - Pluggable Document Database Backend
Supports multiple document database backends with unified interface
"""

import logging
from typing import Dict, Any, Optional, List, Callable
from pathlib import Path
from datetime import datetime
from abc import ABC, abstractmethod

logger = logging.getLogger(__name__)


class DocumentStoreInterface(ABC):
    """Abstract interface for document storage backends."""
    
    @abstractmethod
    def insert_document(self, doc_id: str, content: Any, metadata: Dict[str, Any] = None) -> str:
        """Insert a document."""
        pass
    
    @abstractmethod
    def get_document(self, doc_id: str) -> Optional[Dict[str, Any]]:
        """Get a document by ID."""
        pass
    
    @abstractmethod
    def search_documents(self, query_func: Callable = None, **kwargs) -> List[Dict[str, Any]]:
        """Search documents."""
        pass
    
    @abstractmethod
    def update_document(self, doc_id: str, updates: Dict[str, Any]) -> bool:
        """Update a document."""
        pass
    
    @abstractmethod
    def delete_document(self, doc_id: str) -> bool:
        """Delete a document."""
        pass
    
    @abstractmethod
    def get_all_documents(self) -> List[Dict[str, Any]]:
        """Get all documents."""
        pass
    
    @abstractmethod
    def count_documents(self) -> int:
        """Count total documents."""
        pass


class TinyDBDocumentStore(DocumentStoreInterface):
    """TinyDB implementation - Fast, non-server, file-based document store."""
    
    def __init__(self, namespace: str, collection: str, config: Dict[str, Any] = None):
        """Initialize TinyDB document store.
        
        Args:
            namespace: Namespace name
            collection: Collection name
            config: Optional configuration (e.g., {'storage_path': 'custom/path'})
        """
        self.namespace = namespace
        self.collection = collection
        
        try:
            from tinydb import TinyDB, Query
            
            # Create storage path
            if config and 'storage_path' in config:
                storage_path = Path(config['storage_path'])
            else:
                storage_path = Path(f"contextcore_data/namespaces/{namespace}/collections/{collection}")
            
            storage_path.mkdir(parents=True, exist_ok=True)
            
            # Initialize TinyDB
            db_file = storage_path / "documents.json"
            self.db = TinyDB(str(db_file))
            self.Doc = Query()
            
            logger.info(f"[EMOJI] TinyDB document store initialized: {db_file}")
            
        except ImportError:
            logger.error("[EMOJI] TinyDB not installed. Install with: pip install tinydb")
            raise ImportError("TinyDB is required. Install with: pip install tinydb")
    
    def insert_document(self, doc_id: str, content: Any, metadata: Dict[str, Any] = None) -> str:
        """Insert a document."""
        doc = {
            'doc_id': doc_id,
            'content': content,
            'metadata': metadata or {},
            'created_at': datetime.now().isoformat(),
            'updated_at': datetime.now().isoformat()
        }
        doc_id_inserted = self.db.insert(doc)
        logger.debug(f"[EMOJI] Inserted document {doc_id} into TinyDB")
        return str(doc_id_inserted)
    
    def get_document(self, doc_id: str) -> Optional[Dict[str, Any]]:
        """Get a document by ID."""
        results = self.db.search(self.Doc.doc_id == doc_id)
        return results[0] if results else None
    
    def search_documents(self, query_func: Callable = None, **kwargs) -> List[Dict[str, Any]]:
        """Search documents.
        
        Args:
            query_func: Custom query function (e.g., lambda doc: doc['metadata']['type'] == 'pdf')
            **kwargs: Search criteria (e.g., metadata_type='pdf')
        """
        if query_func:
            return self.db.search(query_func)
        elif kwargs:
            # Build query from kwargs
            query = self.Doc
            for key, value in kwargs.items():
                if key.startswith('metadata_'):
                    # Search in metadata
                    metadata_key = key.replace('metadata_', '')
                    query = query.metadata[metadata_key] == value
                else:
                    query = query[key] == value
            return self.db.search(query)
        else:
            return self.db.all()
    
    def update_document(self, doc_id: str, updates: Dict[str, Any]) -> bool:
        """Update a document."""
        updates['updated_at'] = datetime.now().isoformat()
        result = self.db.update(updates, self.Doc.doc_id == doc_id)
        return len(result) > 0
    
    def delete_document(self, doc_id: str) -> bool:
        """Delete a document."""
        result = self.db.remove(self.Doc.doc_id == doc_id)
        return len(result) > 0
    
    def get_all_documents(self) -> List[Dict[str, Any]]:
        """Get all documents."""
        return self.db.all()
    
    def count_documents(self) -> int:
        """Count total documents."""
        return len(self.db)


class SQLiteDocumentStore(DocumentStoreInterface):
    """SQLite with JSON1 extension - Non-server, built-in document store."""
    
    def __init__(self, namespace: str, collection: str, config: Dict[str, Any] = None):
        """Initialize SQLite document store.
        
        Args:
            namespace: Namespace name
            collection: Collection name
            config: Optional configuration
        """
        import sqlite3
        import json
        
        self.namespace = namespace
        self.collection = collection
        
        # Create storage path
        if config and 'storage_path' in config:
            storage_path = Path(config['storage_path'])
        else:
            storage_path = Path(f"contextcore_data/namespaces/{namespace}/collections/{collection}")
        
        storage_path.mkdir(parents=True, exist_ok=True)
        
        # Initialize SQLite database
        db_file = storage_path / "documents.db"
        self.conn = sqlite3.connect(str(db_file), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        
        # Create table if not exists
        self.conn.execute('''
            CREATE TABLE IF NOT EXISTS documents (
                doc_id TEXT PRIMARY KEY,
                content TEXT,
                metadata TEXT,
                created_at TEXT,
                updated_at TEXT
            )
        ''')
        
        # Enable JSON1 extension
        self.conn.execute('PRAGMA foreign_keys = ON')
        self.conn.commit()
        
        logger.info(f"[EMOJI] SQLite document store initialized: {db_file}")
    
    def insert_document(self, doc_id: str, content: Any, metadata: Dict[str, Any] = None) -> str:
        """Insert a document."""
        import json
        
        self.conn.execute('''
            INSERT OR REPLACE INTO documents (doc_id, content, metadata, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?)
        ''', (
            doc_id,
            json.dumps(content) if isinstance(content, (dict, list)) else str(content),
            json.dumps(metadata or {}),
            datetime.now().isoformat(),
            datetime.now().isoformat()
        ))
        self.conn.commit()
        logger.debug(f"[EMOJI] Inserted document {doc_id} into SQLite")
        return doc_id
    
    def get_document(self, doc_id: str) -> Optional[Dict[str, Any]]:
        """Get a document by ID."""
        import json
        
        cursor = self.conn.execute('SELECT * FROM documents WHERE doc_id = ?', (doc_id,))
        row = cursor.fetchone()
        if row:
            return {
                'doc_id': row['doc_id'],
                'content': json.loads(row['content']) if row['content'] else None,
                'metadata': json.loads(row['metadata']) if row['metadata'] else {},
                'created_at': row['created_at'],
                'updated_at': row['updated_at']
            }
        return None
    
    def search_documents(self, query_func: Callable = None, **kwargs) -> List[Dict[str, Any]]:
        """Search documents."""
        import json
        
        if kwargs:
            # Build SQL query from kwargs
            conditions = []
            params = []
            for key, value in kwargs.items():
                if key.startswith('metadata_'):
                    # Use JSON1 extension for metadata search
                    metadata_key = key.replace('metadata_', '')
                    conditions.append(f"json_extract(metadata, '$.{metadata_key}') = ?")
                    params.append(value)
                else:
                    conditions.append(f"{key} = ?")
                    params.append(value)
            
            query = f"SELECT * FROM documents WHERE {' AND '.join(conditions)}"
            cursor = self.conn.execute(query, params)
        else:
            cursor = self.conn.execute('SELECT * FROM documents')
        
        results = []
        for row in cursor.fetchall():
            results.append({
                'doc_id': row['doc_id'],
                'content': json.loads(row['content']) if row['content'] else None,
                'metadata': json.loads(row['metadata']) if row['metadata'] else {},
                'created_at': row['created_at'],
                'updated_at': row['updated_at']
            })
        return results
    
    def update_document(self, doc_id: str, updates: Dict[str, Any]) -> bool:
        """Update a document."""
        import json
        
        # Get existing document
        doc = self.get_document(doc_id)
        if not doc:
            return False
        
        # Merge updates
        if 'content' in updates:
            doc['content'] = updates['content']
        if 'metadata' in updates:
            doc['metadata'].update(updates['metadata'])
        else:
            doc['metadata'].update(updates)
        
        # Update in database
        self.conn.execute('''
            UPDATE documents 
            SET content = ?, metadata = ?, updated_at = ?
            WHERE doc_id = ?
        ''', (
            json.dumps(doc['content']) if isinstance(doc['content'], (dict, list)) else str(doc['content']),
            json.dumps(doc['metadata']),
            datetime.now().isoformat(),
            doc_id
        ))
        self.conn.commit()
        return True
    
    def delete_document(self, doc_id: str) -> bool:
        """Delete a document."""
        cursor = self.conn.execute('DELETE FROM documents WHERE doc_id = ?', (doc_id,))
        self.conn.commit()
        return cursor.rowcount > 0
    
    def get_all_documents(self) -> List[Dict[str, Any]]:
        """Get all documents."""
        return self.search_documents()
    
    def count_documents(self) -> int:
        """Count total documents."""
        cursor = self.conn.execute('SELECT COUNT(*) as count FROM documents')
        return cursor.fetchone()['count']


class CouchDBDocumentStore(DocumentStoreInterface):
    """CouchDB implementation - Server-based document store (for future use)."""
    
    def __init__(self, namespace: str, collection: str, config: Dict[str, Any] = None):
        """Initialize CouchDB document store.
        
        Args:
            namespace: Namespace name
            collection: Collection name
            config: Configuration with 'url', 'username', 'password', 'database'
        """
        self.namespace = namespace
        self.collection = collection
        
        try:
            import couchdb
            
            # Get configuration
            url = config.get('url', 'http://localhost:5984') if config else 'http://localhost:5984'
            username = config.get('username') if config else None
            password = config.get('password') if config else None
            database_name = config.get('database', f"{namespace}_{collection}") if config else f"{namespace}_{collection}"
            
            # Connect to CouchDB
            if username and password:
                self.server = couchdb.Server(f"{url}")
                self.server.resource.credentials = (username, password)
            else:
                self.server = couchdb.Server(url)
            
            # Get or create database
            if database_name in self.server:
                self.db = self.server[database_name]
            else:
                self.db = self.server.create(database_name)
            
            logger.info(f"[EMOJI] CouchDB document store initialized: {database_name}")
            
        except ImportError:
            logger.error("[EMOJI] CouchDB not installed. Install with: pip install couchdb")
            raise ImportError("CouchDB is required. Install with: pip install couchdb")
        except Exception as e:
            logger.error(f"[EMOJI] Failed to connect to CouchDB: {e}")
            raise
    
    def insert_document(self, doc_id: str, content: Any, metadata: Dict[str, Any] = None) -> str:
        """Insert a document."""
        doc = {
            '_id': doc_id,
            'doc_id': doc_id,
            'content': content,
            'metadata': metadata or {},
            'created_at': datetime.now().isoformat(),
            'updated_at': datetime.now().isoformat()
        }
        doc_id_inserted, _ = self.db.save(doc)
        logger.debug(f"[EMOJI] Inserted document {doc_id} into CouchDB")
        return doc_id_inserted
    
    def get_document(self, doc_id: str) -> Optional[Dict[str, Any]]:
        """Get a document by ID."""
        try:
            return dict(self.db[doc_id])
        except:
            return None
    
    def search_documents(self, query_func: Callable = None, **kwargs) -> List[Dict[str, Any]]:
        """Search documents using CouchDB views or Mango queries."""
        # For now, return all documents (can be enhanced with views)
        results = []
        for doc_id in self.db:
            doc = dict(self.db[doc_id])
            if query_func and query_func(doc):
                results.append(doc)
            elif not query_func:
                results.append(doc)
        return results
    
    def update_document(self, doc_id: str, updates: Dict[str, Any]) -> bool:
        """Update a document."""
        try:
            doc = self.db[doc_id]
            doc.update(updates)
            doc['updated_at'] = datetime.now().isoformat()
            self.db.save(doc)
            return True
        except:
            return False
    
    def delete_document(self, doc_id: str) -> bool:
        """Delete a document."""
        try:
            doc = self.db[doc_id]
            self.db.delete(doc)
            return True
        except:
            return False
    
    def get_all_documents(self) -> List[Dict[str, Any]]:
        """Get all documents."""
        return [dict(self.db[doc_id]) for doc_id in self.db]
    
    def count_documents(self) -> int:
        """Count total documents."""
        return len(self.db)


class DocumentStore:
    """Unified document store with pluggable backends."""
    
    _backends = {
        'tinydb': TinyDBDocumentStore,
        'sqlite': SQLiteDocumentStore,
        'couchdb': CouchDBDocumentStore,
    }
    
    def __init__(self, namespace: str, collection: str, backend: str = 'tinydb', config: Dict[str, Any] = None):
        """Initialize document store with specified backend.
        
        Args:
            namespace: Namespace name
            collection: Collection name
            backend: Backend type ('tinydb', 'sqlite', 'couchdb')
            config: Optional backend-specific configuration
        """
        self.namespace = namespace
        self.collection = collection
        self.backend_name = backend.lower()
        
        if self.backend_name not in self._backends:
            logger.warning(f"[EMOJI][EMOJI] Unknown backend '{backend}', falling back to 'tinydb'")
            self.backend_name = 'tinydb'
        
        backend_class = self._backends[self.backend_name]
        self.backend = backend_class(namespace, collection, config or {})
        
        logger.info(f"[EMOJI] Document store initialized: {backend} for {namespace}/{collection}")
    
    def insert_document(self, doc_id: str, content: Any, metadata: Dict[str, Any] = None) -> str:
        """Insert a document."""
        return self.backend.insert_document(doc_id, content, metadata)
    
    def get_document(self, doc_id: str) -> Optional[Dict[str, Any]]:
        """Get a document by ID."""
        return self.backend.get_document(doc_id)
    
    def search_documents(self, query_func: Callable = None, **kwargs) -> List[Dict[str, Any]]:
        """Search documents."""
        return self.backend.search_documents(query_func, **kwargs)
    
    def update_document(self, doc_id: str, updates: Dict[str, Any]) -> bool:
        """Update a document."""
        return self.backend.update_document(doc_id, updates)
    
    def delete_document(self, doc_id: str) -> bool:
        """Delete a document."""
        return self.backend.delete_document(doc_id)
    
    def get_all_documents(self) -> List[Dict[str, Any]]:
        """Get all documents."""
        return self.backend.get_all_documents()
    
    def count_documents(self) -> int:
        """Count total documents."""
        return self.backend.count_documents()
    
    @classmethod
    def get_available_backends(cls) -> List[str]:
        """Get list of available backends."""
        return list(cls._backends.keys())
    
    @classmethod
    def register_backend(cls, name: str, backend_class: type):
        """Register a custom backend."""
        cls._backends[name.lower()] = backend_class
        logger.info(f"[EMOJI] Registered document store backend: {name}")




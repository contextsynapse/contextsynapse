"""
ID Generation Strategy

Provides consistent, retrievable IDs for documents and chunks with:
- Deterministic generation (same input = same ID)
- Hash-based deduplication
- Human-readable prefixes
- Namespace support
"""

import hashlib
import uuid
from pathlib import Path
from typing import Optional, Dict, Any
from datetime import datetime
import logging

logger = logging.getLogger(__name__)


class IDGenerator:
    """
    Generates IDs for documents and chunks with a consistent strategy.
    
    Strategy:
    - Document IDs: Based on file hash + metadata (deterministic)
    - Chunk IDs: Based on document ID + strategy + position (deterministic)
    - Supports namespace prefixes
    - Supports human-readable formats
    """
    
    # ID Prefixes
    DOCUMENT_PREFIX = "doc"
    CHUNK_PREFIX = "chunk"
    
    @staticmethod
    def generate_document_id(
        file_path: str,
        file_hash: Optional[str] = None,
        namespace: Optional[str] = None,
        use_hash: bool = True
    ) -> str:
        """
        Generate document ID from file path and hash.
        
        Strategy:
        1. If file_hash provided and use_hash=True: Use hash-based ID (deterministic)
        2. Otherwise: Use file path hash (deterministic)
        3. Add namespace prefix if provided
        
        Args:
            file_path: Path to source file
            file_hash: Pre-computed file hash (optional)
            namespace: Namespace prefix (optional)
            use_hash: Whether to use hash-based ID (default: True)
            
        Returns:
            Document ID (e.g., "doc_sha256_abc123..." or "doc_ns1_sha256_abc123...")
        """
        if use_hash:
            if file_hash:
                # Use provided hash
                hash_value = file_hash.split('-')[-1] if '-' in file_hash else file_hash
            else:
                # Compute hash from file path (for deterministic ID)
                hash_obj = hashlib.sha256()
                hash_obj.update(str(Path(file_path).absolute()).encode('utf-8'))
                hash_value = hash_obj.hexdigest()[:16]  # First 16 chars for readability
            
            # Generate ID: doc_{namespace_}{hash}
            if namespace:
                return f"{IDGenerator.DOCUMENT_PREFIX}_{namespace}_{hash_value}"
            else:
                return f"{IDGenerator.DOCUMENT_PREFIX}_{hash_value}"
        else:
            # Use UUID (non-deterministic, but unique)
            unique_id = uuid.uuid4().hex[:12]
            if namespace:
                return f"{IDGenerator.DOCUMENT_PREFIX}_{namespace}_{unique_id}"
            else:
                return f"{IDGenerator.DOCUMENT_PREFIX}_{unique_id}"
    
    @staticmethod
    def generate_chunk_id(
        document_id: str,
        page_no: int,
        chunk_index: int,
        chunking_strategy: str,
        strategy_version: str = "v1",
        namespace: Optional[str] = None
    ) -> str:
        """
        Generate chunk ID from document ID and chunk metadata.
        
        Strategy:
        - Format: chunk_{document_id}_p{page_no}_{strategy}_{version}_c{index}
        - Deterministic: Same document + page + strategy + index = same chunk ID
        - Includes strategy for multiple chunking strategies support
        
        Args:
            document_id: Document ID (without prefix)
            chunk_index: Chunk index within page
            chunking_strategy: Chunking strategy (semantic, fixed, paragraph)
            strategy_version: Strategy version (v1, v2, etc.)
            namespace: Namespace prefix (optional)
            
        Returns:
            Chunk ID (e.g., "chunk_doc_abc123_p1_semantic_v1_c0")
        """
        # Remove prefix if present
        if document_id.startswith(IDGenerator.DOCUMENT_PREFIX + "_"):
            doc_id_clean = document_id[len(IDGenerator.DOCUMENT_PREFIX) + 1:]
        else:
            doc_id_clean = document_id
        
        # Generate chunk ID
        chunk_id = f"{IDGenerator.CHUNK_PREFIX}_{doc_id_clean}_p{page_no}_{chunking_strategy}_{strategy_version}_c{chunk_index}"
        
        if namespace:
            chunk_id = f"{IDGenerator.CHUNK_PREFIX}_{namespace}_{doc_id_clean}_p{page_no}_{chunking_strategy}_{strategy_version}_c{chunk_index}"
        
        return chunk_id
    
    @staticmethod
    def parse_document_id(document_id: str) -> Dict[str, Any]:
        """
        Parse document ID to extract components.
        
        Args:
            document_id: Document ID to parse
            
        Returns:
            Dictionary with parsed components (namespace, hash, etc.)
        """
        parts = document_id.split('_')
        
        if len(parts) < 2:
            return {'type': 'unknown', 'raw': document_id}
        
        if parts[0] != IDGenerator.DOCUMENT_PREFIX:
            return {'type': 'unknown', 'raw': document_id}
        
        if len(parts) == 3:
            # Format: doc_{namespace}_{hash}
            return {
                'type': 'document',
                'namespace': parts[1],
                'hash': parts[2],
                'raw': document_id
            }
        elif len(parts) == 2:
            # Format: doc_{hash}
            return {
                'type': 'document',
                'namespace': None,
                'hash': parts[1],
                'raw': document_id
            }
        else:
            return {'type': 'unknown', 'raw': document_id}
    
    @staticmethod
    def parse_chunk_id(chunk_id: str) -> Dict[str, Any]:
        """
        Parse chunk ID to extract components.
        
        Args:
            chunk_id: Chunk ID to parse
            
        Returns:
            Dictionary with parsed components (document_id, page_no, strategy, etc.)
        """
        parts = chunk_id.split('_')
        
        if len(parts) < 6:
            return {'type': 'unknown', 'raw': chunk_id}
        
        if parts[0] != IDGenerator.CHUNK_PREFIX:
            return {'type': 'unknown', 'raw': chunk_id}
        
        # Format: chunk_{doc_id}_p{page_no}_{strategy}_{version}_c{index}
        # Or: chunk_{namespace}_{doc_id}_p{page_no}_{strategy}_{version}_c{index}
        
        try:
            # Find page number (starts with 'p')
            page_idx = None
            for i, part in enumerate(parts):
                if part.startswith('p') and part[1:].isdigit():
                    page_idx = i
                    page_no = int(part[1:])
                    break
            
            if page_idx is None:
                return {'type': 'unknown', 'raw': chunk_id}
            
            # Extract document ID (everything before page number)
            if page_idx == 2:
                # Has namespace: chunk_{namespace}_{doc_id}_p...
                namespace = parts[1]
                doc_id_parts = parts[2:page_idx]
            else:
                # No namespace: chunk_{doc_id}_p...
                namespace = None
                doc_id_parts = parts[1:page_idx]
            
            document_id = '_'.join(doc_id_parts)
            
            # Extract strategy and version
            if page_idx + 3 < len(parts):
                strategy = parts[page_idx + 1]
                version = parts[page_idx + 2]
                index_part = parts[page_idx + 3]
                chunk_index = int(index_part[1:]) if index_part.startswith('c') and index_part[1:].isdigit() else None
            else:
                strategy = None
                version = None
                chunk_index = None
            
            return {
                'type': 'chunk',
                'namespace': namespace,
                'document_id': document_id,
                'page_no': page_no,
                'strategy': strategy,
                'version': version,
                'chunk_index': chunk_index,
                'raw': chunk_id
            }
        except (ValueError, IndexError) as e:
            logger.warning(f"Failed to parse chunk ID {chunk_id}: {e}")
            return {'type': 'unknown', 'raw': chunk_id}


class DeduplicationManager:
    """
    Manages document deduplication using checksums/hashes.
    
    Prevents reprocessing the same document by:
    - Computing file hashes
    - Checking against existing documents
    - Tracking extraction history
    """
    
    def __init__(self, namespace: Optional[str] = None, base_dir: str = "contextcore_data"):
        """
        Initialize deduplication manager.
        
        Args:
            namespace: Namespace for deduplication scope
            base_dir: Base directory for storage
        """
        self.namespace = namespace
        self.base_dir = Path(base_dir)
        self.dedup_db_path = self.base_dir / "namespaces" / (namespace or "default") / "deduplication.db"
        self.dedup_db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()
    
    def _init_db(self):
        """Initialize deduplication database."""
        import sqlite3
        self.conn = sqlite3.connect(str(self.dedup_db_path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.cursor = self.conn.cursor()
        
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS document_hashes (
                file_hash TEXT PRIMARY KEY,
                document_id TEXT NOT NULL,
                file_path TEXT NOT NULL,
                namespace TEXT,
                first_seen_at TEXT NOT NULL,
                last_seen_at TEXT NOT NULL,
                extraction_count INTEGER DEFAULT 1,
                normalized_version TEXT,
                UNIQUE(file_hash, namespace)
            )
        ''')
        
        self.cursor.execute('CREATE INDEX IF NOT EXISTS idx_file_hash ON document_hashes(file_hash)')
        self.cursor.execute('CREATE INDEX IF NOT EXISTS idx_document_id ON document_hashes(document_id)')
        self.cursor.execute('CREATE INDEX IF NOT EXISTS idx_namespace ON document_hashes(namespace)')
        self.conn.commit()
    
    def compute_file_hash(self, file_path: str, algorithm: str = "sha256") -> str:
        """
        Compute hash of a file.
        
        Args:
            file_path: Path to file
            algorithm: Hash algorithm (sha256, md5, etc.)
            
        Returns:
            Hash string in format: "{algorithm}-{hex_digest}"
        """
        hash_obj = hashlib.new(algorithm)
        with open(file_path, 'rb') as f:
            for chunk in iter(lambda: f.read(4096), b""):
                hash_obj.update(chunk)
        
        hex_digest = hash_obj.hexdigest()
        return f"{algorithm}-{hex_digest}"
    
    def check_duplicate(
        self,
        file_path: str,
        file_hash: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        """
        Check if document already exists (deduplication).
        
        Args:
            file_path: Path to file
            file_hash: Pre-computed hash (optional)
            
        Returns:
            Existing document info if duplicate found, None otherwise
        """
        if not file_hash:
            file_hash = self.compute_file_hash(file_path)
        
        self.cursor.execute('''
            SELECT * FROM document_hashes
            WHERE file_hash = ? AND (namespace = ? OR namespace IS NULL)
            ORDER BY last_seen_at DESC
            LIMIT 1
        ''', (file_hash, self.namespace))
        
        row = self.cursor.fetchone()
        if row:
            return dict(row)
        return None
    
    def register_document(
        self,
        file_path: str,
        document_id: str,
        file_hash: Optional[str] = None,
        normalized_version: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Register a document (new or update existing).
        
        Args:
            file_path: Path to file
            document_id: Document ID
            file_hash: Pre-computed hash (optional)
            normalized_version: Normalized version (optional)
            
        Returns:
            Registration info
        """
        if not file_hash:
            file_hash = self.compute_file_hash(file_path)
        
        # Check if already exists
        existing = self.check_duplicate(file_path, file_hash)
        
        now = datetime.utcnow().isoformat() + "Z"
        
        if existing:
            # Update existing record
            extraction_count = existing.get('extraction_count', 1) + 1
            self.cursor.execute('''
                UPDATE document_hashes
                SET document_id = ?,
                    last_seen_at = ?,
                    extraction_count = ?,
                    normalized_version = COALESCE(?, normalized_version)
                WHERE file_hash = ? AND (namespace = ? OR namespace IS NULL)
            ''', (document_id, now, extraction_count, normalized_version, file_hash, self.namespace))
            self.conn.commit()
            
            return {
                'status': 'updated',
                'file_hash': file_hash,
                'document_id': document_id,
                'extraction_count': extraction_count,
                'first_seen_at': existing.get('first_seen_at'),
                'last_seen_at': now
            }
        else:
            # Insert new record
            self.cursor.execute('''
                INSERT INTO document_hashes
                (file_hash, document_id, file_path, namespace, first_seen_at, last_seen_at, extraction_count, normalized_version)
                VALUES (?, ?, ?, ?, ?, ?, 1, ?)
            ''', (file_hash, document_id, str(file_path), self.namespace, now, now, normalized_version))
            self.conn.commit()
            
            return {
                'status': 'new',
                'file_hash': file_hash,
                'document_id': document_id,
                'extraction_count': 1,
                'first_seen_at': now,
                'last_seen_at': now
            }
    
    def get_document_by_hash(self, file_hash: str) -> Optional[Dict[str, Any]]:
        """Get document info by hash."""
        self.cursor.execute('''
            SELECT * FROM document_hashes
            WHERE file_hash = ? AND (namespace = ? OR namespace IS NULL)
            ORDER BY last_seen_at DESC
            LIMIT 1
        ''', (file_hash, self.namespace))
        
        row = self.cursor.fetchone()
        return dict(row) if row else None
    
    def close(self):
        """Close database connection."""
        if hasattr(self, 'conn') and self.conn:
            self.conn.close()
    
    def __del__(self):
        """Ensure connection is closed."""
        self.close()

































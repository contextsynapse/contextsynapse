"""
ChromaDB Vector Store for AIContextDB
Cross-platform vector database using ChromaDB.
"""

import logging
from typing import List, Dict, Any, Optional
from pathlib import Path

logger = logging.getLogger(__name__)

try:
    import chromadb
    from chromadb.config import Settings
    CHROMA_AVAILABLE = True
except ImportError:
    CHROMA_AVAILABLE = False
    logger.warning("ChromaDB not available. Install with: pip install chromadb")

class ChromaVectorStore:
    """
    ChromaDB-based vector store.
    
    Features:
    - Cross-platform (Windows, Linux, Mac)
    - Persistent storage
    - Metadata filtering
    - Good performance
    """
    
    def __init__(self, dimension: int = 1536, metric: str = "cosine", 
                 collection_name: str = "default", persist_directory: Optional[str] = None):
        """
        Initialize ChromaDB vector store.
        
        Args:
            dimension: Vector dimension
            metric: Similarity metric ("cosine", "l2", "ip")
            collection_name: Collection name
            persist_directory: Directory to persist data
        """
        if not CHROMA_AVAILABLE:
            raise ImportError("ChromaDB not available. Install with: pip install chromadb")
        
        self.dimension = dimension
        self.metric = metric
        self.collection_name = collection_name
        
        # Initialize ChromaDB client
        if persist_directory:
            self.client = chromadb.PersistentClient(path=persist_directory)
        else:
            self.client = chromadb.Client()
        
        # Get or create collection
        try:
            self.collection = self.client.get_collection(name=collection_name)
        except:
            self.collection = self.client.create_collection(
                name=collection_name,
                metadata={"hnsw:space": metric}  # HNSW for fast search
            )
        
        self.vector_count = 0
    
    def add_vectors(self, node_ids: List[str], vectors: List[List[float]], 
                   metadata_list: Optional[List[Dict[str, Any]]] = None) -> bool:
        """Add vectors to ChromaDB."""
        try:
            # Prepare documents (empty for now, can add text later)
            documents = [""] * len(node_ids)
            
            # Prepare metadata
            metadatas = []
            for i, node_id in enumerate(node_ids):
                meta = {"node_id": node_id}
                if metadata_list and i < len(metadata_list):
                    meta.update(metadata_list[i])
                metadatas.append(meta)
            
            # Add to collection
            self.collection.add(
                ids=node_ids,
                embeddings=vectors,
                documents=documents,
                metadatas=metadatas
            )
            
            self.vector_count = self.collection.count()
            return True
            
        except Exception as e:
            logger.error(f"Failed to add vectors to ChromaDB: {e}")
            return False
    
    def search(self, query_vector: List[float], k: int = 10, 
              filter_ids: Optional[List[str]] = None) -> List[Dict[str, Any]]:
        """Search for similar vectors."""
        try:
            # Build where clause if filtering
            where = None
            if filter_ids:
                where = {"node_id": {"$in": filter_ids}}
            
            # Search
            results = self.collection.query(
                query_embeddings=[query_vector],
                n_results=k,
                where=where
            )
            
            # Format results
            formatted_results = []
            if results['ids'] and len(results['ids'][0]) > 0:
                for i, node_id in enumerate(results['ids'][0]):
                    score = 1.0 - results['distances'][0][i] if results.get('distances') else 1.0
                    metadata = results['metadatas'][0][i] if results.get('metadatas') else {}
                    
                    formatted_results.append({
                        'node_id': node_id,
                        'score': score,
                        'distance': results['distances'][0][i] if results.get('distances') else 0.0,
                        'metadata': metadata
                    })
            
            return formatted_results
            
        except Exception as e:
            logger.error(f"ChromaDB search failed: {e}")
            return []
    
    def save(self, filepath: str) -> bool:
        """Save is automatic with ChromaDB persistent client."""
        # ChromaDB automatically persists when using PersistentClient
        return True
    
    def load(self, filepath: str) -> bool:
        """Load is automatic with ChromaDB persistent client."""
        # ChromaDB automatically loads when using PersistentClient
        self.vector_count = self.collection.count()
        return True
    
    def get_vector_count(self) -> int:
        """Get number of vectors."""
        try:
            self.vector_count = self.collection.count()
            return self.vector_count
        except:
            return 0




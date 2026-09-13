"""
Custom Vector Store for AIContextDB
Lightweight, cross-platform vector database using NumPy.
Works on Windows, Linux, and Mac without external dependencies.
"""

import numpy as np
import json
import pickle
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple
from datetime import datetime
import logging
from collections import defaultdict

logger = logging.getLogger(__name__)

class CustomVectorStore:
    """
    Custom vector store using NumPy for efficient similarity search.
    
    Features:
    - Fast similarity search using NumPy vectorized operations
    - Approximate Nearest Neighbor (ANN) support for large-scale search
    - Persistent storage (pickle for vectors, JSON for metadata)
    - Cosine similarity by default
    - Batch operations
    - Cross-platform (works on Windows, Linux, Mac)
    - No external dependencies beyond NumPy
    """
    
    def __init__(self, dimension: int = 1536, metric: str = "cosine", use_ann: bool = True, ann_algorithm: str = "lsh"):
        """
        Initialize custom vector store.
        
        Args:
            dimension: Vector dimension (e.g., 1536, 3072)
            metric: Similarity metric ("cosine", "euclidean", "dot")
            use_ann: Whether to use Approximate Nearest Neighbor (faster for large datasets)
            ann_algorithm: ANN algorithm ("lsh", "random_projection", "exact")
        """
        self.dimension = dimension
        self.metric = metric
        self.use_ann = use_ann
        self.ann_algorithm = ann_algorithm
        
        # Store vectors as numpy array (n_vectors x dimension)
        self.vectors: Optional[np.ndarray] = None
        
        # Metadata storage: map node_id to index position and metadata
        self.id_to_index: Dict[str, int] = {}  # node_id -> index position
        self.index_to_id: Dict[int, str] = {}  # index position -> node_id
        self.metadata: Dict[str, Dict[str, Any]] = {}  # node_id -> metadata
        
        # ANN structures
        self.ann_index = None  # ANN index structure
        self.ann_threshold = 1000  # Use ANN if more than this many vectors
        
        self.vector_count = 0
    
    def add_vectors(self, node_ids: List[str], vectors: List[List[float]], 
                   metadata_list: Optional[List[Dict[str, Any]]] = None) -> bool:
        """
        Add vectors to the store.
        
        Args:
            node_ids: List of node IDs
            vectors: List of embedding vectors (each is a list of floats)
            metadata_list: Optional list of metadata dicts
        
        Returns:
            True if successful
        """
        if len(node_ids) != len(vectors):
            raise ValueError(f"node_ids ({len(node_ids)}) and vectors ({len(vectors)}) must have same length")
        
        try:
            # Convert to numpy array
            new_vectors = np.array(vectors, dtype=np.float32)
            
            # Validate dimensions
            if new_vectors.shape[1] != self.dimension:
                raise ValueError(f"Vector dimension {new_vectors.shape[1]} doesn't match store dimension {self.dimension}")
            
            # Normalize for cosine similarity
            if self.metric == "cosine":
                # Normalize each vector to unit length
                norms = np.linalg.norm(new_vectors, axis=1, keepdims=True)
                norms[norms == 0] = 1  # Avoid division by zero
                new_vectors = new_vectors / norms
            
            # Append to existing vectors or create new array
            if self.vectors is None:
                self.vectors = new_vectors
            else:
                self.vectors = np.vstack([self.vectors, new_vectors])
            
            # Store metadata
            start_index = self.vector_count
            for i, node_id in enumerate(node_ids):
                index_pos = start_index + i
                self.id_to_index[node_id] = index_pos
                self.index_to_id[index_pos] = node_id
                
                if metadata_list and i < len(metadata_list):
                    self.metadata[node_id] = metadata_list[i]
                else:
                    self.metadata[node_id] = {}
            
            self.vector_count = len(self.vectors)
            
            # Rebuild ANN index if using ANN
            if self.use_ann and self.vector_count > self.ann_threshold:
                self.ann_index = None  # Force rebuild
                self._build_lsh_index()
            
            return True
            
        except Exception as e:
            logger.error(f"Failed to add vectors: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return False
    
    def search(self, query_vector: List[float], k: int = 10, 
              filter_ids: Optional[List[str]] = None) -> List[Dict[str, Any]]:
        """
        Search for similar vectors using exact or approximate nearest neighbor.
        
        Args:
            query_vector: Query embedding vector
            k: Number of results to return
            filter_ids: Optional list of node IDs to filter by
        
        Returns:
            List of results with node_id, score, and metadata
        """
        if self.vector_count == 0 or self.vectors is None:
            return []
        
        try:
            # Use ANN for large datasets, exact search for small ones
            use_ann_search = self.use_ann and self.vector_count > self.ann_threshold
            
            if use_ann_search:
                return self._search_ann(query_vector, k, filter_ids)
            else:
                return self._search_exact(query_vector, k, filter_ids)
                
        except Exception as e:
            logger.error(f"Search failed: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return []
    
    def _search_exact(self, query_vector: List[float], k: int = 10, 
                     filter_ids: Optional[List[str]] = None) -> List[Dict[str, Any]]:
        """Exact nearest neighbor search (brute force)."""
        # Convert query to numpy array
        query = np.array([query_vector], dtype=np.float32)
        
        # Normalize for cosine similarity
        if self.metric == "cosine":
            norm = np.linalg.norm(query)
            if norm > 0:
                query = query / norm
        
        # Calculate similarities using vectorized operations
        if self.metric == "cosine" or self.metric == "dot":
            # Cosine similarity: dot product of normalized vectors
            similarities = np.dot(self.vectors, query.T).flatten()
        elif self.metric == "euclidean":
            # Euclidean distance: lower is better, so we'll convert to similarity
            distances = np.linalg.norm(self.vectors - query, axis=1)
            similarities = 1.0 / (1.0 + distances)  # Convert distance to similarity
        else:
            raise ValueError(f"Unsupported metric: {self.metric}")
        
        # Get top-k indices
        k = min(k, self.vector_count)
        top_indices = np.argsort(similarities)[::-1][:k]  # Sort descending, take top k
        
        results = []
        for idx in top_indices:
            node_id = self.index_to_id.get(int(idx))
            if node_id is None:
                continue
            
            # Apply filter if provided
            if filter_ids and node_id not in filter_ids:
                continue
            
            score = float(similarities[idx])
            
            results.append({
                'node_id': node_id,
                'score': score,
                'distance': float(1.0 - score) if self.metric == "cosine" else float(1.0 / (1.0 + score)),
                'metadata': self.metadata.get(node_id, {})
            })
        
        return results
    
    def _search_ann(self, query_vector: List[float], k: int = 10, 
                   filter_ids: Optional[List[str]] = None) -> List[Dict[str, Any]]:
        """
        Approximate Nearest Neighbor search.
        Uses Locality-Sensitive Hashing (LSH) or random projection for fast search.
        """
        if self.ann_algorithm == "lsh":
            return self._search_lsh(query_vector, k, filter_ids)
        elif self.ann_algorithm == "random_projection":
            return self._search_random_projection(query_vector, k, filter_ids)
        else:
            # Fallback to exact search
            return self._search_exact(query_vector, k, filter_ids)
    
    def _search_lsh(self, query_vector: List[float], k: int = 10, 
                   filter_ids: Optional[List[str]] = None) -> List[Dict[str, Any]]:
        """
        Locality-Sensitive Hashing (LSH) for approximate search.
        Simple implementation using random hyperplanes.
        """
        # For LSH, we use a simple approach: random projection + hash buckets
        # This is a simplified version - for production, consider using a library
        
        # Build LSH index if not exists
        if self.ann_index is None:
            self._build_lsh_index()
        
        # Convert query to numpy array
        query = np.array(query_vector, dtype=np.float32)
        
        # Normalize for cosine similarity
        if self.metric == "cosine":
            norm = np.linalg.norm(query)
            if norm > 0:
                query = query / norm
        
        # Hash query to find candidate buckets
        query_hash = self._hash_vector(query)
        
        # Get candidates from same or nearby buckets
        candidates = self._get_lsh_candidates(query_hash, query, k * 3)  # Get 3x candidates for refinement
        
        # Refine candidates with exact similarity
        if len(candidates) > 0:
            candidate_vectors = self.vectors[candidates]
            if self.metric == "cosine" or self.metric == "dot":
                similarities = np.dot(candidate_vectors, query).flatten()
            else:
                distances = np.linalg.norm(candidate_vectors - query, axis=1)
                similarities = 1.0 / (1.0 + distances)
            
            # Get top-k from candidates
            top_candidate_indices = np.argsort(similarities)[::-1][:k]
            top_indices = candidates[top_candidate_indices]
        else:
            # Fallback to exact search if no candidates
            return self._search_exact(query_vector, k, filter_ids)
        
        # Format results
        results = []
        for idx in top_indices:
            node_id = self.index_to_id.get(int(idx))
            if node_id is None:
                continue
            
            if filter_ids and node_id not in filter_ids:
                continue
            
            # Calculate exact similarity for result
            if self.metric == "cosine" or self.metric == "dot":
                score = float(np.dot(self.vectors[idx], query))
            else:
                dist = np.linalg.norm(self.vectors[idx] - query)
                score = float(1.0 / (1.0 + dist))
            
            results.append({
                'node_id': node_id,
                'score': score,
                'distance': float(1.0 - score) if self.metric == "cosine" else float(1.0 / (1.0 + score)),
                'metadata': self.metadata.get(node_id, {})
            })
        
        return results
    
    def _build_lsh_index(self):
        """Build LSH index for approximate search."""
        if self.vectors is None or len(self.vectors) == 0:
            return
        
        # Simple LSH: use random hyperplanes to create hash buckets
        num_hyperplanes = min(10, self.dimension // 10)  # Number of random hyperplanes
        np.random.seed(42)  # For reproducibility
        
        # Generate random hyperplanes
        self.lsh_hyperplanes = np.random.randn(num_hyperplanes, self.dimension).astype(np.float32)
        
        # Normalize hyperplanes
        norms = np.linalg.norm(self.lsh_hyperplanes, axis=1, keepdims=True)
        norms[norms == 0] = 1
        self.lsh_hyperplanes = self.lsh_hyperplanes / norms
        
        # Build hash buckets
        self.lsh_buckets: Dict[int, List[int]] = defaultdict(list)
        
        for idx in range(len(self.vectors)):
            vector = self.vectors[idx]
            if self.metric == "cosine":
                # Normalize vector
                norm = np.linalg.norm(vector)
                if norm > 0:
                    vector = vector / norm
            
            # Hash vector
            hash_val = self._hash_vector(vector)
            self.lsh_buckets[hash_val].append(idx)
        
        self.ann_index = "lsh"  # Mark as built
    
    def _hash_vector(self, vector: np.ndarray) -> int:
        """Hash vector using hyperplanes."""
        if not hasattr(self, 'lsh_hyperplanes'):
            return 0
        
        # Project vector onto hyperplanes and create binary hash
        projections = np.dot(self.lsh_hyperplanes, vector)
        binary_hash = (projections > 0).astype(int)
        
        # Convert binary array to integer hash
        hash_int = 0
        for bit in binary_hash:
            hash_int = (hash_int << 1) | bit
        
        return hash_int
    
    def _get_lsh_candidates(self, query_hash: int, query: np.ndarray, max_candidates: int = 100) -> np.ndarray:
        """Get candidate indices from LSH buckets."""
        candidates = set()
        
        # Get candidates from same bucket
        if query_hash in self.lsh_buckets:
            candidates.update(self.lsh_buckets[query_hash])
        
        # Get candidates from nearby buckets (Hamming distance 1)
        if hasattr(self, 'lsh_hyperplanes'):
            num_bits = len(self.lsh_hyperplanes)
            for i in range(num_bits):
                # Flip bit i
                nearby_hash = query_hash ^ (1 << (num_bits - 1 - i))
                if nearby_hash in self.lsh_buckets:
                    candidates.update(self.lsh_buckets[nearby_hash])
        
        # Limit candidates
        candidates_list = list(candidates)[:max_candidates]
        return np.array(candidates_list, dtype=int) if candidates_list else np.array([], dtype=int)
    
    def _search_random_projection(self, query_vector: List[float], k: int = 10, 
                                 filter_ids: Optional[List[str]] = None) -> List[Dict[str, Any]]:
        """
        Random projection-based approximate search.
        Projects vectors to lower dimension for faster search.
        """
        # For now, fallback to exact search
        # Can be enhanced with actual random projection
        return self._search_exact(query_vector, k, filter_ids)
    
    def save(self, filepath: str) -> bool:
        """
        Save vectors and metadata to disk.
        
        Args:
            filepath: Path to save (without extension)
        """
        try:
            path = Path(filepath)
            path.parent.mkdir(parents=True, exist_ok=True)
            
            # Save vectors as numpy file (efficient binary format)
            vectors_file = path.parent / (path.name + "_vectors.npy")
            if self.vectors is not None:
                np.save(str(vectors_file), self.vectors)
            else:
                # Create empty array to save
                np.save(str(vectors_file), np.array([], dtype=np.float32).reshape(0, self.dimension))
            
            # Save metadata as pickle (preserves dict structure)
            metadata_file = path.parent / (path.name + "_metadata.pkl")
            with open(metadata_file, 'wb') as f:
                pickle.dump({
                    'id_to_index': self.id_to_index,
                    'index_to_id': self.index_to_id,
                    'metadata': self.metadata,
                    'vector_count': self.vector_count,
                    'total_vectors': self.vector_count,  # Add alias for compatibility
                    'count': self.vector_count,  # Add another alias
                    'dimension': self.dimension,
                    'metric': self.metric,
                    'use_ann': self.use_ann,
                    'ann_algorithm': self.ann_algorithm,
                    'lsh_hyperplanes': getattr(self, 'lsh_hyperplanes', None),
                    'lsh_buckets': dict(getattr(self, 'lsh_buckets', {}))
                }, f)
            
            logger.info(f"Saved vector store to {path} ({self.vector_count} vectors)")
            return True
            
        except Exception as e:
            logger.error(f"Failed to save vector store: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return False
    
    def load(self, filepath: str) -> bool:
        """
        Load vectors and metadata from disk.
        
        Args:
            filepath: Path to load from (without extension)
        """
        try:
            path = Path(filepath)
            
            # Load vectors
            vectors_file = path.parent / (path.name + "_vectors.npy")
            if not vectors_file.exists():
                logger.warning(f"Vectors file not found: {vectors_file}")
                return False
            
            self.vectors = np.load(str(vectors_file))
            
            # Load metadata
            metadata_file = path.parent / (path.name + "_metadata.pkl")
            if not metadata_file.exists():
                logger.warning(f"Metadata file not found: {metadata_file}")
                return False
            
            with open(metadata_file, 'rb') as f:
                data = pickle.load(f)
                self.id_to_index = data.get('id_to_index', {})
                self.index_to_id = data.get('index_to_id', {})
                self.metadata = data.get('metadata', {})
                self.vector_count = data.get('vector_count', 0)
                self.dimension = data.get('dimension', self.dimension)
                self.metric = data.get('metric', self.metric)
                self.use_ann = data.get('use_ann', self.use_ann)
                self.ann_algorithm = data.get('ann_algorithm', self.ann_algorithm)
                
                # Restore LSH index if available
                if 'lsh_hyperplanes' in data and data['lsh_hyperplanes'] is not None:
                    self.lsh_hyperplanes = data['lsh_hyperplanes']
                    self.lsh_buckets = defaultdict(list, data.get('lsh_buckets', {}))
                    self.ann_index = "lsh"
            
            # Validate
            if self.vectors is not None and len(self.vectors) != self.vector_count:
                logger.warning(f"Vector count mismatch: {len(self.vectors)} vectors vs {self.vector_count} metadata entries")
                self.vector_count = len(self.vectors)
            
            logger.info(f"Loaded vector store from {vectors_file} ({self.vector_count} vectors)")
            return True
            
        except Exception as e:
            logger.error(f"Failed to load vector store: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return False
    
    def get_vector_count(self) -> int:
        """Get number of vectors in store."""
        return self.vector_count
    
    def remove_vectors(self, node_ids: List[str]) -> bool:
        """
        Remove vectors from store.
        Note: This rebuilds the index, which can be slow for large stores.
        """
        if not node_ids:
            return True
        
        try:
            if self.vectors is None:
                return True
            
            # Get indices to keep
            indices_to_keep = []
            new_id_to_index = {}
            new_index_to_id = {}
            new_metadata = {}
            
            new_idx = 0
            for old_idx in range(self.vector_count):
                node_id = self.index_to_id.get(old_idx)
                if node_id and node_id not in node_ids:
                    indices_to_keep.append(old_idx)
                    new_id_to_index[node_id] = new_idx
                    new_index_to_id[new_idx] = node_id
                    new_metadata[node_id] = self.metadata.get(node_id, {})
                    new_idx += 1
            
            # Rebuild vectors array
            if indices_to_keep:
                self.vectors = self.vectors[indices_to_keep]
            else:
                self.vectors = None
            
            # Update metadata
            self.id_to_index = new_id_to_index
            self.index_to_id = new_index_to_id
            self.metadata = new_metadata
            self.vector_count = len(indices_to_keep) if indices_to_keep else 0
            
            return True
            
        except Exception as e:
            logger.error(f"Failed to remove vectors: {e}")
            return False
    
    def get_vector(self, node_id: str) -> Optional[np.ndarray]:
        """Get vector for a specific node ID."""
        if self.vectors is None:
            return None
        
        idx = self.id_to_index.get(node_id)
        if idx is None:
            return None
        
        return self.vectors[idx]
    
    def batch_add(self, batch_size: int = 100) -> bool:
        """
        Prepare for batch operations.
        This is a placeholder for future batch optimization.
        """
        return True


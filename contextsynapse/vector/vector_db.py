"""
AIContextDB Universal Vector Database System
Self-contained universal vector database with multiple provider support.
Extracted and adapted from QGraph Universal Vector Database.
"""

import asyncio
import numpy as np
import logging
from typing import Dict, List, Any, Optional, Union, Tuple, Callable
from dataclasses import dataclass
from enum import Enum
import time
import hashlib
from datetime import datetime
import json
import threading
from collections import defaultdict
import os

# Try to import various vector database libraries
try:
    import faiss
    FAISS_AVAILABLE = True
except ImportError:
    FAISS_AVAILABLE = False

try:
    import chromadb
    CHROMA_AVAILABLE = True
except ImportError:
    CHROMA_AVAILABLE = False

try:
    import pinecone
    PINECONE_AVAILABLE = True
except ImportError:
    PINECONE_AVAILABLE = False

try:
    import weaviate
    WEAVIATE_AVAILABLE = True
except ImportError:
    WEAVIATE_AVAILABLE = False

try:
    import qdrant_client
    QDRANT_AVAILABLE = True
except ImportError:
    QDRANT_AVAILABLE = False

logger = logging.getLogger(__name__)

class VectorDBProvider(Enum):
    """Supported vector database providers."""
    FAISS = "faiss"
    CHROMA = "chroma"
    PINECONE = "pinecone"
    WEAVIATE = "weaviate"
    QDRANT = "qdrant"
    CUSTOM = "custom"

@dataclass
class VectorDBCapabilities:
    """Vector database capabilities."""
    supports_metadata: bool = True
    supports_filtering: bool = True
    supports_batch_operations: bool = True
    supports_upsert: bool = True
    supports_delete: bool = True
    supports_update: bool = True
    supports_similarity_search: bool = True
    supports_hybrid_search: bool = False
    supports_quantization: bool = False
    supports_sharding: bool = False
    max_dimensions: int = 2048
    max_vectors: int = 1000000

@dataclass
class VectorDBConfig:
    """Configuration for universal vector database manager."""
    default_provider: VectorDBProvider = VectorDBProvider.FAISS
    providers: Dict[VectorDBProvider, Dict[str, Any]] = None
    dimension: int = 768
    metric: str = "cosine"  # cosine, euclidean, dot
    batch_size: int = 1000
    enable_compression: bool = False
    enable_quantization: bool = False
    enable_sharding: bool = False
    shard_count: int = 4
    cache_size: int = 10000
    enable_logging: bool = True

@dataclass
class VectorResult:
    """Result of vector database operation."""
    success: bool
    data: Any = None
    error: Optional[str] = None
    metadata: Dict[str, Any] = None

class AIContextDBUniversalVectorDBManager:
    """
    AIContextDB Universal vector database manager that supports any vector database provider.
    """
    
    def __init__(self, config: Optional[VectorDBConfig] = None):
        self.config = config or VectorDBConfig()
        self.providers = {}  # provider -> instance
        self.collections = {}  # collection_name -> provider
        self.usage_stats = defaultdict(int)
        self.lock = threading.Lock()
        
        # Initialize providers
        self._initialize_providers()
        
        logger.info("AIContextDB Universal Vector DB Manager initialized")
    
    def _initialize_providers(self) -> None:
        """Initialize vector database providers."""
        # FAISS
        if FAISS_AVAILABLE:
            self.providers[VectorDBProvider.FAISS] = self._create_faiss_provider()
        
        # Chroma
        if CHROMA_AVAILABLE:
            self.providers[VectorDBProvider.CHROMA] = self._create_chroma_provider()
        
        # Pinecone
        if PINECONE_AVAILABLE:
            self.providers[VectorDBProvider.PINECONE] = self._create_pinecone_provider()
        
        # Weaviate
        if WEAVIATE_AVAILABLE:
            self.providers[VectorDBProvider.WEAVIATE] = self._create_weaviate_provider()
        
        # Qdrant
        if QDRANT_AVAILABLE:
            self.providers[VectorDBProvider.QDRANT] = self._create_qdrant_provider()
    
    def _create_faiss_provider(self) -> Dict[str, Any]:
        """Create FAISS provider."""
        return {
            "type": "faiss",
            "indexes": {},
            "capabilities": VectorDBCapabilities(
                supports_metadata=True,
                supports_filtering=False,
                supports_batch_operations=True,
                supports_upsert=True,
                supports_delete=True,
                supports_update=True,
                supports_similarity_search=True,
                supports_hybrid_search=False,
                supports_quantization=True,
                supports_sharding=False,
                max_dimensions=2048,
                max_vectors=1000000
            )
        }
    
    def _create_chroma_provider(self) -> Dict[str, Any]:
        """Create Chroma provider."""
        return {
            "type": "chroma",
            "client": None,  # Will be initialized when needed
            "capabilities": VectorDBCapabilities(
                supports_metadata=True,
                supports_filtering=True,
                supports_batch_operations=True,
                supports_upsert=True,
                supports_delete=True,
                supports_update=True,
                supports_similarity_search=True,
                supports_hybrid_search=True,
                supports_quantization=False,
                supports_sharding=True,
                max_dimensions=2048,
                max_vectors=10000000
            )
        }
    
    def _create_pinecone_provider(self) -> Dict[str, Any]:
        """Create Pinecone provider."""
        return {
            "type": "pinecone",
            "client": None,  # Will be initialized when needed
            "capabilities": VectorDBCapabilities(
                supports_metadata=True,
                supports_filtering=True,
                supports_batch_operations=True,
                supports_upsert=True,
                supports_delete=True,
                supports_update=True,
                supports_similarity_search=True,
                supports_hybrid_search=True,
                supports_quantization=False,
                supports_sharding=True,
                max_dimensions=2048,
                max_vectors=100000000
            )
        }
    
    def _create_weaviate_provider(self) -> Dict[str, Any]:
        """Create Weaviate provider."""
        return {
            "type": "weaviate",
            "client": None,  # Will be initialized when needed
            "capabilities": VectorDBCapabilities(
                supports_metadata=True,
                supports_filtering=True,
                supports_batch_operations=True,
                supports_upsert=True,
                supports_delete=True,
                supports_update=True,
                supports_similarity_search=True,
                supports_hybrid_search=True,
                supports_quantization=False,
                supports_sharding=True,
                max_dimensions=2048,
                max_vectors=100000000
            )
        }
    
    def _create_qdrant_provider(self) -> Dict[str, Any]:
        """Create Qdrant provider."""
        return {
            "type": "qdrant",
            "client": None,  # Will be initialized when needed
            "capabilities": VectorDBCapabilities(
                supports_metadata=True,
                supports_filtering=True,
                supports_batch_operations=True,
                supports_upsert=True,
                supports_delete=True,
                supports_update=True,
                supports_similarity_search=True,
                supports_hybrid_search=True,
                supports_quantization=True,
                supports_sharding=True,
                max_dimensions=2048,
                max_vectors=100000000
            )
        }
    
    async def create_collection(self, collection_name: str, 
                              dimension: Optional[int] = None,
                              provider: Optional[VectorDBProvider] = None) -> bool:
        """Create a new collection."""
        try:
            if provider is None:
                provider = self.config.default_provider
            
            if dimension is None:
                dimension = self.config.dimension
            
            # Check if collection already exists
            if collection_name in self.collections:
                logger.warning(f"Collection {collection_name} already exists")
                return False
            
            # Create collection with provider
            success = await self._create_collection_with_provider(
                collection_name, dimension, provider
            )
            
            if success:
                self.collections[collection_name] = provider
                self.usage_stats[f"collections_created_{provider.value}"] += 1
                logger.info(f"Created collection {collection_name} with {provider.value}")
            
            return success
            
        except Exception as e:
            logger.error(f"Error creating collection {collection_name}: {e}")
            return False
    
    async def _create_collection_with_provider(self, collection_name: str, 
                                             dimension: int, 
                                             provider: VectorDBProvider) -> bool:
        """Create collection with specific provider."""
        if provider == VectorDBProvider.FAISS:
            return await self._create_faiss_collection(collection_name, dimension)
        elif provider == VectorDBProvider.CHROMA:
            return await self._create_chroma_collection(collection_name, dimension)
        elif provider == VectorDBProvider.PINECONE:
            return await self._create_pinecone_collection(collection_name, dimension)
        elif provider == VectorDBProvider.WEAVIATE:
            return await self._create_weaviate_collection(collection_name, dimension)
        elif provider == VectorDBProvider.QDRANT:
            return await self._create_qdrant_collection(collection_name, dimension)
        else:
            raise ValueError(f"Unsupported provider: {provider}")
    
    async def _create_faiss_collection(self, collection_name: str, dimension: int) -> bool:
        """Create FAISS collection."""
        if not FAISS_AVAILABLE:
            raise ImportError("FAISS library not available")
        
        try:
            # Create FAISS index
            if self.config.metric == "cosine":
                index = faiss.IndexFlatIP(dimension)  # Inner product for cosine similarity
            elif self.config.metric == "euclidean":
                index = faiss.IndexFlatL2(dimension)  # L2 distance for euclidean
            else:
                index = faiss.IndexFlatIP(dimension)  # Default to inner product
            
            # Store index
            self.providers[VectorDBProvider.FAISS]["indexes"][collection_name] = {
                "index": index,
                "dimension": dimension,
                "vectors": [],
                "metadata": []
            }
            
            return True
            
        except Exception as e:
            logger.error(f"Error creating FAISS collection: {e}")
            return False
    
    async def _create_chroma_collection(self, collection_name: str, dimension: int) -> bool:
        """Create Chroma collection."""
        if not CHROMA_AVAILABLE:
            raise ImportError("Chroma library not available")
        
        try:
            # Initialize Chroma client if not already done
            if self.providers[VectorDBProvider.CHROMA]["client"] is None:
                client = chromadb.Client()
                self.providers[VectorDBProvider.CHROMA]["client"] = client
            
            client = self.providers[VectorDBProvider.CHROMA]["client"]
            
            # Create collection
            collection = client.create_collection(
                name=collection_name,
                metadata={"dimension": dimension}
            )
            
            # Store collection reference
            self.providers[VectorDBProvider.CHROMA]["collections"] = self.providers[VectorDBProvider.CHROMA].get("collections", {})
            self.providers[VectorDBProvider.CHROMA]["collections"][collection_name] = collection
            
            return True
            
        except Exception as e:
            logger.error(f"Error creating Chroma collection: {e}")
            return False
    
    async def _create_pinecone_collection(self, collection_name: str, dimension: int) -> bool:
        """Create Pinecone collection."""
        if not PINECONE_AVAILABLE:
            raise ImportError("Pinecone library not available")
        
        try:
            # Initialize Pinecone client if not already done
            if self.providers[VectorDBProvider.PINECONE]["client"] is None:
                pinecone.init(
                    api_key=self.config.providers.get(VectorDBProvider.PINECONE, {}).get("api_key"),
                    environment=self.config.providers.get(VectorDBProvider.PINECONE, {}).get("environment")
                )
                client = pinecone.Index(collection_name)
                self.providers[VectorDBProvider.PINECONE]["client"] = client
            
            # Create index
            pinecone.create_index(
                name=collection_name,
                dimension=dimension,
                metric=self.config.metric
            )
            
            return True
            
        except Exception as e:
            logger.error(f"Error creating Pinecone collection: {e}")
            return False
    
    async def _create_weaviate_collection(self, collection_name: str, dimension: int) -> bool:
        """Create Weaviate collection."""
        if not WEAVIATE_AVAILABLE:
            raise ImportError("Weaviate library not available")
        
        try:
            # Initialize Weaviate client if not already done
            if self.providers[VectorDBProvider.WEAVIATE]["client"] is None:
                client = weaviate.Client(
                    url=self.config.providers.get(VectorDBProvider.WEAVIATE, {}).get("url", "http://localhost:8080")
                )
                self.providers[VectorDBProvider.WEAVIATE]["client"] = client
            
            client = self.providers[VectorDBProvider.WEAVIATE]["client"]
            
            # Create schema
            schema = {
                "class": collection_name,
                "vectorizer": "none",  # We'll provide vectors manually
                "properties": [
                    {"name": "text", "dataType": ["text"]},
                    {"name": "metadata", "dataType": ["object"]}
                ]
            }
            
            client.schema.create_class(schema)
            
            return True
            
        except Exception as e:
            logger.error(f"Error creating Weaviate collection: {e}")
            return False
    
    async def _create_qdrant_collection(self, collection_name: str, dimension: int) -> bool:
        """Create Qdrant collection."""
        if not QDRANT_AVAILABLE:
            raise ImportError("Qdrant library not available")
        
        try:
            # Initialize Qdrant client if not already done
            if self.providers[VectorDBProvider.QDRANT]["client"] is None:
                client = qdrant_client.QdrantClient(
                    url=self.config.providers.get(VectorDBProvider.QDRANT, {}).get("url", "http://localhost:6333")
                )
                self.providers[VectorDBProvider.QDRANT]["client"] = client
            
            client = self.providers[VectorDBProvider.QDRANT]["client"]
            
            # Create collection
            client.create_collection(
                collection_name=collection_name,
                vectors_config=qdrant_client.VectorParams(
                    size=dimension,
                    distance=qdrant_client.Distance.COSINE if self.config.metric == "cosine" else qdrant_client.Distance.EUCLID
                )
            )
            
            return True
            
        except Exception as e:
            logger.error(f"Error creating Qdrant collection: {e}")
            return False
    
    async def insert_vectors(self, collection_name: str, 
                           vectors: List[List[float]], 
                           ids: Optional[List[str]] = None,
                           metadata: Optional[List[Dict[str, Any]]] = None) -> bool:
        """Insert vectors into collection."""
        try:
            if collection_name not in self.collections:
                logger.error(f"Collection {collection_name} does not exist")
                return False
            
            provider = self.collections[collection_name]
            
            # Generate IDs if not provided
            if ids is None:
                ids = [f"vector_{i}_{int(time.time())}" for i in range(len(vectors))]
            
            # Generate metadata if not provided
            if metadata is None:
                metadata = [{} for _ in range(len(vectors))]
            
            # Insert with provider
            success = await self._insert_vectors_with_provider(
                collection_name, vectors, ids, metadata, provider
            )
            
            if success:
                self.usage_stats[f"vectors_inserted_{provider.value}"] += len(vectors)
                logger.info(f"Inserted {len(vectors)} vectors into {collection_name}")
            
            return success
            
        except Exception as e:
            logger.error(f"Error inserting vectors: {e}")
            return False
    
    async def _insert_vectors_with_provider(self, collection_name: str, 
                                          vectors: List[List[float]], 
                                          ids: List[str], 
                                          metadata: List[Dict[str, Any]], 
                                          provider: VectorDBProvider) -> bool:
        """Insert vectors with specific provider."""
        if provider == VectorDBProvider.FAISS:
            return await self._insert_faiss_vectors(collection_name, vectors, ids, metadata)
        elif provider == VectorDBProvider.CHROMA:
            return await self._insert_chroma_vectors(collection_name, vectors, ids, metadata)
        elif provider == VectorDBProvider.PINECONE:
            return await self._insert_pinecone_vectors(collection_name, vectors, ids, metadata)
        elif provider == VectorDBProvider.WEAVIATE:
            return await self._insert_weaviate_vectors(collection_name, vectors, ids, metadata)
        elif provider == VectorDBProvider.QDRANT:
            return await self._insert_qdrant_vectors(collection_name, vectors, ids, metadata)
        else:
            raise ValueError(f"Unsupported provider: {provider}")
    
    async def _insert_faiss_vectors(self, collection_name: str, 
                                   vectors: List[List[float]], 
                                   ids: List[str], 
                                   metadata: List[Dict[str, Any]]) -> bool:
        """Insert vectors into FAISS collection."""
        try:
            collection_data = self.providers[VectorDBProvider.FAISS]["indexes"][collection_name]
            index = collection_data["index"]
            
            # Convert to numpy array
            vectors_array = np.array(vectors, dtype=np.float32)
            
            # Add vectors to index
            index.add(vectors_array)
            
            # Store metadata
            collection_data["vectors"].extend(vectors)
            collection_data["metadata"].extend(metadata)
            
            return True
            
        except Exception as e:
            logger.error(f"Error inserting FAISS vectors: {e}")
            return False
    
    async def _insert_chroma_vectors(self, collection_name: str, 
                                   vectors: List[List[float]], 
                                   ids: List[str], 
                                   metadata: List[Dict[str, Any]]) -> bool:
        """Insert vectors into Chroma collection."""
        try:
            collection = self.providers[VectorDBProvider.CHROMA]["collections"][collection_name]
            
            # Prepare data for Chroma
            documents = [meta.get("text", "") for meta in metadata]
            metadatas = [meta for meta in metadata]
            
            # Add vectors
            collection.add(
                embeddings=vectors,
                documents=documents,
                metadatas=metadatas,
                ids=ids
            )
            
            return True
            
        except Exception as e:
            logger.error(f"Error inserting Chroma vectors: {e}")
            return False
    
    async def _insert_pinecone_vectors(self, collection_name: str, 
                                     vectors: List[List[float]], 
                                     ids: List[str], 
                                     metadata: List[Dict[str, Any]]) -> bool:
        """Insert vectors into Pinecone collection."""
        try:
            index = self.providers[VectorDBProvider.PINECONE]["client"]
            
            # Prepare data for Pinecone
            vectors_data = []
            for i, (vector, vector_id, meta) in enumerate(zip(vectors, ids, metadata)):
                vectors_data.append({
                    "id": vector_id,
                    "values": vector,
                    "metadata": meta
                })
            
            # Upsert vectors
            index.upsert(vectors=vectors_data)
            
            return True
            
        except Exception as e:
            logger.error(f"Error inserting Pinecone vectors: {e}")
            return False
    
    async def _insert_weaviate_vectors(self, collection_name: str, 
                                     vectors: List[List[float]], 
                                     ids: List[str], 
                                     metadata: List[Dict[str, Any]]) -> bool:
        """Insert vectors into Weaviate collection."""
        try:
            client = self.providers[VectorDBProvider.WEAVIATE]["client"]
            
            # Prepare data for Weaviate
            objects = []
            for i, (vector, vector_id, meta) in enumerate(zip(vectors, ids, metadata)):
                objects.append({
                    "class": collection_name,
                    "id": vector_id,
                    "vector": vector,
                    "properties": {
                        "text": meta.get("text", ""),
                        "metadata": meta
                    }
                })
            
            # Batch insert
            client.batch.create_objects(objects)
            
            return True
            
        except Exception as e:
            logger.error(f"Error inserting Weaviate vectors: {e}")
            return False
    
    async def _insert_qdrant_vectors(self, collection_name: str, 
                                   vectors: List[List[float]], 
                                   ids: List[str], 
                                   metadata: List[Dict[str, Any]]) -> bool:
        """Insert vectors into Qdrant collection."""
        try:
            client = self.providers[VectorDBProvider.QDRANT]["client"]
            
            # Prepare data for Qdrant
            points = []
            for i, (vector, vector_id, meta) in enumerate(zip(vectors, ids, metadata)):
                points.append(qdrant_client.PointStruct(
                    id=vector_id,
                    vector=vector,
                    payload=meta
                ))
            
            # Upsert points
            client.upsert(
                collection_name=collection_name,
                points=points
            )
            
            return True
            
        except Exception as e:
            logger.error(f"Error inserting Qdrant vectors: {e}")
            return False
    
    async def search_vectors(self, collection_name: str, 
                           query_vector: List[float], 
                           top_k: int = 10,
                           filter_conditions: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        """Search for similar vectors."""
        try:
            if collection_name not in self.collections:
                logger.error(f"Collection {collection_name} does not exist")
                return []
            
            provider = self.collections[collection_name]
            
            # Search with provider
            results = await self._search_vectors_with_provider(
                collection_name, query_vector, top_k, filter_conditions, provider
            )
            
            # Update usage stats
            self.usage_stats[f"searches_performed_{provider.value}"] += 1
            
            return results
            
        except Exception as e:
            logger.error(f"Error searching vectors: {e}")
            return []
    
    async def _search_vectors_with_provider(self, collection_name: str, 
                                          query_vector: List[float], 
                                          top_k: int, 
                                          filter_conditions: Optional[Dict[str, Any]], 
                                          provider: VectorDBProvider) -> List[Dict[str, Any]]:
        """Search vectors with specific provider."""
        if provider == VectorDBProvider.FAISS:
            return await self._search_faiss_vectors(collection_name, query_vector, top_k)
        elif provider == VectorDBProvider.CHROMA:
            return await self._search_chroma_vectors(collection_name, query_vector, top_k, filter_conditions)
        elif provider == VectorDBProvider.PINECONE:
            return await self._search_pinecone_vectors(collection_name, query_vector, top_k, filter_conditions)
        elif provider == VectorDBProvider.WEAVIATE:
            return await self._search_weaviate_vectors(collection_name, query_vector, top_k, filter_conditions)
        elif provider == VectorDBProvider.QDRANT:
            return await self._search_qdrant_vectors(collection_name, query_vector, top_k, filter_conditions)
        else:
            raise ValueError(f"Unsupported provider: {provider}")
    
    async def _search_faiss_vectors(self, collection_name: str, 
                                  query_vector: List[float], 
                                  top_k: int) -> List[Dict[str, Any]]:
        """Search vectors in FAISS collection."""
        try:
            collection_data = self.providers[VectorDBProvider.FAISS]["indexes"][collection_name]
            index = collection_data["index"]
            
            # Convert query to numpy array
            query_array = np.array([query_vector], dtype=np.float32)
            
            # Search
            distances, indices = index.search(query_array, top_k)
            
            # Prepare results
            results = []
            for i, (distance, idx) in enumerate(zip(distances[0], indices[0])):
                if idx < len(collection_data["vectors"]):
                    results.append({
                        "id": f"vector_{idx}",
                        "distance": float(distance),
                        "vector": collection_data["vectors"][idx],
                        "metadata": collection_data["metadata"][idx]
                    })
            
            return results
            
        except Exception as e:
            logger.error(f"Error searching FAISS vectors: {e}")
            return []
    
    async def _search_chroma_vectors(self, collection_name: str, 
                                   query_vector: List[float], 
                                   top_k: int, 
                                   filter_conditions: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Search vectors in Chroma collection."""
        try:
            collection = self.providers[VectorDBProvider.CHROMA]["collections"][collection_name]
            
            # Search
            results = collection.query(
                query_embeddings=[query_vector],
                n_results=top_k,
                where=filter_conditions
            )
            
            # Prepare results
            search_results = []
            for i in range(len(results["ids"][0])):
                search_results.append({
                    "id": results["ids"][0][i],
                    "distance": results["distances"][0][i],
                    "vector": results["embeddings"][0][i],
                    "metadata": results["metadatas"][0][i]
                })
            
            return search_results
            
        except Exception as e:
            logger.error(f"Error searching Chroma vectors: {e}")
            return []
    
    async def _search_pinecone_vectors(self, collection_name: str, 
                                     query_vector: List[float], 
                                     top_k: int, 
                                     filter_conditions: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Search vectors in Pinecone collection."""
        try:
            index = self.providers[VectorDBProvider.PINECONE]["client"]
            
            # Search
            results = index.query(
                vector=query_vector,
                top_k=top_k,
                include_metadata=True,
                filter=filter_conditions
            )
            
            # Prepare results
            search_results = []
            for match in results["matches"]:
                search_results.append({
                    "id": match["id"],
                    "distance": match["score"],
                    "vector": match["values"],
                    "metadata": match["metadata"]
                })
            
            return search_results
            
        except Exception as e:
            logger.error(f"Error searching Pinecone vectors: {e}")
            return []
    
    async def _search_weaviate_vectors(self, collection_name: str, 
                                     query_vector: List[float], 
                                     top_k: int, 
                                     filter_conditions: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Search vectors in Weaviate collection."""
        try:
            client = self.providers[VectorDBProvider.WEAVIATE]["client"]
            
            # Search
            results = client.query.get(
                collection_name,
                ["text", "metadata"]
            ).with_near_vector({
                "vector": query_vector
            }).with_limit(top_k).do()
            
            # Prepare results
            search_results = []
            for obj in results["data"]["Get"][collection_name]:
                search_results.append({
                    "id": obj["_additional"]["id"],
                    "distance": obj["_additional"]["distance"],
                    "vector": query_vector,  # Weaviate doesn't return the vector
                    "metadata": obj["metadata"]
                })
            
            return search_results
            
        except Exception as e:
            logger.error(f"Error searching Weaviate vectors: {e}")
            return []
    
    async def _search_qdrant_vectors(self, collection_name: str, 
                                   query_vector: List[float], 
                                   top_k: int, 
                                   filter_conditions: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Search vectors in Qdrant collection."""
        try:
            client = self.providers[VectorDBProvider.QDRANT]["client"]
            
            # Search
            results = client.search(
                collection_name=collection_name,
                query_vector=query_vector,
                limit=top_k,
                query_filter=filter_conditions
            )
            
            # Prepare results
            search_results = []
            for point in results:
                search_results.append({
                    "id": point.id,
                    "distance": point.score,
                    "vector": point.vector,
                    "metadata": point.payload
                })
            
            return search_results
            
        except Exception as e:
            logger.error(f"Error searching Qdrant vectors: {e}")
            return []
    
    def get_collection_info(self, collection_name: str) -> Optional[Dict[str, Any]]:
        """Get information about a collection."""
        if collection_name not in self.collections:
            return None
        
        provider = self.collections[collection_name]
        capabilities = self.providers[provider]["capabilities"]
        
        return {
            "name": collection_name,
            "provider": provider.value,
            "capabilities": {
                "supports_metadata": capabilities.supports_metadata,
                "supports_filtering": capabilities.supports_filtering,
                "supports_batch_operations": capabilities.supports_batch_operations,
                "supports_upsert": capabilities.supports_upsert,
                "supports_delete": capabilities.supports_delete,
                "supports_update": capabilities.supports_update,
                "supports_similarity_search": capabilities.supports_similarity_search,
                "supports_hybrid_search": capabilities.supports_hybrid_search,
                "supports_quantization": capabilities.supports_quantization,
                "supports_sharding": capabilities.supports_sharding,
                "max_dimensions": capabilities.max_dimensions,
                "max_vectors": capabilities.max_vectors
            }
        }
    
    def get_available_providers(self) -> List[VectorDBProvider]:
        """Get list of available providers."""
        return list(self.providers.keys())
    
    def get_usage_stats(self) -> Dict[str, Any]:
        """Get usage statistics."""
        return {
            "collections": len(self.collections),
            "providers": {provider.value: "available" for provider in self.providers.keys()},
            "usage_stats": dict(self.usage_stats)
        }
    
    async def close(self) -> None:
        """Close all connections."""
        # Close provider connections
        for provider in self.providers.values():
            if provider.get("client"):
                # Close client connections
                pass
        
        logger.info("AIContextDB Universal Vector DB Manager closed")

# Legacy compatibility classes
class VectorConfig(VectorDBConfig):
    """Legacy compatibility."""
    pass

class AIContextDBVectorDB:
    """Legacy compatibility wrapper."""
    
    def __init__(self, config: VectorConfig):
        """Initialize with legacy config."""
        self.config = config
        self.manager = AIContextDBUniversalVectorDBManager(config)
        self.logger = logging.getLogger(f"{__name__}.AIContextDBVectorDB")
    
    async def create_collection(self, name: str, dimension: int = None) -> VectorResult:
        """Create collection with legacy interface."""
        try:
            success = await self.manager.create_collection(name, dimension)
            return VectorResult(success=success, data={"name": name} if success else None)
        except Exception as e:
            return VectorResult(success=False, error=str(e))
    
    async def add_vector(self, collection: str, vector: List[float], 
                        vector_id: str = None, metadata: Dict[str, Any] = None) -> VectorResult:
        """Add vector with legacy interface."""
        try:
            success = await self.manager.insert_vectors(
                collection, [vector], [vector_id] if vector_id else None, [metadata] if metadata else None
            )
            return VectorResult(success=success, data={"id": vector_id} if success else None)
        except Exception as e:
            return VectorResult(success=False, error=str(e))
    
    async def search_vectors(self, collection: str, query_vector: List[float], 
                           top_k: int = 10, threshold: float = 0.0) -> VectorResult:
        """Search vectors with legacy interface."""
        try:
            results = await self.manager.search_vectors(collection, query_vector, top_k)
            # Filter by threshold
            filtered_results = [r for r in results if (1.0 - r.get("distance", 1.0)) >= threshold]
            return VectorResult(success=True, data=filtered_results)
        except Exception as e:
            return VectorResult(success=False, error=str(e))
    
    def get_collection_info(self, collection: str) -> VectorResult:
        """Get collection info with legacy interface."""
        try:
            info = self.manager.get_collection_info(collection)
            return VectorResult(success=info is not None, data=info)
        except Exception as e:
            return VectorResult(success=False, error=str(e))
    
    def get_stats(self) -> Dict[str, Any]:
        """Get stats with legacy interface."""
        return self.manager.get_usage_stats()

# Global instances
def get_vector_db(config: VectorConfig) -> AIContextDBVectorDB:
    """Get a vector database instance."""
    return AIContextDBVectorDB(config)

def create_vector_db(backend: VectorDBProvider, dimension: int = 768) -> AIContextDBVectorDB:
    """Create a vector database instance."""
    config = VectorConfig(default_provider=backend, dimension=dimension)
    return AIContextDBVectorDB(config)

def get_universal_vector_db(config: VectorDBConfig = None) -> AIContextDBUniversalVectorDBManager:
    """Get the universal vector database manager."""
    return AIContextDBUniversalVectorDBManager(config)
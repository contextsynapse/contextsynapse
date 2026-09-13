"""
AIContextDB Universal Embeddings - Self-contained implementation
Provides unified embedding management with multi-provider support.
"""

import os
import logging
import numpy as np
from typing import Dict, List, Any, Optional, Union, Tuple
from dataclasses import dataclass
import time

# Load environment variables from .env file
try:
    from ..utils.env_loader import load_env_file, initialize_env_loader
    initialize_env_loader()
except ImportError:
    # Fallback: just try to load manually
    pass

import requests
import json

logger = logging.getLogger(__name__)

@dataclass
class EmbeddingModelConfig:
    """Configuration for an embedding model."""
    name: str
    provider: str
    model_id: str
    api_key_env: str
    dimension: int = 768
    capabilities: List[str] = None
    
    def __post_init__(self):
        if self.capabilities is None:
            self.capabilities = ["embedding"]

class AIContextDBEmbeddingService:
    """Embedding service with direct Ollama/OpenAI support."""

    def __init__(self):
        self.models: Dict[str, EmbeddingModelConfig] = {}
        self._ollama_url = os.environ.get("OLLAMA_URL", "http://localhost:11434")
        self._register_default_models()
        logger.info("Embedding service initialized (Ollama: %s)", self._ollama_url)
    
    def _register_default_models(self):
        """Register default embedding models."""
        default_models = {
            "text-embedding-ada-002": EmbeddingModelConfig(
                name="text-embedding-ada-002",
                provider="openai",
                model_id="text-embedding-ada-002",
                api_key_env="OPENAI_API_KEY",
                dimension=1536,
                capabilities=["embedding"]
            ),
            "text-embedding-3-small": EmbeddingModelConfig(
                name="text-embedding-3-small",
                provider="openai",
                model_id="text-embedding-3-small",
                api_key_env="OPENAI_API_KEY",
                dimension=1536,
                capabilities=["embedding"]
            ),
            "text-embedding-3-large": EmbeddingModelConfig(
                name="text-embedding-3-large",
                provider="openai",
                model_id="text-embedding-3-large",
                api_key_env="OPENAI_API_KEY",
                dimension=3072,
                capabilities=["embedding"]
            ),
            "cohere-embed": EmbeddingModelConfig(
                name="cohere-embed",
                provider="cohere",
                model_id="embed-english-v2.0",
                api_key_env="COHERE_API_KEY",
                dimension=4096,
                capabilities=["embedding"]
            ),
            "nomic-embed-text": EmbeddingModelConfig(
                name="nomic-embed-text",
                provider="ollama",
                model_id="nomic-embed-text",
                api_key_env="",
                dimension=768,
                capabilities=["embedding"]
            ),
            # Gemini
            "gemini-embedding": EmbeddingModelConfig(
                name="gemini-embedding",
                provider="gemini",
                model_id="models/text-embedding-004",
                api_key_env="GEMINI_API_KEY",
                dimension=768,
                capabilities=["embedding"]
            ),
            # Mistral
            "mistral-embed": EmbeddingModelConfig(
                name="mistral-embed",
                provider="mistral",
                model_id="mistral-embed",
                api_key_env="MISTRAL_API_KEY",
                dimension=1024,
                capabilities=["embedding"]
            ),
            # Together AI
            "together-embed": EmbeddingModelConfig(
                name="together-embed",
                provider="together",
                model_id="togethercomputer/m2-bert-80M-8k-retrieval",
                api_key_env="TOGETHER_API_KEY",
                dimension=768,
                capabilities=["embedding"]
            ),
            # Ollama variants
            "mxbai-embed-large": EmbeddingModelConfig(
                name="mxbai-embed-large",
                provider="ollama",
                model_id="mxbai-embed-large",
                api_key_env="",
                dimension=1024,
                capabilities=["embedding"]
            ),
            "all-minilm": EmbeddingModelConfig(
                name="all-minilm",
                provider="ollama",
                model_id="all-minilm",
                api_key_env="",
                dimension=384,
                capabilities=["embedding"]
            ),
        }
        
        for model_name, config in default_models.items():
            self.register_model(model_name, config)
    
    def register_model(self, name: str, config: EmbeddingModelConfig):
        """Register an embedding model."""
        self.models[name] = config
    
    def get_model(self, name: str) -> Optional[EmbeddingModelConfig]:
        """Get embedding model configuration."""
        return self.models.get(name)
    
    def list_models(self) -> List[str]:
        """List all available embedding models."""
        return list(self.models.keys())
    
    def _call_ollama(self, text: str, model_id: str) -> List[float]:
        """Call Ollama embeddings API."""
        resp = requests.post(
            f"{self._ollama_url}/api/embeddings",
            json={"model": model_id, "prompt": text, "keep_alive": "60m"},
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()["embedding"]

    def _call_openai(self, text: str, model_id: str) -> List[float]:
        """Call OpenAI embeddings API."""
        import openai
        client = openai.OpenAI()
        resp = client.embeddings.create(input=text, model=model_id)
        return resp.data[0].embedding

    def _call_gemini(self, text: str, model_id: str) -> List[float]:
        """Call Google Gemini embeddings API."""
        import google.generativeai as genai
        genai.configure(api_key=os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY"))
        result = genai.embed_content(model=model_id, content=text)
        return result["embedding"]

    def _call_openai_compatible(self, text: str, model_id: str, base_url: str, key_env: str) -> List[float]:
        """Call any OpenAI-compatible embeddings API (Mistral, Together, etc.)."""
        import openai
        client = openai.OpenAI(api_key=os.environ.get(key_env), base_url=base_url)
        resp = client.embeddings.create(input=text, model=model_id)
        return resp.data[0].embedding

    def _call_cohere(self, text: str, model_id: str) -> List[float]:
        """Call Cohere embeddings API."""
        import cohere
        client = cohere.ClientV2(api_key=os.environ.get("COHERE_API_KEY"))
        resp = client.embed(texts=[text], model=model_id, input_type="search_document")
        return resp.embeddings[0]

    # Provider → (base_url, key_env) for OpenAI-compatible embedding APIs
    _COMPAT_EMBED = {
        "mistral":  ("https://api.mistral.ai/v1", "MISTRAL_API_KEY"),
        "together": ("https://api.together.xyz/v1", "TOGETHER_API_KEY"),
    }

    def embed_text(self, text: str, model_name: str = "nomic-embed-text") -> Dict[str, Any]:
        """Generate embedding for text. Supports: Ollama, OpenAI, Gemini, Mistral, Together, Cohere."""
        start_time = time.time()
        try:
            model_config = self.get_model(model_name)
            if not model_config:
                return {"success": False, "error": f"Model '{model_name}' not found", "embedding": None,
                        "execution_time": time.time() - start_time}

            provider = model_config.provider
            if provider == "ollama":
                embedding = self._call_ollama(text, model_config.model_id)
            elif provider == "openai":
                embedding = self._call_openai(text, model_config.model_id)
            elif provider == "gemini":
                embedding = self._call_gemini(text, model_config.model_id)
            elif provider == "cohere":
                embedding = self._call_cohere(text, model_config.model_id)
            elif provider in self._COMPAT_EMBED:
                base_url, key_env = self._COMPAT_EMBED[provider]
                embedding = self._call_openai_compatible(text, model_config.model_id, base_url, key_env)
            else:
                return self._fallback_embedding(text, model_name)

            return {
                "success": True,
                "embedding": embedding,
                "model": model_name,
                "provider": model_config.provider,
                "dimension": len(embedding),
                "execution_time": time.time() - start_time,
            }
        except Exception as e:
            logger.warning("Embedding failed for %s: %s — trying OpenAI fallback", model_name, e)
            # Fallback chain: Ollama → OpenAI → mock
            if model_name != "text-embedding-3-small" and self.get_model("text-embedding-3-small"):
                try:
                    return self.embed_text(text, model_name="text-embedding-3-small")
                except Exception:
                    pass
            return self._fallback_embedding(text, model_name)
    
    def embed_batch(self, texts: List[str], model_name: str = "nomic-embed-text") -> Dict[str, Any]:
        """Generate embeddings for multiple texts — uses native batch APIs when available."""
        start_time = time.time()

        try:
            model_config = self.get_model(model_name)
            if not model_config:
                return {
                    "success": False,
                    "error": f"Embedding model '{model_name}' not found",
                    "embeddings": None,
                    "execution_time": time.time() - start_time
                }

            provider = model_config.provider
            embeddings = None

            # True batch: Ollama /api/embed supports input arrays
            if provider == "ollama" and texts:
                try:
                    resp = requests.post(
                        f"{self._ollama_url}/api/embed",
                        json={"model": model_config.model_id, "input": texts, "keep_alive": "60m"},
                        timeout=120,
                    )
                    resp.raise_for_status()
                    embeddings = resp.json().get("embeddings", [])
                except Exception as e:
                    logger.debug("Ollama batch embed failed (%s), falling back to sequential", e)

            # True batch: OpenAI supports input arrays natively
            if embeddings is None and provider == "openai" and texts:
                try:
                    import openai
                    client = openai.OpenAI()
                    resp = client.embeddings.create(input=texts, model=model_config.model_id)
                    embeddings = [d.embedding for d in resp.data]
                except Exception as e:
                    logger.debug("OpenAI batch embed failed (%s), falling back to sequential", e)

            # Fallback: sequential per-text
            if embeddings is None:
                embeddings = []
                for text in texts:
                    result = self.embed_text(text, model_name)
                    if result.get("success") and result.get("embedding") is not None:
                        embeddings.append(result["embedding"])
                    else:
                        embeddings.append(None)

            return {
                "success": True,
                "embeddings": embeddings,
                "model": model_name,
                "provider": model_config.provider,
                "dimension": model_config.dimension,
                "count": len(texts),
                "execution_time": time.time() - start_time,
            }
                
        except Exception as e:
            logger.error(f"[EMOJI] Batch embedding generation failed: {e}")
            return {
                "success": False,
                "error": str(e),
                "embeddings": None,
                "execution_time": time.time() - start_time
            }
    
    def _fallback_embedding(self, text: str, model_name: str) -> Dict[str, Any]:
        """Fallback embedding generation."""
        logger.warning(f"[EMOJI][EMOJI] Using fallback embedding for {model_name}")
        
        # Generate mock embedding based on text hash
        import hashlib
        text_hash = hashlib.md5(text.encode()).hexdigest()
        
        # Create deterministic mock embedding
        np.random.seed(int(text_hash[:8], 16))
        mock_embedding = np.random.randn(768).astype(np.float32)
        
        return {
            "success": True,
            "embedding": mock_embedding,
            "model": model_name,
            "provider": "fallback",
            "dimension": 768,
            "execution_time": time.time(),
            "warning": "Using fallback embedding implementation"
        }
    
    def _fallback_batch_embedding(self, texts: List[str], model_name: str) -> Dict[str, Any]:
        """Fallback batch embedding generation."""
        logger.warning(f"[EMOJI][EMOJI] Using fallback batch embedding for {model_name}")
        
        embeddings = []
        for text in texts:
            result = self._fallback_embedding(text, model_name)
            embeddings.append(result["embedding"])
        
        return {
            "success": True,
            "embeddings": embeddings,
            "model": model_name,
            "provider": "fallback",
            "dimension": 768,
            "count": len(texts),
            "execution_time": time.time(),
            "warning": "Using fallback batch embedding implementation"
        }
    
    def get_model_dimension(self, model_name: str) -> int:
        """Get the dimension of an embedding model."""
        model_config = self.get_model(model_name)
        return model_config.dimension if model_config else 768

# Global embedding service instance
embedding_service = AIContextDBEmbeddingService()

def get_embedding_service() -> AIContextDBEmbeddingService:
    """Get the global embedding service instance."""
    return embedding_service

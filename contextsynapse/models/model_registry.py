"""
AIContextDB Model Registry - Self-contained implementation
Provides unified model management with multi-provider support.
"""

import os
import logging
from typing import Dict, List, Any, Optional, Union
from dataclasses import dataclass
from enum import Enum
import time
import json

# Load environment variables from .env file
try:
    from ..utils.env_loader import load_env_file, initialize_env_loader
    initialize_env_loader()
except ImportError:
    # Fallback: just try to load manually
    pass

# Import AIContextDB's self-contained LLM system
try:
    from ..llm.universal_llm import AIContextDBUniversalLLM, LLMProvider, LLMConfig
    AICONTEXTDB_LLM_AVAILABLE = True
except ImportError:
    AICONTEXTDB_LLM_AVAILABLE = False
    AIContextDBUniversalLLM = None
    LLMProvider = None
    LLMConfig = None

logger = logging.getLogger(__name__)

@dataclass
class ModelConfig:
    """Configuration for a specific model."""
    name: str
    provider: str
    model_id: str
    api_key_env: str
    default_params: Dict[str, Any] = None
    capabilities: List[str] = None
    
    def __post_init__(self):
        if self.default_params is None:
            self.default_params = {}
        if self.capabilities is None:
            self.capabilities = []

class AIContextDBModelRegistry:
    """AIContextDB Model Registry with self-contained LLM integration."""
    
    def __init__(self):
        self.models: Dict[str, ModelConfig] = {}
        self.llm = None
        
        if AICONTEXTDB_LLM_AVAILABLE:
            self.llm = AIContextDBUniversalLLM()
            self._register_default_models()
            logger.info("[EMOJI] AIContextDB Model Registry initialized with self-contained Universal LLM")
        else:
            logger.warning("[EMOJI][EMOJI] AIContextDB Universal LLM not available, using fallback")
    
    def _register_default_models(self):
        """Register default models from configuration."""
        default_models = {
            "gpt-4": ModelConfig(
                name="gpt-4",
                provider="openai",
                model_id="gpt-4",
                api_key_env="OPENAI_API_KEY",
                default_params={"temperature": 0.7, "max_tokens": 1000},
                capabilities=["text_generation", "reasoning"]
            ),
            "gpt-3.5-turbo": ModelConfig(
                name="gpt-3.5-turbo",
                provider="openai", 
                model_id="gpt-3.5-turbo",
                api_key_env="OPENAI_API_KEY",
                default_params={"temperature": 0.7, "max_tokens": 1000},
                capabilities=["text_generation", "reasoning"]
            ),
            "text-embedding-ada-002": ModelConfig(
                name="text-embedding-ada-002",
                provider="openai",
                model_id="text-embedding-ada-002", 
                api_key_env="OPENAI_API_KEY",
                capabilities=["embedding"]
            ),
            "claude-3": ModelConfig(
                name="claude-3",
                provider="anthropic",
                model_id="claude-3-sonnet-20240229",
                api_key_env="ANTHROPIC_API_KEY",
                default_params={"temperature": 0.7, "max_tokens": 1000},
                capabilities=["text_generation", "reasoning"]
            )
        }
        
        for model_name, config in default_models.items():
            self.register_model(model_name, config)
    
    def register_model(self, name: str, config: ModelConfig):
        """Register a model with its configuration (lazy - only checks API key when model is actually used)."""
        self.models[name] = config
        
        # Only register OpenAI models by default (others are lazy-loaded when needed)
        # Register with AIContextDB Universal LLM if available
        if self.llm and config.provider in ["openai"]:
            try:
                # Try to load from .env file if not already in environment
                try:
                    from ..utils.env_loader import get_env_var
                    api_key = get_env_var(config.api_key_env)
                except ImportError:
                    api_key = os.getenv(config.api_key_env)
                
                if api_key:
                    # API key is already set in universal_llm during initialization
                    logger.info(f"[EMOJI] Registered {name} with {config.provider}")
                else:
                    logger.warning(f"[EMOJI][EMOJI] No API key found for {name} ({config.api_key_env})")
            except Exception as e:
                logger.error(f"[EMOJI] Failed to register {name}: {e}")
        # For other providers (anthropic, google, cohere), we skip API key check
        # They will be checked only when the model is actually used
    
    def get_model(self, name: str) -> Optional[ModelConfig]:
        """Get model configuration."""
        return self.models.get(name)
    
    def list_models(self) -> List[str]:
        """List all available models."""
        return list(self.models.keys())
    
    def execute_model(self, model_name: str, prompt: str, **params) -> Dict[str, Any]:
        """Execute a model with given prompt and parameters."""
        start_time = time.time()
        
        try:
            model_config = self.get_model(model_name)
            if not model_config:
                return {
                    "success": False,
                    "error": f"Model '{model_name}' not found",
                    "response": None,
                    "execution_time": time.time() - start_time
                }
            
            # Merge default params with provided params
            final_params = {**model_config.default_params, **params}
            
            if self.llm and model_config.provider in ["openai", "anthropic"]:
                # Use AIContextDB Universal LLM
                try:
                    response = self.llm.generate(
                        model_name=model_config.model_id,
                        prompt=prompt,
                        **final_params
                    )
                    
                    # Extract the actual response text from llm.generate() result
                    # llm.generate() returns: {"success": True, "response": "text", ...}
                    response_text = response.get('response', '') if response.get('success') else None
                    
                    return {
                        "success": response.get('success', False),
                        "response": response_text,  # Extract just the text, not the entire dict
                        "model": model_name,
                        "provider": model_config.provider,
                        "execution_time": time.time() - start_time,
                        "params": final_params,
                        "error": response.get('error') if not response.get('success') else None
                    }
                except Exception as e:
                    logger.error(f"[EMOJI] AIContextDB LLM execution failed: {e}")
                    return self._fallback_execution(model_name, prompt, final_params)
            else:
                # Fallback execution
                return self._fallback_execution(model_name, prompt, final_params)
                
        except Exception as e:
            logger.error(f"[EMOJI] Model execution failed: {e}")
            return {
                "success": False,
                "error": str(e),
                "response": None,
                "execution_time": time.time() - start_time
            }
    
    def _fallback_execution(self, model_name: str, prompt: str, params: Dict[str, Any]) -> Dict[str, Any]:
        """Fallback execution when AIContextDB LLM is not available."""
        logger.warning(f"[EMOJI][EMOJI] Using fallback execution for {model_name}")
        
        # Simple fallback response
        fallback_response = f"Fallback response for '{model_name}': {prompt[:100]}..."
        
        return {
            "success": True,
            "response": fallback_response,
            "model": model_name,
            "provider": "fallback",
            "execution_time": time.time(),
            "params": params,
            "warning": "Using fallback implementation"
        }
    
    def get_model_capabilities(self, model_name: str) -> List[str]:
        """Get capabilities of a specific model."""
        model_config = self.get_model(model_name)
        return model_config.capabilities if model_config else []
    
    def is_embedding_model(self, model_name: str) -> bool:
        """Check if a model is an embedding model."""
        capabilities = self.get_model_capabilities(model_name)
        return "embedding" in capabilities
    
    def is_text_generation_model(self, model_name: str) -> bool:
        """Check if a model is a text generation model."""
        capabilities = self.get_model_capabilities(model_name)
        return "text_generation" in capabilities

# Global model registry instance
model_registry = AIContextDBModelRegistry()

def get_model_registry() -> AIContextDBModelRegistry:
    """Get the global model registry instance."""
    return model_registry

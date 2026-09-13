"""
Configuration Loader
Centralized configuration management with support for:
- .env file (primary)
- config.yaml file (fallback)
- Environment variables (highest priority)
"""

import os
import logging
from pathlib import Path
from typing import Dict, Optional, Any
import yaml

logger = logging.getLogger(__name__)

# Load environment variables first
try:
    from .env_loader import load_env_file, get_env_var, initialize_env_loader
    initialize_env_loader()
except ImportError:
    # Fallback if env_loader not available
    get_env_var = lambda key, default=None: os.getenv(key, default)


class ConfigLoader:
    """Centralized configuration loader with multiple sources."""
    
    def __init__(self, config_file: Optional[str] = None):
        """
        Initialize configuration loader.
        
        Args:
            config_file: Path to config.yaml file (defaults to config/config.yaml)
        """
        self.config_file = config_file
        self.config_data: Dict[str, Any] = {}
        
        # Try to load config.yaml
        self._load_config_file()
        
        # Ensure .env is loaded
        try:
            initialize_env_loader()
        except:
            pass
    
    def _load_config_file(self):
        """Load configuration from config.yaml file."""
        try:
            if self.config_file:
                config_path = Path(self.config_file)
            else:
                # Look for config.yaml in common locations
                project_root = Path(__file__).parent.parent.parent
                config_path = project_root / "config" / "core" / "application.yaml"
                
                # Also check legacy locations for backward compatibility
                if not config_path.exists():
                    config_path = project_root / "config" / "config.yaml"
                if not config_path.exists():
                    config_path = project_root / "config.yaml"
            
            if config_path.exists():
                with open(config_path, 'r', encoding='utf-8') as f:
                    self.config_data = yaml.safe_load(f) or {}
                logger.debug(f"[EMOJI] Loaded configuration from {config_path}")
            else:
                logger.debug(f"[EMOJI] Config file not found at {config_path}")
        except Exception as e:
            logger.warning(f"[EMOJI][EMOJI] Failed to load config file: {e}")
            self.config_data = {}
    
    def get_api_key(self, key_name: str, default: Optional[str] = None) -> Optional[str]:
        """
        Get API key from configuration sources.
        
        Priority order:
        1. Environment variables (highest priority)
        2. .env file
        3. config.yaml file (lowest priority)
        
        Args:
            key_name: API key name (e.g., 'OPENAI_API_KEY', 'openai_api_key')
            default: Default value if not found
            
        Returns:
            API key value or default
        """
        # Try environment variable first (highest priority)
        env_value = os.getenv(key_name)
        if env_value:
            return env_value
        
        # Try .env file
        try:
            env_file_value = get_env_var(key_name)
            if env_file_value:
                return env_file_value
        except:
            pass
        
        # Try config.yaml (lowercase key mapping)
        config_key = key_name.lower()
        
        # Check various paths in config.yaml
        if 'llm' in self.config_data:
            llm_config = self.config_data.get('llm', {})
            if config_key in llm_config:
                value = llm_config[config_key]
                if value and value.strip():  # Only return if not empty
                    return value
        
        # Check embeddings section
        if 'embeddings' in self.config_data:
            emb_config = self.config_data.get('embeddings', {})
            if 'watsonx' in emb_config:
                watsonx_config = emb_config.get('watsonx', {})
                if 'api_key' in watsonx_config and config_key == 'watsonx_api_key':
                    value = watsonx_config.get('api_key', '')
                    if value and value.strip():
                        return value
        
        # Direct config access
        if config_key in self.config_data:
            value = self.config_data[config_key]
            if value and value.strip():
                return value
        
        return default
    
    def get_config(self, key_path: str, default: Any = None) -> Any:
        """
        Get configuration value from config.yaml using dot notation.
        
        Args:
            key_path: Dot-separated path (e.g., 'llm.provider', 'graph.default_engine')
            default: Default value if not found
            
        Returns:
            Configuration value or default
        """
        keys = key_path.split('.')
        value = self.config_data
        
        for key in keys:
            if isinstance(value, dict):
                value = value.get(key)
            else:
                return default
            
            if value is None:
                return default
        
        return value
    
    def get_all_api_keys(self) -> Dict[str, Optional[str]]:
        """
        Get all API keys from configuration.
        
        Returns:
            Dictionary mapping API key names to values
        """
        api_keys = {
            'OPENAI_API_KEY': self.get_api_key('OPENAI_API_KEY'),
            'ANTHROPIC_API_KEY': self.get_api_key('ANTHROPIC_API_KEY'),
            'GOOGLE_API_KEY': self.get_api_key('GOOGLE_API_KEY'),
            'COHERE_API_KEY': self.get_api_key('COHERE_API_KEY'),
            'WATSONX_API_KEY': self.get_api_key('WATSONX_API_KEY'),
        }
        
        # Filter out None values
        return {k: v for k, v in api_keys.items() if v}


# Global config loader instance
_global_config_loader: Optional[ConfigLoader] = None

def get_config_loader() -> ConfigLoader:
    """Get or create global configuration loader instance."""
    global _global_config_loader
    if _global_config_loader is None:
        _global_config_loader = ConfigLoader()
    return _global_config_loader

def get_api_key(key_name: str, default: Optional[str] = None) -> Optional[str]:
    """
    Convenience function to get API key from global config loader.
    
    Args:
        key_name: API key name (e.g., 'OPENAI_API_KEY')
        default: Default value if not found
        
    Returns:
        API key value or default
    """
    return get_config_loader().get_api_key(key_name, default)


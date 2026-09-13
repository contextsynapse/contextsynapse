"""
Environment Variable Loader
Loads environment variables from .env file with fallback support.
"""

import os
import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

def load_env_file(env_path: Optional[str] = None) -> bool:
    """
    Load environment variables from .env file.
    
    Args:
        env_path: Path to .env file (defaults to .env in project root)
        
    Returns:
        True if .env file was found and loaded, False otherwise
    """
    # Try python-dotenv first (recommended approach)
    try:
        from dotenv import load_dotenv
        
        if env_path:
            env_file = Path(env_path)
        else:
            # Look for .env in project root (multiple possible locations)
            project_root = Path(__file__).parent.parent.parent
            env_file = project_root / ".env"
            
            # Also check current directory
            if not env_file.exists():
                env_file = Path(".env")
        
        if env_file.exists():
            load_dotenv(env_file)
            logger.info(f"[EMOJI] Loaded environment variables from {env_file}")
            return True
        else:
            logger.debug(f"[EMOJI] .env file not found at {env_file}")
            return False
            
    except ImportError:
        # Fallback: Manual parsing if python-dotenv not available
        logger.debug("[EMOJI] python-dotenv not available, using manual .env parsing")
        return _load_env_manual(env_path)
    
    except Exception as e:
        logger.warning(f"[EMOJI][EMOJI] Failed to load .env file with python-dotenv: {e}, trying manual parsing")
        return _load_env_manual(env_path)


def _load_env_manual(env_path: Optional[str] = None) -> bool:
    """
    Manually parse .env file and set environment variables.
    
    Args:
        env_path: Path to .env file (defaults to .env in project root)
        
    Returns:
        True if .env file was found and loaded, False otherwise
    """
    try:
        if env_path:
            env_file = Path(env_path)
        else:
            # Look for .env in project root
            project_root = Path(__file__).parent.parent.parent
            env_file = project_root / ".env"
            
            # Also check current directory
            if not env_file.exists():
                env_file = Path(".env")
        
        if not env_file.exists():
            logger.debug(f"[EMOJI] .env file not found at {env_file}")
            return False
        
        # Parse .env file manually
        loaded_count = 0
        with open(env_file, 'r', encoding='utf-8') as f:
            for line_num, line in enumerate(f, 1):
                line = line.strip()
                
                # Skip empty lines and comments
                if not line or line.startswith('#'):
                    continue
                
                # Parse KEY=VALUE format
                if '=' in line:
                    key, value = line.split('=', 1)
                    key = key.strip()
                    value = value.strip()
                    
                    # Remove quotes if present
                    if value.startswith('"') and value.endswith('"'):
                        value = value[1:-1]
                    elif value.startswith("'") and value.endswith("'"):
                        value = value[1:-1]
                    
                    # Only set if not already in environment
                    if key and value and key not in os.environ:
                        os.environ[key] = value
                        loaded_count += 1
                        logger.debug(f"  [EMOJI] Loaded {key} from .env")
        
        if loaded_count > 0:
            logger.info(f"[EMOJI] Loaded {loaded_count} environment variables from {env_file}")
            return True
        else:
            logger.debug(f"[EMOJI] No new environment variables loaded from {env_file}")
            return False
            
    except Exception as e:
        logger.warning(f"[EMOJI][EMOJI] Failed to manually load .env file: {e}")
        return False


def get_env_var(key: str, default: Optional[str] = None) -> Optional[str]:
    """
    Get environment variable, with automatic .env loading.
    
    Args:
        key: Environment variable name
        default: Default value if not found
        
    Returns:
        Environment variable value or default (NEVER hardcoded)
    """
    # Load .env file once if not already loaded
    if not hasattr(get_env_var, '_env_loaded'):
        load_env_file()
        get_env_var._env_loaded = True
    
    value = os.getenv(key, default)
    
    # Security check: warn if value looks like a placeholder
    if value and any(placeholder in value.lower() for placeholder in ['your-', 'replace', 'placeholder', 'example']):
        logger.warning(f"[EMOJI][EMOJI] Warning: API key for {key} appears to be a placeholder. Please set a real value in .env file or environment variables.")
    
    return value


# Initialize: Try to load .env file on module import
_load_env_initialized = False

def initialize_env_loader():
    """Initialize environment variable loader."""
    global _load_env_initialized
    if not _load_env_initialized:
        load_env_file()
        _load_env_initialized = True


"""
AIContextDB Search Package
Provides enhanced search capabilities with BM25, dense, and hybrid search.
"""

from .enhanced_search import AIContextDBEnhancedSearch, get_enhanced_search, enhanced_search

try:
    from .whoosh_search import WhooshSearchEngine, WhooshConfig
except ImportError:
    WhooshSearchEngine = None
    WhooshConfig = None

__all__ = [
    'AIContextDBEnhancedSearch',
    'get_enhanced_search',
    'enhanced_search',
    'WhooshSearchEngine',
    'WhooshConfig',
]

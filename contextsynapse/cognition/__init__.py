from .events import CognitionEvent, CognitionEventStream
from .read_tracker import ReadTracker
from .derivation import DerivationTracker
from .feedback import FeedbackProcessor
from .invalidation import InvalidationEngine
from .hallucination import HallucinationTracer

_stream_cache = {}


def get_cognition_stream(namespace: str = "default") -> CognitionEventStream:
    """Get or create a CognitionEventStream for a namespace."""
    if namespace not in _stream_cache:
        redis_client = None
        try:
            import os, redis
            url = os.environ.get("CONTEXTSYNAPSE_REDIS_URL") or os.environ.get("AICONTEXTDB_REDIS_URL")
            if url:
                redis_client = redis.from_url(url)
        except Exception:
            pass
        _stream_cache[namespace] = CognitionEventStream(namespace=namespace, redis_client=redis_client)
    return _stream_cache[namespace]


__all__ = [
    "CognitionEvent", "CognitionEventStream",
    "ReadTracker", "DerivationTracker", "FeedbackProcessor",
    "InvalidationEngine", "HallucinationTracer",
    "get_cognition_stream",
]

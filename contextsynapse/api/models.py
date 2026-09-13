"""
Shared API Models
=================
Pydantic models shared across routers.
"""

from typing import Any, Generic, List, Optional, TypeVar
from pydantic import BaseModel, Field

T = TypeVar("T")


class PaginationParams(BaseModel):
    """Standard pagination query parameters."""
    limit: int = Field(default=50, ge=1, le=500, description="Max items per page")
    offset: int = Field(default=0, ge=0, description="Number of items to skip")


def paginate(items: list, limit: int = 50, offset: int = 0) -> dict:
    """Apply pagination to a list and return the standard envelope.

    Returns::

        {
            "items": [...],
            "total": 123,
            "limit": 50,
            "offset": 0
        }
    """
    total = len(items)
    sliced = items[offset : offset + limit]
    return {
        "items": sliced,
        "total": total,
        "limit": limit,
        "offset": offset,
    }

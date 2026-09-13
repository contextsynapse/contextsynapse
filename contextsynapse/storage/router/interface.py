"""ContentStore interface -- the contract every storage backend implements.

All content stores (DuckDB, PostgreSQL, Parquet) implement this ABC.
The StorageRouter and ContentResolver talk to this interface, never to
a specific backend directly.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional


class ContentStoreInterface(ABC):
    """Abstract interface for structured content storage.

    Every backend must implement these methods. The StorageRouter
    routes data here, and the ContentResolver reads from here.

    Tables/collections:
        documents      -- document metadata (title, url, author, date)
        passages       -- passage text + position
        facts          -- extracted statements with confidence
        prices         -- time-series OHLCV
        entity_details -- extended entity info (description, aliases)
    """

    # ── Documents ──

    @abstractmethod
    def store_document(self, doc: Dict[str, Any], namespace: str = "default") -> str:
        """Store document metadata. Returns doc id."""

    @abstractmethod
    def get_document(self, doc_id: str) -> Optional[Dict[str, Any]]:
        """Get document by ID."""

    def get_documents_by_namespace(self, namespace: str) -> List[Dict[str, Any]]:
        """Get all documents in a namespace. Override for efficiency."""
        return []

    # ── Passages ──

    @abstractmethod
    def store_passages(self, passages: List[Dict[str, Any]], namespace: str = "default") -> None:
        """Batch store passages."""

    @abstractmethod
    def get_passage(self, passage_id: str) -> Optional[Dict[str, Any]]:
        """Get passage by ID (full record)."""

    @abstractmethod
    def get_passage_text(self, passage_id: str) -> str:
        """Get passage text only (fast path)."""

    @abstractmethod
    def get_passage_texts_batch(self, passage_ids: List[str]) -> Dict[str, str]:
        """Batch text lookup. Returns {id: text}."""

    @abstractmethod
    def get_passages_by_doc(self, doc_id: str) -> List[Dict[str, Any]]:
        """Get all passages for a document, ordered by position."""

    # ── Facts ──

    @abstractmethod
    def store_facts(self, facts: List[Dict[str, Any]], namespace: str = "default") -> None:
        """Batch store facts."""

    @abstractmethod
    def get_fact(self, fact_id: str) -> Optional[Dict[str, Any]]:
        """Get fact by ID."""

    @abstractmethod
    def get_facts_by_entity(self, entity_id: str) -> List[Dict[str, Any]]:
        """Get all facts mentioning an entity."""

    @abstractmethod
    def get_facts_by_passage(self, passage_id: str) -> List[Dict[str, Any]]:
        """Get all facts extracted from a passage."""

    # ── Prices / Time-series ──

    @abstractmethod
    def store_prices(self, prices: List[Dict[str, Any]], namespace: str = "default") -> None:
        """Batch store price data."""

    @abstractmethod
    def get_prices(self, ticker: str, days: int = 30) -> List[Dict[str, Any]]:
        """Get latest N days of prices for a ticker."""

    @abstractmethod
    def get_prices_range(self, ticker: str, start_date: str, end_date: str) -> List[Dict[str, Any]]:
        """Get prices in a date range."""

    @abstractmethod
    def get_price_latest(self, ticker: str) -> Optional[Dict[str, Any]]:
        """Get most recent price for a ticker."""

    # ── Entity details ──

    @abstractmethod
    def store_entity_details(self, entity: Dict[str, Any], namespace: str = "default") -> None:
        """Store extended entity info."""

    @abstractmethod
    def get_entity_details(self, entity_id: str) -> Optional[Dict[str, Any]]:
        """Get entity details by ID."""

    # ── Query ──

    def query(self, sql: str, params: list = None) -> List[Dict[str, Any]]:
        """Run arbitrary SQL/query. Not all backends support this."""
        raise NotImplementedError(f"{self.__class__.__name__} does not support raw queries")

    # ── Stats ──

    @abstractmethod
    def stats(self) -> Dict[str, int]:
        """Return row counts for all tables."""

    # ── Lifecycle ──

    def close(self) -> None:
        """Close connections. Called on shutdown."""
        pass

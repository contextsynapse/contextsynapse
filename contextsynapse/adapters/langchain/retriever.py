"""
LangChain Retriever backed by AIContextDB graph data.

Converts graph nodes into LangChain Documents for use in RAG chains.

Usage:
    from contextsynapse.adapters.langchain import AIContextDBRetriever

    retriever = AIContextDBRetriever(namespace="knowledge")
    docs = retriever.invoke("Alice")
    # Or in a chain:
    chain = retriever | llm | output_parser
"""

import json
import logging
from typing import Any, Dict, List, Optional

from langchain_core.callbacks import CallbackManagerForRetrieverRun
from langchain_core.documents import Document
from langchain_core.retrievers import BaseRetriever
from pydantic import Field

from .._base import AIContextDBConnection

logger = logging.getLogger(__name__)


class AIContextDBRetriever(BaseRetriever):
    """
    Retrieve LangChain Documents from AIContextDB graph nodes.

    Supports:
    - Text search across node properties
    - Label filtering
    - AIQL query-based retrieval
    - Optional daemon context merging
    """

    connection: Any = Field(default=None, exclude=True)
    namespace: str = "default"
    node_labels: Optional[List[str]] = None
    max_results: int = 20
    search_properties: List[str] = Field(default_factory=lambda: ["name", "content", "description", "title", "text"])
    include_daemon_context: bool = False

    def model_post_init(self, __context: Any) -> None:
        if self.connection is None:
            self.connection = AIContextDBConnection(namespace=self.namespace)

    def _get_relevant_documents(
        self,
        query: str,
        *,
        run_manager: Optional[CallbackManagerForRetrieverRun] = None,
    ) -> List[Document]:
        """Retrieve documents matching the query."""
        nodes = self.connection.get_nodes()
        query_lower = query.lower()

        scored: List[tuple] = []
        for node in nodes:
            # Filter by label if specified
            if self.node_labels and node.label not in self.node_labels:
                continue

            # Score by matching against searchable properties
            score = self._score_node(node, query_lower)
            if score > 0:
                scored.append((score, node))

        # Sort by score descending, take top results
        scored.sort(key=lambda x: x[0], reverse=True)
        top_nodes = [node for _, node in scored[:self.max_results]]

        # If no matches found, return all nodes (up to limit) as fallback
        if not top_nodes:
            if self.node_labels:
                top_nodes = [n for n in nodes if n.label in self.node_labels][:self.max_results]
            else:
                top_nodes = nodes[:self.max_results]

        documents = [self._node_to_document(node) for node in top_nodes]

        # Optionally append daemon context as a document
        if self.include_daemon_context and self.connection.daemon_url:
            daemon_doc = self._get_daemon_document()
            if daemon_doc:
                documents.append(daemon_doc)

        return documents

    def _score_node(self, node, query_lower: str) -> float:
        """Score a node against a search query. Higher = better match."""
        score = 0.0
        props = node.properties or {}

        for prop_name in self.search_properties:
            val = props.get(prop_name)
            if isinstance(val, str):
                val_lower = val.lower()
                if query_lower == val_lower:
                    score += 10.0  # exact match
                elif query_lower in val_lower:
                    score += 5.0   # substring match
                else:
                    # Word-level overlap
                    query_words = set(query_lower.split())
                    val_words = set(val_lower.split())
                    overlap = query_words & val_words
                    if overlap:
                        score += len(overlap) * 2.0

        # Bonus for label match
        if query_lower in node.label.lower():
            score += 3.0

        return score

    @staticmethod
    def _node_to_document(node) -> Document:
        """Convert a GraphNode to a LangChain Document."""
        props = node.properties or {}

        # Build page_content from meaningful properties
        content_parts = [f"[{node.label}]"]
        for key in ["name", "title", "content", "description", "text"]:
            val = props.get(key)
            if val:
                content_parts.append(f"{key}: {val}")

        # Add remaining properties
        for key, val in props.items():
            if key not in ("name", "title", "content", "description", "text", "domain"):
                content_parts.append(f"{key}: {val}")

        return Document(
            page_content="\n".join(content_parts),
            metadata={
                "node_id": node.id,
                "label": node.label,
                **{k: v for k, v in props.items() if isinstance(v, (str, int, float, bool))},
            },
        )

    def _get_daemon_document(self) -> Optional[Document]:
        """Fetch daemon context as a single document."""
        try:
            hub = self.connection.build_context(include_daemon=True)
            prompt = hub.to_prompt()
            if prompt.strip():
                return Document(
                    page_content=prompt,
                    metadata={"source": "daemon", "label": "DaemonContext"},
                )
        except Exception as exc:
            logger.debug(f"Could not fetch daemon context: {exc}")
        return None

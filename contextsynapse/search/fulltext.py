"""Full-text search module using Whoosh."""

import json
import os
import shutil
from pathlib import Path

from whoosh import index
from whoosh.fields import ID, TEXT, STORED, Schema
from whoosh.qparser import MultifieldParser
from whoosh import highlight


class FullTextIndex:
    """Full-text search index for graph nodes backed by Whoosh."""

    def __init__(self, index_dir: str = "contextcore_data/fulltext_index"):
        self.index_dir = Path(index_dir)
        self.schema = Schema(
            node_id=ID(stored=True, unique=True),
            label=TEXT(stored=True),
            content=TEXT(stored=True),
            properties_json=STORED,
        )
        self._ix = self._open_or_create()

    def _open_or_create(self) -> index.FileIndex:
        """Open existing index or create a new one."""
        if self.index_dir.exists() and index.exists_in(str(self.index_dir)):
            return index.open_dir(str(self.index_dir))
        self.index_dir.mkdir(parents=True, exist_ok=True)
        return index.create_in(str(self.index_dir), self.schema)

    @staticmethod
    def _extract_content(properties: dict) -> str:
        """Concatenate all string-valued properties into a single text blob."""
        parts = []
        for value in properties.values():
            if isinstance(value, str):
                parts.append(value)
        return " ".join(parts)

    def index_node(self, node_id: str, label: str, properties: dict) -> None:
        """Index a single node's text properties.

        If the node already exists in the index it is updated in-place.
        """
        content = self._extract_content(properties)
        props_json = json.dumps(properties, default=str)

        writer = self._ix.writer()
        writer.update_document(
            node_id=node_id,
            label=label,
            content=content,
            properties_json=props_json,
        )
        writer.commit()

    def index_nodes(self, nodes: list) -> None:
        """Batch-index a list of nodes.

        Each element should be a dict with keys ``node_id``, ``label``, and
        ``properties``.
        """
        writer = self._ix.writer()
        for node in nodes:
            node_id = str(node.get("node_id", ""))
            label = str(node.get("label", ""))
            properties = node.get("properties", {})
            content = self._extract_content(properties)
            props_json = json.dumps(properties, default=str)
            writer.update_document(
                node_id=node_id,
                label=label,
                content=content,
                properties_json=props_json,
            )
        writer.commit()

    def search(self, query_str: str, limit: int = 20) -> list[dict]:
        """Run a full-text search and return matching nodes.

        Returns a list of dicts, each containing:
        - ``node_id``
        - ``label``
        - ``score``
        - ``highlights`` (highlighted snippet from *content*)
        """
        parser = MultifieldParser(["content", "label"], schema=self.schema)
        query = parser.parse(query_str)

        results: list[dict] = []
        with self._ix.searcher() as searcher:
            hits = searcher.search(query, limit=limit)
            hits.fragmenter = highlight.ContextFragmenter(maxchars=200, surround=40)
            hits.formatter = highlight.UppercaseFormatter()

            for hit in hits:
                results.append(
                    {
                        "node_id": hit["node_id"],
                        "label": hit["label"],
                        "score": hit.score,
                        "highlights": hit.highlights("content"),
                    }
                )
        return results

    def remove_node(self, node_id: str) -> None:
        """Remove a node from the index by its id."""
        writer = self._ix.writer()
        writer.delete_by_term("node_id", node_id)
        writer.commit()

    def rebuild(self, nodes: list) -> None:
        """Clear the index and re-index all provided nodes."""
        self.clear()
        self.index_nodes(nodes)

    def clear(self) -> None:
        """Wipe the entire index and recreate an empty one."""
        if self.index_dir.exists():
            shutil.rmtree(self.index_dir)
        self.index_dir.mkdir(parents=True, exist_ok=True)
        self._ix = index.create_in(str(self.index_dir), self.schema)


# Global singleton
fulltext_index = FullTextIndex()

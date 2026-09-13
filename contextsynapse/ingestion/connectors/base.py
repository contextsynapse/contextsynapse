"""
ConnectorIngestor — Base class for pulling external system metadata + data into the graph.

Two-phase protocol:
  Phase 1: discover_metadata() → structural graph nodes (Table, Column, Issue, etc.)
  Phase 2: pull_data() → content items fed into the extraction pipeline
"""

from __future__ import annotations

import uuid
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


@dataclass
class ConnectorGraphResult:
    """Result of a connector ingestion phase."""
    nodes: List[Dict[str, Any]] = field(default_factory=list)
    edges: List[Dict[str, Any]] = field(default_factory=list)
    data_items: List[Dict[str, Any]] = field(default_factory=list)  # Phase 2: text content for pipeline
    stats: Dict[str, Any] = field(default_factory=dict)


class ConnectorIngestor(ABC):
    """
    Base class for connector-to-graph ingestion.

    Subclasses implement discover_metadata() for structural schema discovery
    and pull_data() for content extraction.
    """

    connector_type: str = ""

    def __init__(self, config: Dict[str, Any], credentials: Dict[str, Any]):
        self.config = config
        self.credentials = credentials

    @abstractmethod
    def test_connection(self) -> Dict[str, Any]:
        """Test connectivity. Returns {success: bool, message: str}."""
        ...

    @abstractmethod
    def discover_metadata(self, context_id: str) -> ConnectorGraphResult:
        """Phase 1: Discover structural metadata and return graph nodes/edges."""
        ...

    @abstractmethod
    def pull_data(self, context_id: str, options: Optional[Dict[str, Any]] = None) -> ConnectorGraphResult:
        """Phase 2: Pull content for extraction pipeline. Returns data_items."""
        ...

    # ------------------------------------------------------------------
    # Helper methods for building graph nodes
    # ------------------------------------------------------------------

    def _uid(self) -> str:
        return str(uuid.uuid4())

    def _make_connector_node(
        self, context_id: str, *, name: Optional[str] = None,
        extra_props: Optional[Dict[str, Any]] = None,
    ) -> Tuple[Dict, Dict]:
        """Create root Connector node + HAS_CONNECTOR edge from Context."""
        node_id = self._uid()
        props = {
            "name": name or f"{self.connector_type} connector",
            "connector_type": self.connector_type,
            "connected_at": datetime.now(timezone.utc).isoformat(),
        }
        if extra_props:
            props.update(extra_props)

        node = {"id": node_id, "label": "Connector", "properties": props}
        edge = {
            "id": self._uid(),
            "source": context_id,
            "target": node_id,
            "label": "HAS_CONNECTOR",
            "properties": {"connector_type": self.connector_type},
        }
        return node, edge

    def _make_sync_run_node(
        self, connector_node_id: str, status: str,
        nodes_created: int, edges_created: int, duration_ms: int,
        error: Optional[str] = None,
    ) -> Tuple[Dict, Dict]:
        """Create a SyncRun node linked to the Connector."""
        node_id = self._uid()
        props = {
            "name": f"Sync {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')}",
            "status": status,
            "nodes_created": nodes_created,
            "edges_created": edges_created,
            "duration_ms": duration_ms,
            "synced_at": datetime.now(timezone.utc).isoformat(),
        }
        if error:
            props["error"] = error[:500]

        node = {"id": node_id, "label": "SyncRun", "properties": props}
        edge = {
            "id": self._uid(),
            "source": connector_node_id,
            "target": node_id,
            "label": "HAS_SYNC_RUN",
            "properties": {},
        }
        return node, edge

    # ------------------------------------------------------------------
    # Phase 2 helpers: smart text formatting for extraction pipeline
    # ------------------------------------------------------------------

    # Fields to skip when formatting records as text
    _SYSTEM_FIELD_PREFIXES = ("_", "sys_", "link", "href")
    _SYSTEM_FIELDS = {
        "sys_id", "sys_created_on", "sys_updated_on", "sys_mod_count",
        "sys_class_name", "sys_domain", "sys_tags", "sys_scope",
        "self", "expand", "fields", "transitions", "editmeta",
    }

    @staticmethod
    def _format_record_as_text(
        record: Dict[str, Any],
        skip_keys: Optional[set] = None,
        key_fields: Optional[List[str]] = None,
    ) -> str:
        """Format a JSON record as readable text for LLM extraction.

        Prioritizes key_fields first, then appends remaining fields.
        Skips internal/system fields automatically.
        """
        skip = (skip_keys or set()) | ConnectorIngestor._SYSTEM_FIELDS
        parts = []

        # Key fields first (headline)
        if key_fields:
            headline = []
            for k in key_fields:
                v = record.get(k)
                if v is not None and str(v).strip():
                    headline.append(f"{k}: {v}")
            if headline:
                parts.append(" | ".join(headline))

        # Remaining fields
        details = []
        seen = set(key_fields or [])
        for k, v in record.items():
            if k in seen:
                continue
            if k.lower() in skip:
                continue
            if any(k.lower().startswith(p) for p in ConnectorIngestor._SYSTEM_FIELD_PREFIXES):
                continue
            if v is None or (isinstance(v, str) and not v.strip()):
                continue
            if isinstance(v, (dict, list)):
                v = str(v)[:200]
            details.append(f"{k}: {v}")
            seen.add(k)

        if details:
            parts.append(". ".join(details))

        return "\n".join(parts)

    @staticmethod
    def _table_stats_text(
        table_name: str,
        columns: List[Dict[str, Any]],
        row_count: int,
        sample_rows: List[Dict[str, Any]],
        column_stats: Optional[Dict[str, Dict]] = None,
    ) -> str:
        """Generate a natural language summary of a database table.

        Returns text optimized for LLM entity/fact extraction.
        """
        lines = [f"Database table '{table_name}' contains {row_count:,} rows.\n"]

        # Column descriptions
        col_descs = []
        for col in columns:
            name = col.get("name", "")
            dtype = col.get("data_type", "unknown")
            nullable = "nullable" if col.get("nullable") else "required"
            primary = " (primary key)" if col.get("is_primary") else ""
            desc = f"  - {name} ({dtype}, {nullable}{primary})"

            # Add stats if available
            if column_stats and name in column_stats:
                stats = column_stats[name]
                stat_parts = []
                if "distinct" in stats:
                    stat_parts.append(f"{stats['distinct']} distinct values")
                if "min" in stats and "max" in stats:
                    stat_parts.append(f"range: {stats['min']} to {stats['max']}")
                if "null_count" in stats and stats["null_count"] > 0:
                    stat_parts.append(f"{stats['null_count']} nulls")
                if stat_parts:
                    desc += f" — {', '.join(stat_parts)}"
            col_descs.append(desc)

        if col_descs:
            lines.append(f"Columns ({len(col_descs)}):")
            lines.extend(col_descs)

        # Sample data
        if sample_rows:
            lines.append(f"\nSample data ({len(sample_rows)} rows):")
            for i, row in enumerate(sample_rows[:10]):
                row_text = " | ".join(f"{k}={v}" for k, v in row.items() if v is not None)
                lines.append(f"  Row {i+1}: {row_text}")

        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Structured data helpers: direct record → typed node mapping
    # ------------------------------------------------------------------

    @staticmethod
    def _table_name_to_label(table_name: str) -> str:
        """Convert a table/collection name to a PascalCase node label.

        'order_items' → 'OrderItem', 'customers' → 'Customer',
        'user_sessions' → 'UserSession'
        """
        parts = table_name.replace("-", "_").split("_")
        result = []
        for p in parts:
            if not p:
                continue
            # Simple singularize: strip trailing 's' (unless too short or ends in 'ss')
            word = p.capitalize()
            if len(word) > 3 and word.endswith("s") and not word.endswith("ss"):
                word = word[:-1]
            result.append(word)
        return "".join(result) or "Record"

    def _records_to_nodes(
        self,
        records: List[Dict[str, Any]],
        label: str,
        key_field: Optional[str] = None,
        source_node_id: Optional[str] = None,
    ) -> ConnectorGraphResult:
        """Convert a list of records directly to typed graph nodes.

        Args:
            records: List of dicts (rows/records)
            label: Node label (e.g., 'Customer', 'Order')
            key_field: Field to use as deterministic node ID (e.g., 'id')
            source_node_id: Parent node to link via HAS_RECORD edge

        Returns:
            ConnectorGraphResult with nodes and edges
        """
        import hashlib
        result = ConnectorGraphResult()

        for record in records:
            # Deterministic ID from key field if available
            if key_field and key_field in record:
                key_val = str(record[key_field])
                node_id = f"{label.lower()}_{hashlib.sha256(key_val.encode()).hexdigest()[:12]}"
            else:
                node_id = self._uid()

            # Clean properties: skip None, convert complex types
            props = {"name": str(record.get(key_field, record.get("name", node_id[:20])))}
            for k, v in record.items():
                if v is None:
                    continue
                if isinstance(v, (dict, list)):
                    props[k] = str(v)[:500]
                else:
                    props[k] = v
            props["_source_connector"] = self.connector_type

            result.nodes.append(self._node(label, props, node_id=node_id))

            if source_node_id:
                result.edges.append(self._edge(source_node_id, node_id, "HAS_RECORD"))

        return result

    def _node(self, label: str, properties: Dict[str, Any], node_id: Optional[str] = None) -> Dict:
        return {"id": node_id or self._uid(), "label": label, "properties": properties}

    def _edge(self, source: str, target: str, label: str, properties: Optional[Dict[str, Any]] = None) -> Dict:
        return {"id": self._uid(), "source": source, "target": target, "label": label, "properties": properties or {}}

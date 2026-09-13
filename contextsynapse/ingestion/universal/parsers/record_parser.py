"""RecordParser for structured data (CSV, JSON, JSONL).

Parses structured input into uniform ``(RecordMeta, List[Chunk])`` pairs,
mirroring the TurnParser pattern for conversations.
"""
from __future__ import annotations

import csv
import io
import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Tuple, Union

from contextsynapse.ingestion.universal.ingest_content import Chunk

# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class RecordMeta:
    """Metadata about the parsed record set."""
    record_count: int = 0
    record_type: str = ""
    format: str = ""
    fields: List[str] = field(default_factory=list)
    source: str = ""


# ---------------------------------------------------------------------------
# Type hint map for auto-detection
# ---------------------------------------------------------------------------

_TYPE_HINTS: Dict[str, List[str]] = {
    "Issue": ["status", "priority", "assignee", "severity"],
    "Person": ["first_name", "last_name", "email", "phone"],
    "Product": ["price", "sku", "category", "inventory"],
    "Event": ["start_date", "end_date", "location", "attendees"],
    "Task": ["due_date", "assignee", "status", "priority"],
}

# Fields that should appear first in text representation
_PRIORITY_FIELDS = {"name", "title", "label", "id"}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _detect_record_type(fields: List[str]) -> str:
    """Guess a record type from field names using _TYPE_HINTS."""
    if not fields:
        return ""
    field_set = {f.lower() for f in fields}
    best_type = ""
    best_score = 0
    for rtype, hint_fields in _TYPE_HINTS.items():
        score = sum(1 for h in hint_fields if h.lower() in field_set)
        if score > best_score:
            best_score = score
            best_type = rtype
    return best_type if best_score >= 2 else ""


def _record_to_text(record: Dict[str, Any]) -> str:
    """Convert a dict record to a readable text string.

    Puts name/title/label/id fields first for readability.
    """
    if not record:
        return ""
    priority = []
    rest = []
    for key, value in record.items():
        entry = f"{key}: {value}"
        if key.lower() in _PRIORITY_FIELDS:
            priority.append(entry)
        else:
            rest.append(entry)
    return "; ".join(priority + rest)


# ---------------------------------------------------------------------------
# RecordParser
# ---------------------------------------------------------------------------


class RecordParser:
    """Parse CSV, JSON array, JSONL, or dict lists into Chunk sequences."""

    def parse(
        self,
        input_data: Union[str, List[Dict[str, Any]]],
        record_type: str = "",
        source: str = "",
    ) -> Tuple[RecordMeta, List[Chunk]]:
        """Parse *input_data* and return ``(meta, chunks)``.

        Raises ``ValueError`` when the format cannot be determined.
        """
        if isinstance(input_data, list):
            records = input_data
            fmt = "dict_list"
        elif isinstance(input_data, str):
            records, fmt = self._parse_string(input_data)
        else:
            raise ValueError(f"Unsupported input type: {type(input_data).__name__}")

        # Determine fields from first record
        fields: List[str] = list(records[0].keys()) if records else []

        # Auto-detect record type if not provided
        if not record_type and fields:
            record_type = _detect_record_type(fields)

        meta = RecordMeta(
            record_count=len(records),
            record_type=record_type,
            format=fmt,
            fields=fields,
            source=source,
        )

        chunks = self._to_chunks(records, record_type, source)
        return meta, chunks

    # -- string format detection ------------------------------------------------

    def _parse_string(self, text: str) -> Tuple[List[Dict[str, Any]], str]:
        """Detect format and parse string into list of dicts."""
        stripped = text.strip()
        if not stripped:
            raise ValueError("Empty input string")

        # Try JSON array
        if stripped.startswith("["):
            try:
                parsed = json.loads(stripped)
                if isinstance(parsed, list):
                    return parsed, "json_array"
            except (json.JSONDecodeError, ValueError):
                pass

        # Try JSONL (lines starting with '{')
        lines = stripped.splitlines()
        if lines and lines[0].strip().startswith("{"):
            try:
                records = [json.loads(line) for line in lines if line.strip()]
                if records and all(isinstance(r, dict) for r in records):
                    return records, "jsonl"
            except (json.JSONDecodeError, ValueError):
                pass

        # Try CSV
        try:
            return self._parse_csv(stripped), "csv"
        except Exception:
            pass

        raise ValueError("Cannot detect format: input is not valid JSON, JSONL, or CSV")

    def _parse_csv(self, text: str) -> List[Dict[str, Any]]:
        """Parse CSV text into list of dicts."""
        # Try to detect delimiter with Sniffer
        delimiter = ","
        try:
            dialect = csv.Sniffer().sniff(text)
            delimiter = dialect.delimiter
        except csv.Error:
            # Fallback: try tab if comma doesn't work well
            if "\t" in text:
                delimiter = "\t"

        reader = csv.DictReader(io.StringIO(text), delimiter=delimiter)
        records = [dict(row) for row in reader]
        if not records:
            raise ValueError("CSV produced no records")
        return records

    # -- chunk conversion -------------------------------------------------------

    def _to_chunks(
        self,
        records: List[Dict[str, Any]],
        record_type: str,
        source: str,
    ) -> List[Chunk]:
        """Convert record dicts into Chunk objects."""
        chunks: List[Chunk] = []
        for idx, record in enumerate(records):
            chunks.append(Chunk(
                content=_record_to_text(record),
                index=idx,
                chunk_type="record",
                metadata={
                    "record_type": record_type,
                    "fields": dict(record),
                    "source": source,
                },
            ))
        return chunks

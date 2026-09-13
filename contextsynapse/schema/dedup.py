"""Schema-driven deduplication with per-type keys and merge policies."""

from __future__ import annotations

from typing import Dict, Optional

from .sdl import DedupPolicy

# Default policy for types not defined in the schema
_DEFAULT_POLICY = DedupPolicy(key=["name"], merge="latest_wins")


class SchemaDedupStrategy:
    """Deduplication strategy driven by per-type DedupPolicy from the schema.

    Each node type can declare:
      - key fields: which properties form the identity key
      - merge policy: how duplicates are reconciled
        - latest_wins: incoming overwrites existing (existing-only fields preserved)
        - merge_properties: existing preserved, incoming fills gaps only
        - keep_both: no merge, both nodes kept
    """

    def __init__(self, strategies: Dict[str, DedupPolicy]):
        self._strategies = strategies

    def _policy(self, label: str) -> DedupPolicy:
        return self._strategies.get(label, _DEFAULT_POLICY)

    def dedup_key(self, label: str, properties: Dict) -> str:
        """Generate a dedup key from the type's key fields.

        Format: "TypeName:field1_val|field2_val" (lowercased, stripped).
        Unknown types fall back to DedupPolicy(key=["name"]).
        """
        policy = self._policy(label)
        parts = []
        for k in policy.key:
            val = properties.get(k, "")
            if val is None:
                val = ""
            parts.append(str(val).strip().lower())
        joined = "|".join(parts)
        return f"{label}:{joined}"

    def should_dedup(self, label: str) -> bool:
        """Return False if the merge policy is 'keep_both'."""
        return self._policy(label).merge != "keep_both"

    def merge(
        self, label: str, existing: Dict, incoming: Dict
    ) -> Optional[Dict]:
        """Merge two property dicts according to the type's merge policy.

        Returns the merged dict, or None for keep_both (caller keeps both nodes).
        """
        policy = self._policy(label)

        if policy.merge == "keep_both":
            return None

        if policy.merge == "merge_properties":
            # Existing preserved; incoming fills gaps only
            merged = dict(existing)
            for k, v in incoming.items():
                if k not in merged:
                    merged[k] = v
            return merged

        # latest_wins (default): incoming overwrites, existing-only fields preserved
        merged = dict(existing)
        merged.update(incoming)
        return merged

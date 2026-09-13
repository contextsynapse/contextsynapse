"""
AIQL Input Sanitization
========================
Prevents SQL/AIQL injection by sanitizing user-provided identifiers and values
before they are interpolated into AIQL query strings.

Usage::

    from contextsynapse.security.sanitize import sanitize_aiql_identifier, sanitize_aiql_value

    safe_label = sanitize_aiql_identifier(user_label)
    safe_value = sanitize_aiql_value(user_value)
    query = f'SELECT * FROM {safe_label} WHERE name = "{safe_value}"'
"""

from __future__ import annotations

import re
from typing import Any

# AIQL keywords that must never appear in user-provided identifiers
_AIQL_KEYWORDS = frozenset({
    "select", "from", "where", "create", "delete", "update", "drop",
    "insert", "node", "edge", "set", "match", "find", "show", "use",
    "graph", "explain", "load", "extract", "chunk", "embed", "index",
    "grant", "revoke", "union", "and", "or", "not", "in", "like",
    "between", "exists", "null", "true", "false",
})

# Pattern: only alphanumeric, underscore, hyphen allowed in identifiers
_SAFE_IDENTIFIER = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_-]*$")

# Pattern: namespace names — no colons, wildcards, or special Redis chars
_SAFE_NAMESPACE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_-]*$")

# Pattern for each segment within a hierarchical path
_SAFE_SEGMENT = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_-]*$")

# Reserved namespace names that cannot be created by users
_RESERVED_NAMESPACES = frozenset({
    "lru", "names", "deleted", "sync",
})

# System-internal namespaces — bypass validation entirely
_SYSTEM_NAMESPACES = frozenset({
    "default", "__intelligence_config__",
})

# Valid top-level scopes for hierarchical context paths
VALID_SCOPES = frozenset({"market", "portfolio", "client", "frozen"})


def parse_context_path(path: str) -> tuple:
    """Parse a context path into (scope, segments).

    Flat paths (no colon) default to 'market' scope.
    Hierarchical paths split on ':' — first segment is the scope.

    Args:
        path: Context path like 'tcs' or 'market:tcs' or 'portfolio:p001:holdings'

    Returns:
        Tuple of (scope, [remaining_segments])
    """
    if ":" not in path:
        return ("market", [path])

    parts = path.split(":")
    scope = parts[0]
    segments = parts[1:]
    return (scope, segments)


def validate_namespace(name: str) -> str:
    """Validate a context namespace name for safe use in Redis keys.

    Accepts both flat names ('tcs') and hierarchical paths ('market:tcs',
    'portfolio:p001:holdings'). Flat names are validated as single segments.
    Hierarchical paths are validated segment-by-segment with scope checking.

    Args:
        name: Namespace name or hierarchical path to validate.

    Returns:
        The validated name (unchanged).

    Raises:
        ValueError: If the name is invalid, reserved, or too long.
    """
    if not name or not isinstance(name, str):
        raise ValueError("Namespace must be a non-empty string")

    name = name.strip()

    # System-internal namespaces bypass all validation
    if name in _SYSTEM_NAMESPACES:
        return name

    if ":" in name:
        # Hierarchical path: scope:segment[:segment...]
        parts = name.split(":")
        scope = parts[0]
        segments = parts[1:]

        if scope not in VALID_SCOPES:
            raise ValueError(
                f"Invalid scope: {scope!r} — must be one of {sorted(VALID_SCOPES)}"
            )

        if len(segments) > 3:
            raise ValueError(
                f"Namespace path too deep: {len(segments) + 1} segments (max 4)"
            )

        for seg in segments:
            if not seg:
                raise ValueError("Empty segment in namespace path")
            if not _SAFE_SEGMENT.match(seg):
                raise ValueError(
                    f"Invalid segment: {seg!r} — only letters, digits, underscores, "
                    f"and hyphens allowed"
                )
            if len(seg) > 128:
                raise ValueError(f"Segment too long: {len(seg)} chars (max 128)")

        return name
    else:
        # Flat name (backward compat)
        if not _SAFE_NAMESPACE.match(name):
            raise ValueError(
                f"Invalid namespace: {name!r} — only letters, digits, underscores, "
                f"and hyphens allowed (no colons, wildcards, or spaces)"
            )

        if len(name) > 128:
            raise ValueError(f"Namespace too long: {len(name)} chars (max 128)")

        if name.lower() in _RESERVED_NAMESPACES:
            raise ValueError(f"Reserved namespace name: {name!r}")

        return name


def sanitize_aiql_identifier(name: str) -> str:
    """Sanitize a label or property name for use in AIQL queries.

    Allows only alphanumeric characters, underscores, and hyphens.
    Rejects AIQL keywords to prevent query manipulation.

    Args:
        name: User-provided label or property name.

    Returns:
        Sanitized identifier string.

    Raises:
        ValueError: If the identifier is invalid or contains AIQL keywords.
    """
    if not name or not isinstance(name, str):
        raise ValueError("Identifier must be a non-empty string")

    name = name.strip()

    if not _SAFE_IDENTIFIER.match(name):
        raise ValueError(
            f"Invalid identifier: {name!r} — only letters, digits, underscores, and hyphens allowed"
        )

    if name.lower() in _AIQL_KEYWORDS:
        raise ValueError(f"Reserved AIQL keyword: {name!r}")

    if len(name) > 128:
        raise ValueError(f"Identifier too long: {len(name)} chars (max 128)")

    return name


def sanitize_aiql_value(value: Any) -> str:
    """Sanitize a value for use in AIQL string literals.

    Escapes double quotes, backslashes, and strips dangerous characters
    that could break out of a quoted string context.

    Args:
        value: User-provided value (will be converted to string).

    Returns:
        Escaped string safe for use inside double-quoted AIQL literals.
    """
    if value is None:
        return ""

    s = str(value)

    # Strip null bytes
    s = s.replace("\x00", "")

    # Escape backslashes first, then double quotes
    s = s.replace("\\", "\\\\")
    s = s.replace('"', '\\"')

    # Strip semicolons — prevent statement chaining
    s = s.replace(";", "")

    # Limit length to prevent abuse
    if len(s) > 4096:
        s = s[:4096]

    return s


def build_safe_where_clause(where_dict: dict) -> str:
    """Build a safe AIQL WHERE clause from a user-provided dict.

    Each key is sanitized as an identifier, each value as a string literal.

    Args:
        where_dict: Dict of {property_name: value} filters.

    Returns:
        AIQL WHERE clause string, e.g. ``'WHERE name = "Alice" AND age = "30"'``
        Returns empty string if where_dict is empty or None.
    """
    if not where_dict:
        return ""

    conditions = []
    for k, v in where_dict.items():
        safe_key = sanitize_aiql_identifier(k)
        safe_val = sanitize_aiql_value(v)
        conditions.append(f'{safe_key} = "{safe_val}"')

    return " WHERE " + " AND ".join(conditions)

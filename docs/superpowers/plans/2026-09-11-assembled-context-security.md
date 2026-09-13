# Assembled Context + Security Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build hierarchical context namespaces with ACL, AssembledContext as the single security gate, JWT auth, frozen snapshots for SEBI trade audit, and per-scope encryption at rest.

**Architecture:** Contexts are organized into three scopes (`market`, `portfolio`, `client`) with role-based ACLs. AssembledContext composes atomic contexts through purpose templates, enforcing ACL checks per-atomic, logging every assembly, and freezing snapshots when trades are proposed. Encryption is transparent — portfolio/client scopes auto-encrypt via Fernet.

**Tech Stack:** Python 3.12, FastAPI, Redis, cryptography (Fernet), HMAC-SHA256 JWT (built-in, no PyJWT dependency)

**Spec:** `docs/superpowers/specs/2026-09-11-assembled-context-security-design.md`

## Global Constraints

- Python 3.12+, no new external dependencies except `cryptography` (already installed)
- JWT uses built-in HMAC-SHA256 implementation in `contextsynapse/api/auth.py` (no PyJWT)
- All new code in `contextsynapse/` package
- Tests in `tests/unit/`
- Backward compatibility: flat namespace `validate_namespace("tcs")` must still work during migration
- Env var prefix: `AICONTEXTDB_`
- Reuse existing `Role` and `Permission` enums from `contextsynapse/security/rbac.py`
- Reuse existing `FieldEncryptor` from `contextsynapse/security/encryption.py`
- Reuse existing `AuditLogger` from `contextsynapse/pms/audit.py`
- Reuse existing `create_jwt()` / `verify_jwt()` from `contextsynapse/api/auth.py`

---

### Task 1: Hierarchical Namespace Validation

**Files:**
- Modify: `contextsynapse/security/sanitize.py`
- Modify: `contextsynapse/core/redis_registry.py`
- Test: `tests/unit/test_hierarchical_namespace.py`

**Interfaces:**
- Consumes: Nothing (foundation task)
- Produces:
  - `validate_namespace(name: str) -> str` — accepts both flat (`tcs`) and hierarchical (`market:tcs`) formats
  - `parse_context_path(path: str) -> tuple[str, list[str]]` — returns `(scope, segments)`, e.g. `("market", ["tcs"])` or `("portfolio", ["p001", "holdings"])`
  - `VALID_SCOPES: frozenset` — `{"market", "portfolio", "client", "frozen"}`

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_hierarchical_namespace.py
"""Tests for hierarchical context namespace validation."""
import pytest
from contextsynapse.security.sanitize import validate_namespace, parse_context_path, VALID_SCOPES


class TestValidateNamespace:
    """validate_namespace() accepts flat and hierarchical formats."""

    def test_flat_name_accepted(self):
        """Backward compat: flat names like 'tcs' still pass."""
        assert validate_namespace("tcs") == "tcs"

    def test_flat_name_with_underscores(self):
        assert validate_namespace("india_economy") == "india_economy"

    def test_hierarchical_market(self):
        assert validate_namespace("market:tcs") == "market:tcs"

    def test_hierarchical_market_price(self):
        assert validate_namespace("market:tcs_price") == "market:tcs_price"

    def test_hierarchical_portfolio(self):
        assert validate_namespace("portfolio:p001:holdings") == "portfolio:p001:holdings"

    def test_hierarchical_client(self):
        assert validate_namespace("client:c001:profile") == "client:c001:profile"

    def test_hierarchical_frozen(self):
        assert validate_namespace("frozen:T-20260911-001") == "frozen:T-20260911-001"

    def test_rejects_invalid_scope(self):
        with pytest.raises(ValueError, match="Invalid scope"):
            validate_namespace("badscope:tcs")

    def test_rejects_too_deep(self):
        with pytest.raises(ValueError, match="too deep"):
            validate_namespace("market:a:b:c:d")

    def test_rejects_wildcard_in_segment(self):
        with pytest.raises(ValueError, match="only letters"):
            validate_namespace("market:tcs*")

    def test_rejects_empty(self):
        with pytest.raises(ValueError):
            validate_namespace("")

    def test_rejects_spaces_in_segment(self):
        with pytest.raises(ValueError):
            validate_namespace("market:tcs stock")

    def test_segment_max_length(self):
        long_seg = "a" * 129
        with pytest.raises(ValueError, match="too long"):
            validate_namespace(f"market:{long_seg}")

    def test_rejects_reserved_flat(self):
        with pytest.raises(ValueError, match="Reserved"):
            validate_namespace("lru")


class TestParseContextPath:
    """parse_context_path() splits hierarchical paths into (scope, segments)."""

    def test_flat_defaults_to_market(self):
        scope, parts = parse_context_path("tcs")
        assert scope == "market"
        assert parts == ["tcs"]

    def test_market_scope(self):
        scope, parts = parse_context_path("market:india_economy")
        assert scope == "market"
        assert parts == ["india_economy"]

    def test_portfolio_scope(self):
        scope, parts = parse_context_path("portfolio:p001:holdings")
        assert scope == "portfolio"
        assert parts == ["p001", "holdings"]

    def test_client_scope(self):
        scope, parts = parse_context_path("client:c001:mandate")
        assert scope == "client"
        assert parts == ["c001", "mandate"]

    def test_frozen_scope(self):
        scope, parts = parse_context_path("frozen:T-001")
        assert scope == "frozen"
        assert parts == ["T-001"]


class TestValidScopes:
    def test_contains_all_scopes(self):
        assert VALID_SCOPES == {"market", "portfolio", "client", "frozen"}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd c:/qgraph/qgraph-app && python -m pytest tests/unit/test_hierarchical_namespace.py -v`
Expected: FAIL — `parse_context_path` not defined, `VALID_SCOPES` not defined, `validate_namespace` rejects colons

- [ ] **Step 3: Implement hierarchical validation**

Update `contextsynapse/security/sanitize.py`:

```python
# Add after existing _RESERVED_NAMESPACES definition:

VALID_SCOPES = frozenset({"market", "portfolio", "client", "frozen"})

# Pattern for each segment within a hierarchical path
_SAFE_SEGMENT = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_-]*$")


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
```

Then replace the existing `validate_namespace()` function with:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd c:/qgraph/qgraph-app && python -m pytest tests/unit/test_hierarchical_namespace.py -v`
Expected: All PASS

- [ ] **Step 5: Add flat-to-hierarchical migration to registry**

In `contextsynapse/core/redis_registry.py`, add `_migrate_flat_to_hierarchical()` method called from `__init__` after the graph→context migration:

```python
def _migrate_flat_to_hierarchical(self):
    """One-time migration: rename flat context:{name} keys to context:market:{name}.

    Only runs if hierarchical keys don't exist yet. Safe to run multiple times.
    """
    try:
        names = self._r.smembers(self.NAMES_KEY)
        if not names:
            return

        # Check if any names are already hierarchical (contain ':')
        has_hierarchical = any(":" in n for n in names)
        if has_hierarchical:
            return  # already migrated

        pipe = self._r.pipeline()
        count = 0
        for name in names:
            old_key = f"{self.KEY_PREFIX}{name}"
            new_name = f"market:{name}"
            new_key = f"{self.KEY_PREFIX}{new_name}"

            meta = self._r.hgetall(old_key)
            if meta:
                meta["name"] = new_name
                pipe.hset(new_key, mapping=meta)
                pipe.delete(old_key)

            pipe.srem(self.NAMES_KEY, name)
            pipe.sadd(self.NAMES_KEY, new_name)

            # Update LRU
            score = self._r.zscore(self.LRU_KEY, name)
            if score is not None:
                pipe.zrem(self.LRU_KEY, name)
                pipe.zadd(self.LRU_KEY, {new_name: score})

            count += 1

        pipe.execute()
        if count > 0:
            logger.info("[REDIS-REG] Migrated %d contexts to hierarchical (market:*) keys", count)
    except Exception as e:
        logger.warning("[REDIS-REG] Flat→hierarchical migration failed: %s", e)
```

Call it from `__init__` after `_migrate_graph_to_context_keys()`.

- [ ] **Step 6: Commit**

```bash
git add contextsynapse/security/sanitize.py contextsynapse/core/redis_registry.py tests/unit/test_hierarchical_namespace.py
git commit -m "feat: hierarchical context namespace validation (market/portfolio/client/frozen scopes)"
```

---

### Task 2: Context ACL + Ownership Resolver

**Files:**
- Create: `contextsynapse/context/acl.py`
- Test: `tests/unit/test_context_acl.py`

**Interfaces:**
- Consumes:
  - `Role` enum from `contextsynapse/security/rbac.py`
  - `parse_context_path(path: str) -> tuple[str, list[str]]` from Task 1
- Produces:
  - `class Permission(str, Enum)` — `READ`, `WRITE` (context-level, distinct from RBAC Permission)
  - `class JWTIdentity` — `sub: str`, `role: Role`, `tenant: str`, `portfolio_ids: list[str]`, `client_ids: list[str]`, `client_id: str | None`
  - `CONTEXT_ACL: dict[str, dict[Role, set[Permission]]]` — scope→role→permissions mapping
  - `class OwnershipResolver` — `can_access(user: JWTIdentity, context_path: str) -> bool`
  - `def check_context_access(user: JWTIdentity, context_path: str, permission: Permission) -> bool`

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_context_acl.py
"""Tests for context ACL and ownership resolution."""
import pytest
from contextsynapse.context.acl import (
    ContextPermission, JWTIdentity, CONTEXT_ACL, OwnershipResolver,
    check_context_access,
)
from contextsynapse.security.rbac import Role


def _make_fm(portfolios=None, clients=None):
    return JWTIdentity(
        sub="fm_1", role=Role.FUND_MANAGER, tenant="firm_a",
        portfolio_ids=portfolios or ["p001", "p002"],
        client_ids=clients or ["c001"],
    )


def _make_compliance():
    return JWTIdentity(
        sub="co_1", role=Role.COMPLIANCE_OFFICER, tenant="firm_a",
        portfolio_ids=[], client_ids=[],
    )


def _make_client(client_id="c001"):
    return JWTIdentity(
        sub="client_1", role=Role.CLIENT_VIEWER, tenant="firm_a",
        portfolio_ids=[], client_ids=[], client_id=client_id,
    )


def _make_analyst():
    return JWTIdentity(
        sub="ra_1", role=Role.RESEARCH_ANALYST, tenant="firm_a",
        portfolio_ids=[], client_ids=[],
    )


class TestContextACL:
    def test_all_roles_can_read_market(self):
        for role in Role:
            perms = CONTEXT_ACL["market"].get(role, set())
            assert ContextPermission.READ in perms, f"{role} should read market"

    def test_analyst_cannot_read_portfolio(self):
        perms = CONTEXT_ACL["portfolio"].get(Role.RESEARCH_ANALYST, set())
        assert ContextPermission.READ not in perms

    def test_client_cannot_read_portfolio(self):
        perms = CONTEXT_ACL["portfolio"].get(Role.CLIENT_VIEWER, set())
        assert ContextPermission.READ not in perms

    def test_only_admin_can_write_market(self):
        for role in Role:
            perms = CONTEXT_ACL["market"].get(role, set())
            if role == Role.ADMIN:
                assert ContextPermission.WRITE in perms
            else:
                assert ContextPermission.WRITE not in perms


class TestOwnershipResolver:
    def setup_method(self):
        self.resolver = OwnershipResolver()

    def test_fm_can_access_own_portfolio(self):
        fm = _make_fm(portfolios=["p001"])
        assert self.resolver.can_access(fm, "portfolio:p001:holdings")

    def test_fm_denied_other_portfolio(self):
        fm = _make_fm(portfolios=["p001"])
        assert not self.resolver.can_access(fm, "portfolio:p999:holdings")

    def test_compliance_can_access_any_portfolio(self):
        co = _make_compliance()
        assert self.resolver.can_access(co, "portfolio:p999:holdings")

    def test_client_can_access_own_data(self):
        cl = _make_client("c001")
        assert self.resolver.can_access(cl, "client:c001:profile")

    def test_client_denied_other_client(self):
        cl = _make_client("c001")
        assert not self.resolver.can_access(cl, "client:c002:profile")

    def test_fm_can_access_own_client(self):
        fm = _make_fm(clients=["c001"])
        assert self.resolver.can_access(fm, "client:c001:mandate")

    def test_fm_denied_other_client(self):
        fm = _make_fm(clients=["c001"])
        assert not self.resolver.can_access(fm, "client:c999:mandate")

    def test_everyone_can_access_market(self):
        analyst = _make_analyst()
        assert self.resolver.can_access(analyst, "market:tcs")

    def test_unknown_scope_denied(self):
        fm = _make_fm()
        assert not self.resolver.can_access(fm, "badscope:foo")


class TestCheckContextAccess:
    def test_fm_read_own_portfolio(self):
        fm = _make_fm(portfolios=["p001"])
        assert check_context_access(fm, "portfolio:p001:holdings", ContextPermission.READ)

    def test_fm_write_market_denied(self):
        fm = _make_fm()
        assert not check_context_access(fm, "market:tcs", ContextPermission.WRITE)

    def test_analyst_read_portfolio_denied(self):
        analyst = _make_analyst()
        assert not check_context_access(analyst, "portfolio:p001:holdings", ContextPermission.READ)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd c:/qgraph/qgraph-app && python -m pytest tests/unit/test_context_acl.py -v`
Expected: FAIL — module `contextsynapse.context.acl` does not exist

- [ ] **Step 3: Implement ACL module**

```python
# contextsynapse/context/acl.py
"""Context-level Access Control — scope-based ACL + ownership resolution.

Every context path belongs to a scope (market, portfolio, client, frozen).
Each scope has role-based permissions. Portfolio and client scopes additionally
require ownership verification — an FM can only see their own portfolios.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional

from ..security.rbac import Role
from ..security.sanitize import parse_context_path, VALID_SCOPES


class ContextPermission(str, Enum):
    READ = "read"
    WRITE = "write"


@dataclass
class JWTIdentity:
    """Identity extracted from a JWT token."""
    sub: str
    role: Role
    tenant: str = ""
    portfolio_ids: List[str] = field(default_factory=list)
    client_ids: List[str] = field(default_factory=list)
    client_id: Optional[str] = None  # for CLIENT_VIEWER role


CONTEXT_ACL: dict = {
    "market": {
        Role.FUND_MANAGER:        {ContextPermission.READ},
        Role.COMPLIANCE_OFFICER:  {ContextPermission.READ},
        Role.RESEARCH_ANALYST:    {ContextPermission.READ},
        Role.CLIENT_VIEWER:       {ContextPermission.READ},
        Role.OPERATIONS:          {ContextPermission.READ},
        Role.ADMIN:               {ContextPermission.READ, ContextPermission.WRITE},
    },
    "portfolio": {
        Role.FUND_MANAGER:        {ContextPermission.READ},
        Role.COMPLIANCE_OFFICER:  {ContextPermission.READ},
        Role.RESEARCH_ANALYST:    set(),
        Role.CLIENT_VIEWER:       set(),
        Role.OPERATIONS:          {ContextPermission.READ},
        Role.ADMIN:               {ContextPermission.READ, ContextPermission.WRITE},
    },
    "client": {
        Role.FUND_MANAGER:        {ContextPermission.READ},
        Role.COMPLIANCE_OFFICER:  {ContextPermission.READ},
        Role.CLIENT_VIEWER:       {ContextPermission.READ},
        Role.OPERATIONS:          {ContextPermission.READ, ContextPermission.WRITE},
        Role.ADMIN:               {ContextPermission.READ, ContextPermission.WRITE},
    },
    "frozen": {
        Role.FUND_MANAGER:        {ContextPermission.READ},
        Role.COMPLIANCE_OFFICER:  {ContextPermission.READ},
        Role.RESEARCH_ANALYST:    set(),
        Role.CLIENT_VIEWER:       set(),
        Role.OPERATIONS:          {ContextPermission.READ},
        Role.ADMIN:               {ContextPermission.READ},
    },
}


class OwnershipResolver:
    """Checks whether a user can access a specific context path.

    ACL grants scope-level permission. This resolver checks whether the user
    owns the specific portfolio/client within that scope.
    """

    def can_access(self, user: JWTIdentity, context_path: str) -> bool:
        scope, parts = parse_context_path(context_path)

        if scope not in VALID_SCOPES:
            return False

        if scope == "market":
            return True

        if scope == "frozen":
            return user.role in (Role.COMPLIANCE_OFFICER, Role.ADMIN, Role.FUND_MANAGER)

        if scope == "portfolio":
            if not parts:
                return False
            portfolio_id = parts[0]
            if user.role in (Role.COMPLIANCE_OFFICER, Role.ADMIN):
                return True
            return portfolio_id in user.portfolio_ids

        if scope == "client":
            if not parts:
                return False
            client_id = parts[0]
            if user.role in (Role.COMPLIANCE_OFFICER, Role.ADMIN):
                return True
            if user.role == Role.CLIENT_VIEWER:
                return client_id == user.client_id
            return client_id in user.client_ids

        return False


def check_context_access(
    user: JWTIdentity,
    context_path: str,
    permission: ContextPermission,
) -> bool:
    """Check if a user has a specific permission on a context path.

    Combines scope-level ACL with ownership verification.
    """
    scope, _ = parse_context_path(context_path)
    scope_perms = CONTEXT_ACL.get(scope, {})
    role_perms = scope_perms.get(user.role, set())

    if permission not in role_perms:
        return False

    resolver = OwnershipResolver()
    return resolver.can_access(user, context_path)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd c:/qgraph/qgraph-app && python -m pytest tests/unit/test_context_acl.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add contextsynapse/context/acl.py tests/unit/test_context_acl.py
git commit -m "feat: context ACL with scope-based permissions and ownership resolver"
```

---

### Task 3: Frozen Context Snapshots

**Files:**
- Create: `contextsynapse/context/frozen.py`
- Test: `tests/unit/test_frozen_context.py`

**Interfaces:**
- Consumes: Nothing external (self-contained)
- Produces:
  - `@dataclass class FrozenContext` — `frozen_id`, `trade_id`, `assembled_id`, `atomics`, `content`, `content_hash`, `frozen_at`, `frozen_by`
  - `def freeze_context(trade_id: str, assembled_id: str, atomics: list[str], content: dict, frozen_by: str) -> FrozenContext`
  - `def verify_frozen(frozen: FrozenContext) -> bool`
  - `def store_frozen(redis_client, frozen: FrozenContext) -> None`
  - `def retrieve_frozen(redis_client, trade_id: str) -> FrozenContext | None`

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_frozen_context.py
"""Tests for frozen context snapshots (SEBI trade audit)."""
import json
import pytest
from contextsynapse.context.frozen import (
    FrozenContext, freeze_context, verify_frozen, store_frozen, retrieve_frozen,
)


class TestFreezeContext:
    def test_creates_frozen_with_hash(self):
        content = {"signals": [{"source": "tcs", "score": 0.7}], "price": 3500}
        frozen = freeze_context(
            trade_id="T-001",
            assembled_id="ac_001",
            atomics=["market:tcs", "market:tcs_price"],
            content=content,
            frozen_by="fm_user_1",
        )
        assert frozen.trade_id == "T-001"
        assert frozen.assembled_id == "ac_001"
        assert frozen.atomics == ["market:tcs", "market:tcs_price"]
        assert frozen.content == content
        assert frozen.content_hash  # non-empty
        assert frozen.frozen_by == "fm_user_1"
        assert frozen.frozen_id == "frozen:T-001"

    def test_hash_is_deterministic(self):
        content = {"a": 1, "b": [2, 3]}
        f1 = freeze_context("T-1", "ac_1", [], content, "user")
        f2 = freeze_context("T-2", "ac_2", [], content, "user2")
        assert f1.content_hash == f2.content_hash  # same content, same hash


class TestVerifyFrozen:
    def test_unmodified_content_passes(self):
        frozen = freeze_context("T-1", "ac_1", ["market:tcs"], {"price": 100}, "fm")
        assert verify_frozen(frozen) is True

    def test_modified_content_fails(self):
        frozen = freeze_context("T-1", "ac_1", ["market:tcs"], {"price": 100}, "fm")
        frozen.content["price"] = 999  # tamper
        assert verify_frozen(frozen) is False

    def test_modified_hash_fails(self):
        frozen = freeze_context("T-1", "ac_1", [], {"x": 1}, "fm")
        frozen.content_hash = "badhash"
        assert verify_frozen(frozen) is False


class TestStoreRetrieve:
    def test_roundtrip(self):
        """Store and retrieve a frozen context from a mock Redis."""

        class MockRedis:
            def __init__(self):
                self._store = {}
            def set(self, key, value):
                self._store[key] = value
            def get(self, key):
                return self._store.get(key)

        r = MockRedis()
        frozen = freeze_context("T-1", "ac_1", ["market:tcs"], {"price": 42}, "fm")
        store_frozen(r, frozen)
        retrieved = retrieve_frozen(r, "T-1")

        assert retrieved is not None
        assert retrieved.trade_id == "T-1"
        assert retrieved.content == {"price": 42}
        assert verify_frozen(retrieved) is True

    def test_retrieve_missing_returns_none(self):
        class MockRedis:
            def get(self, key):
                return None
        assert retrieve_frozen(MockRedis(), "T-999") is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd c:/qgraph/qgraph-app && python -m pytest tests/unit/test_frozen_context.py -v`
Expected: FAIL — module does not exist

- [ ] **Step 3: Implement frozen context**

```python
# contextsynapse/context/frozen.py
"""Frozen Context — immutable snapshot of assembled context at trade decision time.

When a trade is proposed, the context the FM saw is frozen with a SHA-256 hash.
SEBI auditors can later verify the hash to confirm the snapshot hasn't been tampered with.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


@dataclass
class FrozenContext:
    """Immutable snapshot of an assembled context tied to a trade decision."""
    frozen_id: str
    trade_id: str
    assembled_id: str
    atomics: List[str]
    content: Dict[str, Any]
    content_hash: str
    frozen_at: str
    frozen_by: str


def _compute_hash(content: Dict[str, Any]) -> str:
    """SHA-256 hash of canonical JSON (sorted keys, deterministic)."""
    canonical = json.dumps(content, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def freeze_context(
    trade_id: str,
    assembled_id: str,
    atomics: List[str],
    content: Dict[str, Any],
    frozen_by: str,
) -> FrozenContext:
    """Create a frozen context snapshot for a trade decision."""
    return FrozenContext(
        frozen_id=f"frozen:{trade_id}",
        trade_id=trade_id,
        assembled_id=assembled_id,
        atomics=list(atomics),
        content=content,
        content_hash=_compute_hash(content),
        frozen_at=datetime.now(timezone.utc).isoformat(),
        frozen_by=frozen_by,
    )


def verify_frozen(frozen: FrozenContext) -> bool:
    """Verify that a frozen context's content hasn't been tampered with."""
    return _compute_hash(frozen.content) == frozen.content_hash


def store_frozen(redis_client, frozen: FrozenContext) -> None:
    """Persist a frozen context to Redis."""
    data = json.dumps({
        "frozen_id": frozen.frozen_id,
        "trade_id": frozen.trade_id,
        "assembled_id": frozen.assembled_id,
        "atomics": frozen.atomics,
        "content": frozen.content,
        "content_hash": frozen.content_hash,
        "frozen_at": frozen.frozen_at,
        "frozen_by": frozen.frozen_by,
    }, default=str)
    redis_client.set(f"context:{frozen.frozen_id}", data)


def retrieve_frozen(redis_client, trade_id: str) -> Optional[FrozenContext]:
    """Retrieve a frozen context from Redis by trade ID."""
    raw = redis_client.get(f"context:frozen:{trade_id}")
    if raw is None:
        return None
    data = json.loads(raw)
    return FrozenContext(**data)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd c:/qgraph/qgraph-app && python -m pytest tests/unit/test_frozen_context.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add contextsynapse/context/frozen.py tests/unit/test_frozen_context.py
git commit -m "feat: frozen context snapshots with SHA-256 tamper verification"
```

---

### Task 4: AssembledContext + Purpose Templates

**Files:**
- Create: `contextsynapse/context/assembled.py`
- Test: `tests/unit/test_assembled_context.py`

**Interfaces:**
- Consumes:
  - `JWTIdentity`, `check_context_access`, `ContextPermission` from Task 2
  - `freeze_context` from Task 3
  - `parse_context_path` from Task 1
- Produces:
  - `class ContextPurpose(str, Enum)` — `PRE_TRADE`, `MORNING_BRIEF`, `COMPLIANCE_AUDIT`, `REBALANCE`, `CLIENT_REPORT`, `STOCK_ANALYSIS`, `RISK_REVIEW`, `AD_HOC`
  - `class AssembledContext` — purpose-driven context composition with ACL, audit, and freeze support
  - `PURPOSE_TEMPLATES: dict[ContextPurpose, dict]` — auto-include lists per purpose

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_assembled_context.py
"""Tests for AssembledContext composition and purpose templates."""
import pytest
from contextsynapse.context.assembled import (
    AssembledContext, ContextPurpose, PURPOSE_TEMPLATES, resolve_atomics,
)
from contextsynapse.context.acl import JWTIdentity, ContextPermission
from contextsynapse.security.rbac import Role


def _fm(portfolios=None):
    return JWTIdentity(
        sub="fm_1", role=Role.FUND_MANAGER, tenant="firm_a",
        portfolio_ids=portfolios or ["p001"], client_ids=["c001"],
    )


def _analyst():
    return JWTIdentity(
        sub="ra_1", role=Role.RESEARCH_ANALYST, tenant="firm_a",
        portfolio_ids=[], client_ids=[],
    )


class TestResolveAtomics:
    def test_pre_trade_resolves_stock_and_portfolio(self):
        atomics = resolve_atomics(
            ContextPurpose.PRE_TRADE, subject="tcs", portfolio_id="p001"
        )
        assert "market:tcs" in atomics
        assert "market:tcs_price" in atomics
        assert "market:regulatory_legal" in atomics
        assert "market:negative_signals" in atomics
        assert "portfolio:p001:holdings" in atomics

    def test_ad_hoc_returns_empty(self):
        atomics = resolve_atomics(ContextPurpose.AD_HOC)
        assert atomics == []

    def test_stock_analysis_includes_macro(self):
        atomics = resolve_atomics(ContextPurpose.STOCK_ANALYSIS, subject="infosys")
        assert "market:infosys" in atomics
        assert "market:infosys_price" in atomics
        assert "market:india_economy" in atomics

    def test_compliance_audit_includes_trades(self):
        atomics = resolve_atomics(
            ContextPurpose.COMPLIANCE_AUDIT, portfolio_id="p001"
        )
        assert "portfolio:p001:holdings" in atomics
        assert "portfolio:p001:trades" in atomics
        assert "market:regulatory_legal" in atomics


class TestAssembledContext:
    def test_create_with_purpose(self):
        ac = AssembledContext.create(
            purpose=ContextPurpose.PRE_TRADE,
            user=_fm(),
            subject="tcs",
            portfolio_id="p001",
        )
        assert ac.purpose == ContextPurpose.PRE_TRADE
        assert ac.assembled_by.sub == "fm_1"
        assert len(ac.atomics_granted) > 0
        assert len(ac.atomics_denied) == 0

    def test_acl_denies_analyst_portfolio(self):
        ac = AssembledContext.create(
            purpose=ContextPurpose.PRE_TRADE,
            user=_analyst(),
            subject="tcs",
            portfolio_id="p001",
        )
        # Analyst can see market contexts but not portfolio
        assert "market:tcs" in ac.atomics_granted
        assert "portfolio:p001:holdings" in ac.atomics_denied

    def test_include_adds_atomic(self):
        ac = AssembledContext.create(
            purpose=ContextPurpose.AD_HOC,
            user=_fm(),
        )
        ac = ac.include("market:geopolitical_risk")
        assert "market:geopolitical_risk" in ac.atomics_granted

    def test_exclude_removes_atomic(self):
        ac = AssembledContext.create(
            purpose=ContextPurpose.PRE_TRADE,
            user=_fm(),
            subject="tcs",
            portfolio_id="p001",
        )
        ac = ac.exclude("market:negative_signals")
        assert "market:negative_signals" not in ac.atomics_granted

    def test_to_dict_contains_metadata(self):
        ac = AssembledContext.create(
            purpose=ContextPurpose.PRE_TRADE,
            user=_fm(),
            subject="tcs",
            portfolio_id="p001",
        )
        d = ac.to_dict()
        assert d["id"].startswith("ac_")
        assert d["purpose"] == "pre_trade"
        assert "atomics_granted" in d
        assert "assembled_at" in d

    def test_freeze_produces_frozen_context(self):
        ac = AssembledContext.create(
            purpose=ContextPurpose.PRE_TRADE,
            user=_fm(),
            subject="tcs",
            portfolio_id="p001",
        )
        frozen = ac.to_snapshot(trade_id="T-001")
        assert frozen.trade_id == "T-001"
        assert frozen.frozen_by == "fm_1"
        assert frozen.content_hash  # non-empty
        assert len(frozen.atomics) > 0


class TestPurposeTemplates:
    def test_all_purposes_have_templates(self):
        for p in ContextPurpose:
            assert p in PURPOSE_TEMPLATES, f"Missing template for {p}"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd c:/qgraph/qgraph-app && python -m pytest tests/unit/test_assembled_context.py -v`
Expected: FAIL — module does not exist

- [ ] **Step 3: Implement AssembledContext**

```python
# contextsynapse/context/assembled.py
"""AssembledContext — the single security gate for context composition.

Every context delivery to an FM, agent, or LLM flows through AssembledContext.
It resolves atomic contexts from purpose templates, enforces ACL per-atomic,
logs the assembly, and can freeze a snapshot for SEBI trade audit.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional

from .acl import JWTIdentity, ContextPermission, check_context_access
from .frozen import FrozenContext, freeze_context

logger = logging.getLogger(__name__)


class ContextPurpose(str, Enum):
    PRE_TRADE = "pre_trade"
    MORNING_BRIEF = "morning_brief"
    COMPLIANCE_AUDIT = "compliance_audit"
    REBALANCE = "rebalance"
    CLIENT_REPORT = "client_report"
    STOCK_ANALYSIS = "stock_analysis"
    RISK_REVIEW = "risk_review"
    AD_HOC = "ad_hoc"


# Purpose templates define which atomic contexts each purpose auto-includes.
# Placeholders: {stock}, {portfolio_id}, {client_id} are resolved at assembly time.
PURPOSE_TEMPLATES: Dict[ContextPurpose, Dict[str, Any]] = {
    ContextPurpose.PRE_TRADE: {
        "atomics": [
            "market:{stock}", "market:{stock}_price",
            "market:regulatory_legal", "market:negative_signals",
            "portfolio:{portfolio_id}:holdings",
        ],
        "budget": "standard",
    },
    ContextPurpose.MORNING_BRIEF: {
        "atomics": [
            "market:india_economy", "market:nifty_50",
        ],
        "budget": "deep",
    },
    ContextPurpose.COMPLIANCE_AUDIT: {
        "atomics": [
            "portfolio:{portfolio_id}:holdings",
            "portfolio:{portfolio_id}:trades",
            "market:regulatory_legal",
        ],
        "budget": "deep",
    },
    ContextPurpose.REBALANCE: {
        "atomics": [
            "portfolio:{portfolio_id}:holdings",
        ],
        "budget": "standard",
    },
    ContextPurpose.CLIENT_REPORT: {
        "atomics": [
            "portfolio:{portfolio_id}:holdings",
            "portfolio:{portfolio_id}:nav",
            "client:{client_id}:mandate",
        ],
        "budget": "standard",
    },
    ContextPurpose.STOCK_ANALYSIS: {
        "atomics": [
            "market:{stock}", "market:{stock}_price",
            "market:india_economy", "market:negative_signals",
        ],
        "budget": "deep",
    },
    ContextPurpose.RISK_REVIEW: {
        "atomics": [
            "portfolio:{portfolio_id}:holdings",
            "market:negative_signals", "market:vix",
        ],
        "budget": "standard",
    },
    ContextPurpose.AD_HOC: {
        "atomics": [],
        "budget": "standard",
    },
}


def resolve_atomics(
    purpose: ContextPurpose,
    subject: str = "",
    portfolio_id: str = "",
    client_id: str = "",
) -> List[str]:
    """Expand a purpose template into concrete atomic context paths."""
    template = PURPOSE_TEMPLATES[purpose]
    result = []
    for pattern in template["atomics"]:
        path = pattern.format(
            stock=subject, portfolio_id=portfolio_id, client_id=client_id,
        )
        # Skip paths with unresolved placeholders
        if "{" in path:
            continue
        result.append(path)
    return result


def _make_id(purpose: ContextPurpose, subject: str) -> str:
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    suffix = f"_{subject}" if subject else ""
    return f"ac_{ts}_{purpose.value}{suffix}"


@dataclass
class AssembledContext:
    """Composed context with ACL enforcement and audit trail."""

    id: str
    purpose: ContextPurpose
    assembled_by: JWTIdentity
    assembled_at: str

    atomics_granted: List[str] = field(default_factory=list)
    atomics_denied: List[str] = field(default_factory=list)

    budget: str = "standard"
    frozen: bool = False

    @classmethod
    def create(
        cls,
        purpose: ContextPurpose,
        user: JWTIdentity,
        subject: str = "",
        portfolio_id: str = "",
        client_id: str = "",
    ) -> "AssembledContext":
        """Create an assembled context, enforcing ACL on each atomic."""
        atomics = resolve_atomics(purpose, subject, portfolio_id, client_id)
        template = PURPOSE_TEMPLATES[purpose]

        granted = []
        denied = []
        for path in atomics:
            if check_context_access(user, path, ContextPermission.READ):
                granted.append(path)
            else:
                denied.append(path)

        return cls(
            id=_make_id(purpose, subject),
            purpose=purpose,
            assembled_by=user,
            assembled_at=datetime.now(timezone.utc).isoformat(),
            atomics_granted=granted,
            atomics_denied=denied,
            budget=template.get("budget", "standard"),
        )

    def include(self, *context_paths: str) -> "AssembledContext":
        """Add atomic contexts (subject to ACL check)."""
        for path in context_paths:
            if path in self.atomics_granted:
                continue
            if check_context_access(self.assembled_by, path, ContextPermission.READ):
                self.atomics_granted.append(path)
            else:
                self.atomics_denied.append(path)
        return self

    def exclude(self, *context_paths: str) -> "AssembledContext":
        """Remove atomic contexts from this assembly."""
        for path in context_paths:
            if path in self.atomics_granted:
                self.atomics_granted.remove(path)
        return self

    def to_dict(self) -> Dict[str, Any]:
        """Serialize assembly metadata."""
        return {
            "id": self.id,
            "purpose": self.purpose.value,
            "assembled_by": self.assembled_by.sub,
            "assembled_at": self.assembled_at,
            "atomics_granted": self.atomics_granted,
            "atomics_denied": self.atomics_denied,
            "budget": self.budget,
            "frozen": self.frozen,
        }

    def to_snapshot(self, trade_id: str) -> FrozenContext:
        """Freeze this assembled context for a trade decision."""
        self.frozen = True
        return freeze_context(
            trade_id=trade_id,
            assembled_id=self.id,
            atomics=self.atomics_granted,
            content=self.to_dict(),
            frozen_by=self.assembled_by.sub,
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd c:/qgraph/qgraph-app && python -m pytest tests/unit/test_assembled_context.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add contextsynapse/context/assembled.py tests/unit/test_assembled_context.py
git commit -m "feat: AssembledContext with purpose templates, ACL enforcement, and freeze support"
```

---

### Task 5: JWT Identity Middleware + Audit Wiring

**Files:**
- Create: `contextsynapse/security/jwt_identity.py`
- Modify: `contextsynapse/pms/audit.py` (lines 58-69, add `CONTEXT_ASSEMBLY` category)
- Test: `tests/unit/test_jwt_identity.py`

**Interfaces:**
- Consumes:
  - `verify_jwt(token: str) -> dict | None` from `contextsynapse/api/auth.py`
  - `Role` from `contextsynapse/security/rbac.py`
  - `JWTIdentity` from Task 2
- Produces:
  - `def extract_identity(request: Request) -> JWTIdentity` — FastAPI dependency
  - `def create_pms_jwt(sub, role, tenant, portfolio_ids, client_ids, client_id) -> str` — helper to create PMS-scoped JWTs

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_jwt_identity.py
"""Tests for JWT identity extraction for PMS context access."""
import pytest
from contextsynapse.security.jwt_identity import extract_identity_from_token, create_pms_jwt
from contextsynapse.context.acl import JWTIdentity
from contextsynapse.security.rbac import Role


class TestCreatePmsJwt:
    def test_creates_valid_token(self):
        token = create_pms_jwt(
            sub="fm_1", role="fund_manager", tenant="firm_a",
            portfolio_ids=["p001"], client_ids=["c001"],
        )
        assert isinstance(token, str)
        assert token.count(".") == 2  # JWT has 3 parts

    def test_roundtrip(self):
        token = create_pms_jwt(
            sub="fm_1", role="fund_manager", tenant="firm_a",
            portfolio_ids=["p001", "p002"], client_ids=["c001"],
        )
        identity = extract_identity_from_token(token)
        assert identity is not None
        assert identity.sub == "fm_1"
        assert identity.role == Role.FUND_MANAGER
        assert identity.tenant == "firm_a"
        assert identity.portfolio_ids == ["p001", "p002"]
        assert identity.client_ids == ["c001"]


class TestExtractIdentity:
    def test_invalid_token_returns_none(self):
        assert extract_identity_from_token("bad.token.here") is None

    def test_compliance_role(self):
        token = create_pms_jwt(
            sub="co_1", role="compliance_officer", tenant="firm_a",
        )
        identity = extract_identity_from_token(token)
        assert identity.role == Role.COMPLIANCE_OFFICER

    def test_client_viewer_has_client_id(self):
        token = create_pms_jwt(
            sub="cl_1", role="client_viewer", tenant="firm_a",
            client_id="c001",
        )
        identity = extract_identity_from_token(token)
        assert identity.role == Role.CLIENT_VIEWER
        assert identity.client_id == "c001"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd c:/qgraph/qgraph-app && python -m pytest tests/unit/test_jwt_identity.py -v`
Expected: FAIL — module does not exist

- [ ] **Step 3: Implement JWT identity**

```python
# contextsynapse/security/jwt_identity.py
"""JWT Identity — extracts PMS-scoped identity from JWT tokens.

Creates and validates JWTs carrying role, tenant, portfolio, and client scopes.
Uses the existing HMAC-SHA256 JWT implementation from contextsynapse.api.auth.
"""
from __future__ import annotations

import os
from typing import List, Optional

from ..api.auth import create_jwt, verify_jwt
from ..context.acl import JWTIdentity
from ..security.rbac import Role


def create_pms_jwt(
    sub: str,
    role: str,
    tenant: str = "",
    portfolio_ids: Optional[List[str]] = None,
    client_ids: Optional[List[str]] = None,
    client_id: Optional[str] = None,
    expires_in: int = 3600,
) -> str:
    """Create a JWT token with PMS-scoped claims."""
    payload = {
        "sub": sub,
        "role": role,
        "tenant": tenant,
        "portfolio_ids": portfolio_ids or [],
        "client_ids": client_ids or [],
    }
    if client_id:
        payload["client_id"] = client_id
    return create_jwt(payload, expires_in=expires_in)


def extract_identity_from_token(token: str) -> Optional[JWTIdentity]:
    """Extract a JWTIdentity from a JWT token string.

    Returns None if the token is invalid, expired, or malformed.
    """
    payload = verify_jwt(token)
    if payload is None:
        return None

    role_str = payload.get("role", "")
    try:
        role = Role(role_str)
    except ValueError:
        return None

    return JWTIdentity(
        sub=payload.get("sub", ""),
        role=role,
        tenant=payload.get("tenant", ""),
        portfolio_ids=payload.get("portfolio_ids", []),
        client_ids=payload.get("client_ids", []),
        client_id=payload.get("client_id"),
    )
```

- [ ] **Step 4: Add CONTEXT_ASSEMBLY to audit categories**

In `contextsynapse/pms/audit.py`, update `AUDIT_CATEGORIES` (line 58-69):

```python
AUDIT_CATEGORIES = [
    "trade",          # trade lifecycle
    "portfolio",      # portfolio / holding changes
    "compliance",     # compliance checks, rule fires
    "report",         # statement generation
    "fee",            # fee calculation / deduction
    "cash",           # cash deposit / withdrawal
    "corporate",      # corporate actions
    "access",         # login / logout / permission change
    "rule",           # rule CRUD
    "system",         # automated actions
    "context",        # context assembly and access
]
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd c:/qgraph/qgraph-app && python -m pytest tests/unit/test_jwt_identity.py -v`
Expected: All PASS

- [ ] **Step 6: Commit**

```bash
git add contextsynapse/security/jwt_identity.py contextsynapse/pms/audit.py tests/unit/test_jwt_identity.py
git commit -m "feat: JWT identity extraction for PMS context access + context audit category"
```

---

### Task 6: Scoped Encryption

**Files:**
- Create: `contextsynapse/security/scoped_encryption.py`
- Test: `tests/unit/test_scoped_encryption.py`

**Interfaces:**
- Consumes:
  - `parse_context_path(path) -> tuple[str, list[str]]` from Task 1
  - `FieldEncryptor` from `contextsynapse/security/encryption.py`
- Produces:
  - `class ScopedEncryptor` — `encrypt(context_path, data) -> bytes`, `decrypt(context_path, raw) -> dict`
  - `def derive_client_key(tenant_key: bytes, client_id: str) -> bytes`

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_scoped_encryption.py
"""Tests for per-scope context encryption."""
import json
import os
import pytest
from contextsynapse.security.scoped_encryption import ScopedEncryptor, derive_client_key


@pytest.fixture
def encryptor():
    os.environ["AICONTEXTDB_TENANT_KEY"] = "test-tenant-key-for-unit-tests-32ch"
    enc = ScopedEncryptor()
    yield enc
    os.environ.pop("AICONTEXTDB_TENANT_KEY", None)


class TestScopedEncryptor:
    def test_market_data_not_encrypted(self, encryptor):
        data = {"price": 3500, "symbol": "TCS"}
        raw = encryptor.encrypt("market:tcs", data)
        # Market data is stored as plain JSON (no encryption)
        result = json.loads(raw)
        assert result == data

    def test_portfolio_data_encrypted(self, encryptor):
        data = {"holdings": [{"symbol": "TCS", "qty": 100}]}
        raw = encryptor.encrypt("portfolio:p001:holdings", data)
        # Encrypted data should NOT be valid JSON of the original
        assert raw != json.dumps(data).encode()

    def test_portfolio_roundtrip(self, encryptor):
        data = {"holdings": [{"symbol": "TCS", "qty": 100}]}
        raw = encryptor.encrypt("portfolio:p001:holdings", data)
        decrypted = encryptor.decrypt("portfolio:p001:holdings", raw)
        assert decrypted == data

    def test_client_data_roundtrip(self, encryptor):
        data = {"name": "John Doe", "pan": "ABCDE1234F"}
        raw = encryptor.encrypt("client:c001:profile", data)
        decrypted = encryptor.decrypt("client:c001:profile", raw)
        assert decrypted == data

    def test_frozen_data_roundtrip(self, encryptor):
        data = {"trade_id": "T-001", "content": {"price": 100}}
        raw = encryptor.encrypt("frozen:T-001", data)
        decrypted = encryptor.decrypt("frozen:T-001", raw)
        assert decrypted == data

    def test_different_clients_different_keys(self, encryptor):
        data = {"name": "test"}
        raw1 = encryptor.encrypt("client:c001:profile", data)
        raw2 = encryptor.encrypt("client:c002:profile", data)
        # Same data, different client keys → different ciphertext
        assert raw1 != raw2


class TestDeriveClientKey:
    def test_deterministic(self):
        k1 = derive_client_key(b"master", "c001")
        k2 = derive_client_key(b"master", "c001")
        assert k1 == k2

    def test_different_clients_different_keys(self):
        k1 = derive_client_key(b"master", "c001")
        k2 = derive_client_key(b"master", "c002")
        assert k1 != k2
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd c:/qgraph/qgraph-app && python -m pytest tests/unit/test_scoped_encryption.py -v`
Expected: FAIL — module does not exist

- [ ] **Step 3: Implement scoped encryption**

```python
# contextsynapse/security/scoped_encryption.py
"""Scoped Encryption — transparent per-scope encryption for context data.

Market data is plaintext (public). Portfolio, client, and frozen data are
encrypted with Fernet (AES-128-CBC). Client data uses per-client derived keys.
"""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
from typing import Any, Dict

from ..security.sanitize import parse_context_path

logger = logging.getLogger(__name__)

# Scopes that require encryption
_ENCRYPTED_SCOPES = {"portfolio", "client", "frozen"}

try:
    from cryptography.fernet import Fernet
    _FERNET_AVAILABLE = True
except ImportError:
    _FERNET_AVAILABLE = False
    logger.warning("cryptography package not installed — scoped encryption disabled")


def derive_client_key(tenant_key: bytes, client_id: str) -> bytes:
    """Derive a per-client Fernet key from the tenant master key."""
    derived = hashlib.pbkdf2_hmac("sha256", tenant_key, client_id.encode(), 100_000)
    return base64.urlsafe_b64encode(derived[:32])


class ScopedEncryptor:
    """Transparent encryption based on context scope."""

    def __init__(self, tenant_key: str = None):
        self._tenant_key = (
            tenant_key or os.environ.get("AICONTEXTDB_TENANT_KEY", "")
        ).encode()

        if self._tenant_key and _FERNET_AVAILABLE:
            # Derive a Fernet-compatible key from the tenant key
            dk = hashlib.pbkdf2_hmac(
                "sha256", self._tenant_key, b"contextsynapse_scope", 100_000
            )
            self._tenant_fernet_key = base64.urlsafe_b64encode(dk[:32])
        else:
            self._tenant_fernet_key = None

    def _fernet_for(self, context_path: str) -> "Fernet":
        """Get the Fernet instance for a given context path."""
        scope, parts = parse_context_path(context_path)

        if scope == "client" and parts:
            client_id = parts[0]
            key = derive_client_key(self._tenant_key, client_id)
        else:
            key = self._tenant_fernet_key

        return Fernet(key)

    def encrypt(self, context_path: str, data: Dict[str, Any]) -> bytes:
        """Encrypt context data based on scope. Market data passes through."""
        scope, _ = parse_context_path(context_path)
        serialized = json.dumps(data, default=str).encode("utf-8")

        if scope not in _ENCRYPTED_SCOPES or not _FERNET_AVAILABLE:
            return serialized

        f = self._fernet_for(context_path)
        return f.encrypt(serialized)

    def decrypt(self, context_path: str, raw: bytes) -> Dict[str, Any]:
        """Decrypt context data based on scope. Market data passes through."""
        scope, _ = parse_context_path(context_path)

        if scope not in _ENCRYPTED_SCOPES or not _FERNET_AVAILABLE:
            return json.loads(raw)

        f = self._fernet_for(context_path)
        decrypted = f.decrypt(raw)
        return json.loads(decrypted)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd c:/qgraph/qgraph-app && python -m pytest tests/unit/test_scoped_encryption.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add contextsynapse/security/scoped_encryption.py tests/unit/test_scoped_encryption.py
git commit -m "feat: scoped encryption — portfolio/client/frozen auto-encrypted via Fernet"
```

---

### Task 7: PMS Integration — Trade Freeze + Assembly Audit

**Files:**
- Modify: `contextsynapse/pms/trade.py` (add `assembled_context` param to `propose_trade`, freeze on propose)
- Test: `tests/unit/test_trade_freeze.py`

**Interfaces:**
- Consumes:
  - `AssembledContext` from Task 4
  - `store_frozen(redis_client, frozen)` from Task 3
  - `AuditLogger.log(action, category, ...)` from `contextsynapse/pms/audit.py`
- Produces:
  - `TradeManager.propose_trade()` now accepts optional `assembled_context: AssembledContext` and freezes it

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_trade_freeze.py
"""Tests for freezing assembled context on trade proposal."""
import pytest
from unittest.mock import MagicMock
from contextsynapse.context.assembled import AssembledContext, ContextPurpose
from contextsynapse.context.acl import JWTIdentity
from contextsynapse.context.frozen import verify_frozen, retrieve_frozen
from contextsynapse.security.rbac import Role


def _make_fm():
    return JWTIdentity(
        sub="fm_1", role=Role.FUND_MANAGER, tenant="firm_a",
        portfolio_ids=["p001"], client_ids=["c001"],
    )


class TestTradeFreezeIntegration:
    def test_assembled_context_freezes_on_snapshot(self):
        ac = AssembledContext.create(
            purpose=ContextPurpose.PRE_TRADE,
            user=_make_fm(),
            subject="tcs",
            portfolio_id="p001",
        )
        frozen = ac.to_snapshot(trade_id="T-20260911-001")

        assert frozen.trade_id == "T-20260911-001"
        assert frozen.frozen_by == "fm_1"
        assert verify_frozen(frozen) is True
        assert ac.frozen is True
        assert len(frozen.atomics) > 0
        assert "market:tcs" in frozen.atomics

    def test_frozen_content_contains_assembly_metadata(self):
        ac = AssembledContext.create(
            purpose=ContextPurpose.PRE_TRADE,
            user=_make_fm(),
            subject="tcs",
            portfolio_id="p001",
        )
        frozen = ac.to_snapshot(trade_id="T-002")
        assert frozen.content["purpose"] == "pre_trade"
        assert frozen.content["assembled_by"] == "fm_1"

    def test_store_and_retrieve_frozen(self):
        from contextsynapse.context.frozen import store_frozen

        class MockRedis:
            def __init__(self):
                self._store = {}
            def set(self, key, value):
                self._store[key] = value
            def get(self, key):
                return self._store.get(key)

        r = MockRedis()
        ac = AssembledContext.create(
            purpose=ContextPurpose.PRE_TRADE,
            user=_make_fm(),
            subject="tcs",
            portfolio_id="p001",
        )
        frozen = ac.to_snapshot(trade_id="T-003")
        store_frozen(r, frozen)

        retrieved = retrieve_frozen(r, "T-003")
        assert retrieved is not None
        assert verify_frozen(retrieved) is True
```

- [ ] **Step 2: Run test to verify it passes (integration of Tasks 3+4)**

Run: `cd c:/qgraph/qgraph-app && python -m pytest tests/unit/test_trade_freeze.py -v`
Expected: All PASS (this is an integration test of the already-built components)

- [ ] **Step 3: Commit**

```bash
git add tests/unit/test_trade_freeze.py
git commit -m "test: integration tests for trade freeze with assembled context"
```

---

### Task 8: Run Full Test Suite + Verify No Regressions

**Files:**
- No new files — verification only

**Interfaces:**
- Consumes: All tasks above
- Produces: Green test suite

- [ ] **Step 1: Run all new tests**

Run: `cd c:/qgraph/qgraph-app && python -m pytest tests/unit/test_hierarchical_namespace.py tests/unit/test_context_acl.py tests/unit/test_frozen_context.py tests/unit/test_assembled_context.py tests/unit/test_jwt_identity.py tests/unit/test_scoped_encryption.py tests/unit/test_trade_freeze.py -v`
Expected: All PASS

- [ ] **Step 2: Run existing test suite to verify no regressions**

Run: `cd c:/qgraph/qgraph-app && python -m pytest tests/unit/ -x --timeout=60 -q`
Expected: No new failures

- [ ] **Step 3: Verify imports work end-to-end**

```bash
cd c:/qgraph/qgraph-app && python -c "
from contextsynapse.security.sanitize import validate_namespace, parse_context_path, VALID_SCOPES
from contextsynapse.context.acl import JWTIdentity, ContextPermission, CONTEXT_ACL, OwnershipResolver, check_context_access
from contextsynapse.context.frozen import FrozenContext, freeze_context, verify_frozen, store_frozen, retrieve_frozen
from contextsynapse.context.assembled import AssembledContext, ContextPurpose, PURPOSE_TEMPLATES, resolve_atomics
from contextsynapse.security.jwt_identity import create_pms_jwt, extract_identity_from_token
from contextsynapse.security.scoped_encryption import ScopedEncryptor, derive_client_key
print('All imports OK')
"
```
Expected: "All imports OK"

- [ ] **Step 4: Commit any fixes**

```bash
git add -A
git commit -m "fix: resolve any issues found during full test suite run"
```

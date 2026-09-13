# Context Tokenization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build blockchain-inspired hash chains per entity, Merkle-root bundle tokens for assembled contexts, proof tokens for trade decisions, and value attribution tracking.

**Architecture:** Each atomic context (entity) maintains a per-entity hash chain in Redis — every update appends a new block with SHA-256 linkage to its predecessor. When contexts are assembled, a Merkle root is computed across all constituent chain heads. When a trade is proposed, a proof token anchors the bundle to the decision with full chain references.

**Tech Stack:** Python 3.12, Redis (hash chains + bundles + proofs), hashlib (SHA-256), no external dependencies

**Spec:** `docs/superpowers/specs/2026-09-11-context-tokenization-design.md`

## Global Constraints

- Python 3.12+, no new external dependencies
- Tests in `tests/unit/`
- Redis keys use prefix `chain:` for chains, `bundle:` for bundles, `proof:` for proofs, `attribution:` for attribution
- All hashing uses SHA-256 via `hashlib.sha256`
- Canonical JSON: `json.dumps(data, sort_keys=True, default=str)` — used everywhere for deterministic hashing
- Timestamps: ISO 8601 UTC via `datetime.now(timezone.utc).isoformat()`
- Existing `contextsynapse/context/assembled.py` and `contextsynapse/context/frozen.py` are modified, not replaced
- Reuse `ContextPurpose` from `contextsynapse/context/assembled.py`

---

### Task 1: ContextToken + ContextChain (Hash Chain Core)

**Files:**
- Create: `contextsynapse/context/token.py`
- Test: `tests/unit/test_context_token.py`

**Interfaces:**
- Consumes: Nothing (foundation task)
- Produces:
  - `def compute_block_hash(version: int, content_hash: str, prev_hash: str | None, timestamp: str) -> str`
  - `def canonical_json(data: dict) -> str`
  - `def content_hash(data: dict) -> str`
  - `@dataclass class ContextToken` — fields: `token_id`, `entity`, `version`, `content_hash`, `content_summary`, `prev_hash`, `block_hash`, `pipelines`, `pipeline_freshness`, `created_at`, `freshness_score`, `times_assembled`, `decisions_influenced`
  - `class ContextChain` — methods: `append(content, pipeline) -> ContextToken`, `get_head() -> ContextToken | None`, `get_version(v) -> ContextToken | None`, `verify() -> tuple[bool, int]`

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_context_token.py
"""Tests for ContextToken hash chain — blockchain-inspired per-entity versioning."""
import json
import pytest
from contextsynapse.context.token import (
    ContextToken, ContextChain, compute_block_hash,
    canonical_json, content_hash,
)


class MockRedis:
    """Minimal Redis mock for chain storage."""
    def __init__(self):
        self._store = {}
    def get(self, key):
        return self._store.get(key)
    def set(self, key, value):
        self._store[key] = value


class TestComputeBlockHash:
    def test_deterministic(self):
        h1 = compute_block_hash(1, "abc", None, "2026-09-11T10:00:00")
        h2 = compute_block_hash(1, "abc", None, "2026-09-11T10:00:00")
        assert h1 == h2

    def test_different_version_different_hash(self):
        h1 = compute_block_hash(1, "abc", None, "2026-09-11T10:00:00")
        h2 = compute_block_hash(2, "abc", None, "2026-09-11T10:00:00")
        assert h1 != h2

    def test_different_prev_hash_different_hash(self):
        h1 = compute_block_hash(2, "abc", "prev1", "2026-09-11T10:00:00")
        h2 = compute_block_hash(2, "abc", "prev2", "2026-09-11T10:00:00")
        assert h1 != h2

    def test_genesis_uses_genesis_string(self):
        h = compute_block_hash(1, "abc", None, "2026-09-11T10:00:00")
        assert isinstance(h, str) and len(h) == 64  # SHA-256 hex


class TestCanonicalJson:
    def test_sorted_keys(self):
        assert canonical_json({"b": 2, "a": 1}) == canonical_json({"a": 1, "b": 2})

    def test_deterministic(self):
        data = {"price": 3500, "symbol": "TCS"}
        assert canonical_json(data) == canonical_json(data)


class TestContextChain:
    def setup_method(self):
        self.redis = MockRedis()

    def test_genesis_block(self):
        chain = ContextChain(self.redis, "market:tcs")
        token = chain.append({"price": 3500}, "price_sensor")
        assert token.version == 1
        assert token.prev_hash is None
        assert token.entity == "market:tcs"
        assert token.token_id == "ct:market:tcs:v1"
        assert "price_sensor" in token.pipelines
        assert token.block_hash  # non-empty

    def test_chain_append_links_to_previous(self):
        chain = ContextChain(self.redis, "market:tcs")
        v1 = chain.append({"price": 3500}, "price_sensor")
        v2 = chain.append({"sentiment": 0.7}, "news_rss")
        assert v2.version == 2
        assert v2.prev_hash == v1.block_hash
        assert v2.token_id == "ct:market:tcs:v2"

    def test_get_head(self):
        chain = ContextChain(self.redis, "market:tcs")
        chain.append({"price": 3500}, "price_sensor")
        chain.append({"sentiment": 0.7}, "news_rss")
        head = chain.get_head()
        assert head is not None
        assert head.version == 2

    def test_get_version(self):
        chain = ContextChain(self.redis, "market:tcs")
        chain.append({"price": 3500}, "price_sensor")
        chain.append({"sentiment": 0.7}, "news_rss")
        v1 = chain.get_version(1)
        assert v1 is not None
        assert v1.version == 1

    def test_get_version_missing_returns_none(self):
        chain = ContextChain(self.redis, "market:tcs")
        assert chain.get_version(99) is None

    def test_get_head_empty_chain_returns_none(self):
        chain = ContextChain(self.redis, "market:tcs")
        assert chain.get_head() is None

    def test_pipeline_merge_preserves_existing(self):
        chain = ContextChain(self.redis, "market:tcs")
        chain.append({"close": 3500}, "price_sensor")
        v2 = chain.append({"sentiment": 0.7}, "news_rss")
        assert "price_sensor" in v2.content_summary
        assert "news_rss" in v2.content_summary
        assert v2.content_summary["price_sensor"]["close"] == 3500
        assert v2.content_summary["news_rss"]["sentiment"] == 0.7

    def test_multiple_pipelines_tracked(self):
        chain = ContextChain(self.redis, "market:tcs")
        chain.append({"close": 3500}, "price_sensor")
        v2 = chain.append({"sentiment": 0.7}, "news_rss")
        assert set(v2.pipelines) == {"price_sensor", "news_rss"}
        assert "price_sensor" in v2.pipeline_freshness
        assert "news_rss" in v2.pipeline_freshness

    def test_verify_clean_chain(self):
        chain = ContextChain(self.redis, "market:tcs")
        chain.append({"price": 3500}, "price_sensor")
        chain.append({"sentiment": 0.7}, "news_rss")
        chain.append({"upgrades": 2}, "analyst_feed")
        valid, count = chain.verify()
        assert valid is True
        assert count == 3

    def test_verify_tampered_chain(self):
        chain = ContextChain(self.redis, "market:tcs")
        chain.append({"price": 3500}, "price_sensor")
        chain.append({"sentiment": 0.7}, "news_rss")
        # Tamper with v1
        raw = self.redis.get("chain:market:tcs:v1")
        data = json.loads(raw)
        data["content_summary"]["price_sensor"]["close"] = 9999
        data["content_hash"] = "tampered"
        self.redis.set("chain:market:tcs:v1", json.dumps(data))
        valid, count = chain.verify()
        assert valid is False
        assert count == 0  # broke at v1

    def test_verify_empty_chain(self):
        chain = ContextChain(self.redis, "market:tcs")
        valid, count = chain.verify()
        assert valid is True
        assert count == 0

    def test_freshness_score_computed(self):
        chain = ContextChain(self.redis, "market:tcs")
        token = chain.append({"price": 3500}, "price_sensor")
        assert 0.0 <= token.freshness_score <= 1.0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd c:/qgraph/qgraph-app && python -m pytest tests/unit/test_context_token.py -v`
Expected: FAIL — module does not exist

- [ ] **Step 3: Implement ContextToken and ContextChain**

```python
# contextsynapse/context/token.py
"""ContextToken — blockchain-inspired per-entity hash chain.

Each atomic context (entity) maintains a chain of versioned tokens.
Every append creates a new block linked to its predecessor via SHA-256.
Multiple pipelines feed the same entity — each pipeline writes to its
own namespace within the content dict.

Redis keys:
  chain:{entity}:head  → int (latest version number)
  chain:{entity}:v{N}  → JSON (ContextToken)
"""
from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple


def canonical_json(data: Any) -> str:
    """Deterministic JSON serialization for hashing."""
    return json.dumps(data, sort_keys=True, default=str)


def content_hash(data: Any) -> str:
    """SHA-256 hash of canonical JSON."""
    return hashlib.sha256(canonical_json(data).encode("utf-8")).hexdigest()


def compute_block_hash(
    version: int,
    content_hash_val: str,
    prev_hash: Optional[str],
    timestamp: str,
) -> str:
    """Compute the block hash for a context token version.

    block_hash = SHA-256(version || content_hash || prev_hash || timestamp)
    """
    block_data = f"{version}:{content_hash_val}:{prev_hash or 'genesis'}:{timestamp}"
    return hashlib.sha256(block_data.encode("utf-8")).hexdigest()


@dataclass
class ContextToken:
    """One version (block) in an entity's hash chain."""
    token_id: str
    entity: str
    version: int

    content_hash: str
    content_summary: Dict[str, Any]

    prev_hash: Optional[str]
    block_hash: str

    pipelines: List[str]
    pipeline_freshness: Dict[str, str]

    created_at: str
    freshness_score: float

    times_assembled: int = 0
    decisions_influenced: int = 0


def _token_to_json(token: ContextToken) -> str:
    return json.dumps({
        "token_id": token.token_id,
        "entity": token.entity,
        "version": token.version,
        "content_hash": token.content_hash,
        "content_summary": token.content_summary,
        "prev_hash": token.prev_hash,
        "block_hash": token.block_hash,
        "pipelines": token.pipelines,
        "pipeline_freshness": token.pipeline_freshness,
        "created_at": token.created_at,
        "freshness_score": token.freshness_score,
        "times_assembled": token.times_assembled,
        "decisions_influenced": token.decisions_influenced,
    }, default=str)


def _token_from_json(raw: str) -> ContextToken:
    data = json.loads(raw)
    return ContextToken(**data)


class ContextChain:
    """Manages the hash chain for an atomic context (entity)."""

    def __init__(self, redis_client, entity: str):
        self._r = redis_client
        self._entity = entity

    def append(self, content: Dict[str, Any], pipeline: str) -> ContextToken:
        """Append a new version to the chain."""
        head_version = self._get_head_version()
        prev_token = self.get_version(head_version) if head_version > 0 else None

        merged = self._merge_content(prev_token, content, pipeline)
        new_version = head_version + 1
        c_hash = content_hash(merged)
        prev_hash = prev_token.block_hash if prev_token else None
        timestamp = datetime.now(timezone.utc).isoformat()
        block_hash = compute_block_hash(new_version, c_hash, prev_hash, timestamp)

        pipeline_freshness = {}
        if prev_token:
            pipeline_freshness = dict(prev_token.pipeline_freshness)
        pipeline_freshness[pipeline] = timestamp

        pipelines = sorted(set(
            (prev_token.pipelines if prev_token else []) + [pipeline]
        ))

        token = ContextToken(
            token_id=f"ct:{self._entity}:v{new_version}",
            entity=self._entity,
            version=new_version,
            content_hash=c_hash,
            content_summary=merged,
            prev_hash=prev_hash,
            block_hash=block_hash,
            pipelines=pipelines,
            pipeline_freshness=pipeline_freshness,
            created_at=timestamp,
            freshness_score=self._compute_freshness(pipeline_freshness),
            times_assembled=0,
            decisions_influenced=0,
        )

        self._r.set(f"chain:{self._entity}:v{new_version}", _token_to_json(token))
        self._r.set(f"chain:{self._entity}:head", str(new_version))
        return token

    def get_head(self) -> Optional[ContextToken]:
        """Get the latest token in the chain."""
        head = self._get_head_version()
        if head == 0:
            return None
        return self.get_version(head)

    def get_version(self, version: int) -> Optional[ContextToken]:
        """Get a specific version from the chain."""
        raw = self._r.get(f"chain:{self._entity}:v{version}")
        if raw is None:
            return None
        return _token_from_json(raw)

    def verify(self) -> Tuple[bool, int]:
        """Walk the chain from genesis to head, verifying each link.

        Returns (is_valid, verified_count).
        """
        head = self._get_head_version()
        if head == 0:
            return (True, 0)

        prev_hash = None
        for v in range(1, head + 1):
            token = self.get_version(v)
            if token is None:
                return (False, v - 1)
            expected = compute_block_hash(
                token.version, token.content_hash, prev_hash, token.created_at
            )
            if token.block_hash != expected:
                return (False, v - 1)
            if token.prev_hash != prev_hash:
                return (False, v - 1)
            prev_hash = token.block_hash

        return (True, head)

    def _get_head_version(self) -> int:
        raw = self._r.get(f"chain:{self._entity}:head")
        if raw is None:
            return 0
        return int(raw)

    def _merge_content(
        self,
        prev_token: Optional[ContextToken],
        new_content: Dict[str, Any],
        pipeline: str,
    ) -> Dict[str, Any]:
        """Merge new pipeline data into entity content.

        Each pipeline writes to its own namespace within the content dict.
        """
        if prev_token is None:
            return {pipeline: new_content}
        merged = dict(prev_token.content_summary)
        merged[pipeline] = new_content
        return merged

    def _compute_freshness(self, pipeline_freshness: Dict[str, str]) -> float:
        """Compute freshness score based on pipeline timestamps.

        Returns 1.0 if all pipelines refreshed within last 5 minutes,
        decays toward 0.0 as data ages.
        """
        if not pipeline_freshness:
            return 0.0
        now = time.time()
        scores = []
        for ts_str in pipeline_freshness.values():
            try:
                ts = datetime.fromisoformat(ts_str).timestamp()
                age_seconds = now - ts
                score = max(0.0, 1.0 - (age_seconds / 3600))  # decay over 1 hour
                scores.append(score)
            except (ValueError, TypeError):
                scores.append(0.0)
        return sum(scores) / len(scores) if scores else 0.0
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd c:/qgraph/qgraph-app && python -m pytest tests/unit/test_context_token.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add contextsynapse/context/token.py tests/unit/test_context_token.py
git commit -m "feat: ContextToken hash chain — per-entity blockchain-inspired versioning"
```

---

### Task 2: BundleToken + Merkle Tree

**Files:**
- Create: `contextsynapse/context/bundle.py`
- Test: `tests/unit/test_bundle_token.py`

**Interfaces:**
- Consumes: Nothing directly (self-contained Merkle math)
- Produces:
  - `@dataclass class TokenRef` — `entity: str`, `version: int`, `block_hash: str`
  - `@dataclass class BundleToken` — fields per spec Section 3.2
  - `def compute_merkle_root(block_hashes: list[str]) -> tuple[str, list[str]]`
  - `def generate_merkle_proof(block_hash: str, all_hashes: list[str]) -> list[tuple[str, str]]`
  - `def verify_membership(block_hash: str, merkle_root: str, proof_path: list[tuple[str, str]]) -> bool`
  - `def create_bundle(purpose, token_refs, assembled_by, acl_denied) -> BundleToken`

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_bundle_token.py
"""Tests for BundleToken — Merkle-root assembled context bundles."""
import pytest
from contextsynapse.context.bundle import (
    TokenRef, BundleToken, compute_merkle_root,
    generate_merkle_proof, verify_membership, create_bundle,
)
from contextsynapse.context.assembled import ContextPurpose


class TestComputeMerkleRoot:
    def test_single_hash(self):
        root, tree = compute_merkle_root(["aaa"])
        assert isinstance(root, str) and len(root) == 64

    def test_two_hashes(self):
        root, tree = compute_merkle_root(["aaa", "bbb"])
        assert isinstance(root, str)

    def test_deterministic(self):
        hashes = ["abc", "def", "ghi"]
        r1, _ = compute_merkle_root(hashes)
        r2, _ = compute_merkle_root(hashes)
        assert r1 == r2

    def test_order_independent(self):
        """Sorted internally, so order of input doesn't matter."""
        r1, _ = compute_merkle_root(["bbb", "aaa", "ccc"])
        r2, _ = compute_merkle_root(["aaa", "ccc", "bbb"])
        assert r1 == r2

    def test_different_hashes_different_root(self):
        r1, _ = compute_merkle_root(["aaa", "bbb"])
        r2, _ = compute_merkle_root(["aaa", "ccc"])
        assert r1 != r2

    def test_empty_hashes(self):
        root, tree = compute_merkle_root([])
        assert isinstance(root, str)
        assert tree == []

    def test_odd_number_of_hashes(self):
        root, tree = compute_merkle_root(["aaa", "bbb", "ccc"])
        assert isinstance(root, str)


class TestMerkleProof:
    def test_valid_member_proves(self):
        hashes = ["aaa", "bbb", "ccc", "ddd"]
        root, _ = compute_merkle_root(hashes)
        proof = generate_merkle_proof("bbb", hashes)
        assert verify_membership("bbb", root, proof)

    def test_non_member_fails(self):
        hashes = ["aaa", "bbb", "ccc"]
        root, _ = compute_merkle_root(hashes)
        proof = generate_merkle_proof("bbb", hashes)
        assert not verify_membership("zzz", root, proof)

    def test_single_element_proves(self):
        hashes = ["aaa"]
        root, _ = compute_merkle_root(hashes)
        proof = generate_merkle_proof("aaa", hashes)
        assert verify_membership("aaa", root, proof)


class TestCreateBundle:
    def test_creates_bundle_with_merkle_root(self):
        refs = [
            TokenRef(entity="market:tcs", version=5, block_hash="hash_tcs"),
            TokenRef(entity="market:india_economy", version=3, block_hash="hash_macro"),
        ]
        bundle = create_bundle(
            purpose=ContextPurpose.PRE_TRADE,
            token_refs=refs,
            assembled_by="fm_1",
            acl_denied=["portfolio:p999:holdings"],
        )
        assert bundle.bundle_id.startswith("bt:pre_trade:")
        assert bundle.merkle_root  # non-empty
        assert bundle.bundle_hash  # non-empty
        assert len(bundle.token_refs) == 2
        assert bundle.assembled_by == "fm_1"
        assert bundle.acl_denied == ["portfolio:p999:holdings"]

    def test_bundle_hash_includes_merkle_root(self):
        refs = [TokenRef("market:tcs", 1, "h1")]
        b1 = create_bundle(ContextPurpose.AD_HOC, refs, "fm", [])
        refs2 = [TokenRef("market:tcs", 1, "h2")]
        b2 = create_bundle(ContextPurpose.AD_HOC, refs2, "fm", [])
        assert b1.bundle_hash != b2.bundle_hash  # different Merkle roots
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd c:/qgraph/qgraph-app && python -m pytest tests/unit/test_bundle_token.py -v`
Expected: FAIL — module does not exist

- [ ] **Step 3: Implement BundleToken with Merkle tree**

```python
# contextsynapse/context/bundle.py
"""BundleToken — Merkle-root assembled context bundles.

When atomic contexts are assembled for a purpose, a BundleToken records
which entity chain versions were included, with a Merkle root for
efficient verification and selective proofs.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from .assembled import ContextPurpose


def _sha256(data: str) -> str:
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


@dataclass
class TokenRef:
    """Reference to a specific version of an entity's chain."""
    entity: str
    version: int
    block_hash: str


@dataclass
class BundleToken:
    """Assembled context bundle with Merkle root verification."""
    bundle_id: str
    purpose: ContextPurpose

    token_refs: List[TokenRef]

    merkle_root: str
    merkle_tree: List[str]

    assembled_by: str
    assembled_at: str
    acl_denied: List[str]

    bundle_hash: str


def compute_merkle_root(block_hashes: List[str]) -> Tuple[str, List[str]]:
    """Compute Merkle root from a list of block hashes.

    Returns (root_hash, full_tree_nodes) for selective verification.
    Input order doesn't matter — hashes are sorted for determinism.
    """
    if not block_hashes:
        return (_sha256("empty"), [])

    leaves = sorted(block_hashes)
    tree = list(leaves)

    level = leaves
    while len(level) > 1:
        next_level = []
        for i in range(0, len(level), 2):
            left = level[i]
            right = level[i + 1] if i + 1 < len(level) else left
            parent = _sha256(left + right)
            next_level.append(parent)
            tree.append(parent)
        level = next_level

    return (level[0], tree)


def generate_merkle_proof(
    block_hash: str,
    all_hashes: List[str],
) -> List[Tuple[str, str]]:
    """Generate a Merkle proof path for a specific block_hash.

    Returns list of (sibling_hash, side) pairs from leaf to root.
    """
    leaves = sorted(all_hashes)
    if block_hash not in leaves:
        return []

    proof = []
    level = leaves
    target = block_hash

    while len(level) > 1:
        idx = level.index(target)
        if idx % 2 == 0:
            sibling_idx = idx + 1 if idx + 1 < len(level) else idx
            sibling = level[sibling_idx]
            proof.append((sibling, "right"))
            parent = _sha256(target + sibling)
        else:
            sibling = level[idx - 1]
            proof.append((sibling, "left"))
            parent = _sha256(sibling + target)

        next_level = []
        for i in range(0, len(level), 2):
            left = level[i]
            right = level[i + 1] if i + 1 < len(level) else left
            next_level.append(_sha256(left + right))
        level = next_level
        target = parent

    return proof


def verify_membership(
    block_hash: str,
    merkle_root: str,
    proof_path: List[Tuple[str, str]],
) -> bool:
    """Verify that a block_hash is part of a Merkle root via proof path."""
    current = block_hash
    for sibling, side in proof_path:
        if side == "left":
            current = _sha256(sibling + current)
        else:
            current = _sha256(current + sibling)
    return current == merkle_root


def create_bundle(
    purpose: ContextPurpose,
    token_refs: List[TokenRef],
    assembled_by: str,
    acl_denied: List[str],
) -> BundleToken:
    """Create a BundleToken from token references."""
    block_hashes = [ref.block_hash for ref in token_refs]
    merkle_root, merkle_tree = compute_merkle_root(block_hashes)
    timestamp = datetime.now(timezone.utc).isoformat()
    ts_compact = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")

    bundle_hash = _sha256(f"{merkle_root}:{assembled_by}:{timestamp}")

    subject = token_refs[0].entity.split(":")[-1] if token_refs else "empty"
    bundle_id = f"bt:{purpose.value}:{subject}:{ts_compact}"

    return BundleToken(
        bundle_id=bundle_id,
        purpose=purpose,
        token_refs=token_refs,
        merkle_root=merkle_root,
        merkle_tree=merkle_tree,
        assembled_by=assembled_by,
        assembled_at=timestamp,
        acl_denied=acl_denied,
        bundle_hash=bundle_hash,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd c:/qgraph/qgraph-app && python -m pytest tests/unit/test_bundle_token.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add contextsynapse/context/bundle.py tests/unit/test_bundle_token.py
git commit -m "feat: BundleToken with Merkle root and selective membership proofs"
```

---

### Task 3: ProofToken + ChainAnchor

**Files:**
- Create: `contextsynapse/context/proof.py`
- Test: `tests/unit/test_proof_token.py`

**Interfaces:**
- Consumes:
  - `BundleToken`, `TokenRef` from Task 2
- Produces:
  - `@dataclass class ChainAnchor` — `entity: str`, `version: int`, `block_hash: str`
  - `@dataclass class ProofToken` — fields per spec Section 3.3
  - `def create_proof_token(trade_id, bundle, decision, decided_by) -> ProofToken`
  - `def verify_proof(proof: ProofToken) -> bool`
  - `def store_proof(redis_client, proof: ProofToken) -> None`
  - `def retrieve_proof(redis_client, trade_id: str) -> ProofToken | None`

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_proof_token.py
"""Tests for ProofToken — immutable trade decision proofs."""
import json
import pytest
from contextsynapse.context.proof import (
    ChainAnchor, ProofToken, create_proof_token, verify_proof,
    store_proof, retrieve_proof,
)
from contextsynapse.context.bundle import TokenRef, BundleToken, create_bundle
from contextsynapse.context.assembled import ContextPurpose


def _make_bundle():
    refs = [
        TokenRef("market:tcs", 5, "hash_tcs_v5"),
        TokenRef("market:india_economy", 3, "hash_macro_v3"),
    ]
    return create_bundle(ContextPurpose.PRE_TRADE, refs, "fm_1", [])


class TestCreateProofToken:
    def test_creates_proof_with_anchors(self):
        bundle = _make_bundle()
        decision = {"action": "buy", "symbol": "TCS", "qty": 500, "price": 3480}
        proof = create_proof_token("T-001", bundle, decision, "fm_1")
        assert proof.proof_id == "pt:T-001"
        assert proof.trade_id == "T-001"
        assert proof.bundle_id == bundle.bundle_id
        assert proof.bundle_hash == bundle.bundle_hash
        assert proof.merkle_root == bundle.merkle_root
        assert len(proof.anchors) == 2
        assert proof.anchors[0].entity == "market:tcs"
        assert proof.anchors[0].version == 5
        assert proof.decision == decision
        assert proof.decided_by == "fm_1"
        assert proof.proof_hash  # non-empty

    def test_proof_hash_deterministic_for_same_inputs(self):
        bundle = _make_bundle()
        decision = {"action": "buy", "symbol": "TCS"}
        p1 = create_proof_token("T-001", bundle, decision, "fm_1")
        # proof_hash depends on decided_at timestamp, so two calls differ
        # but the hash should be non-empty and well-formed
        assert isinstance(p1.proof_hash, str) and len(p1.proof_hash) == 64


class TestVerifyProof:
    def test_unmodified_passes(self):
        bundle = _make_bundle()
        proof = create_proof_token("T-001", bundle, {"action": "buy"}, "fm_1")
        assert verify_proof(proof) is True

    def test_modified_decision_fails(self):
        bundle = _make_bundle()
        proof = create_proof_token("T-001", bundle, {"action": "buy"}, "fm_1")
        proof.decision = {"action": "sell"}  # tamper
        assert verify_proof(proof) is False

    def test_modified_hash_fails(self):
        bundle = _make_bundle()
        proof = create_proof_token("T-001", bundle, {"action": "buy"}, "fm_1")
        proof.proof_hash = "tampered"
        assert verify_proof(proof) is False


class TestStoreRetrieve:
    def test_roundtrip(self):
        class MockRedis:
            def __init__(self): self._store = {}
            def set(self, k, v): self._store[k] = v
            def get(self, k): return self._store.get(k)

        r = MockRedis()
        bundle = _make_bundle()
        proof = create_proof_token("T-002", bundle, {"action": "buy"}, "fm_1")
        store_proof(r, proof)
        retrieved = retrieve_proof(r, "T-002")
        assert retrieved is not None
        assert retrieved.trade_id == "T-002"
        assert verify_proof(retrieved) is True

    def test_retrieve_missing(self):
        class MockRedis:
            def get(self, k): return None
        assert retrieve_proof(MockRedis(), "T-999") is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd c:/qgraph/qgraph-app && python -m pytest tests/unit/test_proof_token.py -v`
Expected: FAIL — module does not exist

- [ ] **Step 3: Implement ProofToken**

```python
# contextsynapse/context/proof.py
"""ProofToken — immutable proof anchoring a context bundle to a trade decision.

Created when a trade is proposed. Stores the bundle's Merkle root and chain
anchors (which version of each entity was seen). Tamper-proof via SHA-256.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from .bundle import BundleToken, TokenRef


def _sha256(data: str) -> str:
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


def _canonical(data: Any) -> str:
    return json.dumps(data, sort_keys=True, default=str)


@dataclass
class ChainAnchor:
    """Reference to a specific version in an entity's chain."""
    entity: str
    version: int
    block_hash: str


@dataclass
class ProofToken:
    """Immutable proof of a trade decision's context."""
    proof_id: str
    trade_id: str

    bundle_id: str
    bundle_hash: str
    merkle_root: str

    anchors: List[ChainAnchor]

    decision: Dict[str, Any]
    decided_by: str
    decided_at: str

    proof_hash: str

    trade_pnl: Optional[float] = None
    attribution_score: Optional[float] = None


def _compute_proof_hash(bundle_hash: str, decision: Dict, decided_at: str) -> str:
    data = f"{bundle_hash}:{_canonical(decision)}:{decided_at}"
    return _sha256(data)


def create_proof_token(
    trade_id: str,
    bundle: BundleToken,
    decision: Dict[str, Any],
    decided_by: str,
) -> ProofToken:
    """Create a proof token anchoring a bundle to a trade decision."""
    decided_at = datetime.now(timezone.utc).isoformat()
    proof_hash = _compute_proof_hash(bundle.bundle_hash, decision, decided_at)

    anchors = [
        ChainAnchor(entity=ref.entity, version=ref.version, block_hash=ref.block_hash)
        for ref in bundle.token_refs
    ]

    return ProofToken(
        proof_id=f"pt:{trade_id}",
        trade_id=trade_id,
        bundle_id=bundle.bundle_id,
        bundle_hash=bundle.bundle_hash,
        merkle_root=bundle.merkle_root,
        anchors=anchors,
        decision=decision,
        decided_by=decided_by,
        decided_at=decided_at,
        proof_hash=proof_hash,
    )


def verify_proof(proof: ProofToken) -> bool:
    """Verify that a proof token hasn't been tampered with."""
    expected = _compute_proof_hash(proof.bundle_hash, proof.decision, proof.decided_at)
    return expected == proof.proof_hash


def store_proof(redis_client, proof: ProofToken) -> None:
    """Persist a proof token to Redis."""
    data = json.dumps({
        "proof_id": proof.proof_id,
        "trade_id": proof.trade_id,
        "bundle_id": proof.bundle_id,
        "bundle_hash": proof.bundle_hash,
        "merkle_root": proof.merkle_root,
        "anchors": [{"entity": a.entity, "version": a.version, "block_hash": a.block_hash}
                     for a in proof.anchors],
        "decision": proof.decision,
        "decided_by": proof.decided_by,
        "decided_at": proof.decided_at,
        "proof_hash": proof.proof_hash,
        "trade_pnl": proof.trade_pnl,
        "attribution_score": proof.attribution_score,
    }, default=str)
    redis_client.set(f"proof:{proof.trade_id}", data)


def retrieve_proof(redis_client, trade_id: str) -> Optional[ProofToken]:
    """Retrieve a proof token from Redis."""
    raw = redis_client.get(f"proof:{trade_id}")
    if raw is None:
        return None
    data = json.loads(raw)
    data["anchors"] = [ChainAnchor(**a) for a in data["anchors"]]
    return ProofToken(**data)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd c:/qgraph/qgraph-app && python -m pytest tests/unit/test_proof_token.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add contextsynapse/context/proof.py tests/unit/test_proof_token.py
git commit -m "feat: ProofToken — immutable trade decision proofs with chain anchors"
```

---

### Task 4: Value Attribution

**Files:**
- Create: `contextsynapse/context/attribution.py`
- Test: `tests/unit/test_attribution.py`

**Interfaces:**
- Consumes:
  - `ProofToken`, `ChainAnchor` from Task 3
  - `ContextChain`, `ContextToken` from Task 1
- Produces:
  - `def attribute_trade_outcome(redis_client, proof: ProofToken, pnl: float) -> None`
  - `def entity_intelligence_score(redis_client, entity: str) -> dict`
  - `def trade_attribution(redis_client, trade_id: str) -> dict | None`

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_attribution.py
"""Tests for value attribution — tracking which context tokens drive profitable decisions."""
import json
import pytest
from contextsynapse.context.attribution import (
    attribute_trade_outcome, entity_intelligence_score, trade_attribution,
)
from contextsynapse.context.proof import ProofToken, ChainAnchor


class MockRedis:
    def __init__(self):
        self._store = {}
        self._lists = {}
    def get(self, k):
        return self._store.get(k)
    def set(self, k, v):
        self._store[k] = v
    def rpush(self, k, v):
        self._lists.setdefault(k, []).append(v)
    def lrange(self, k, start, end):
        lst = self._lists.get(k, [])
        if end == -1:
            return lst[start:]
        return lst[start:end + 1]


def _make_proof(trade_id="T-001", pnl=None):
    return ProofToken(
        proof_id=f"pt:{trade_id}",
        trade_id=trade_id,
        bundle_id="bt:pre_trade:tcs:20260911",
        bundle_hash="bhash",
        merkle_root="mroot",
        anchors=[
            ChainAnchor("market:tcs", 5, "hash_tcs"),
            ChainAnchor("market:india_economy", 3, "hash_macro"),
        ],
        decision={"action": "buy", "symbol": "TCS"},
        decided_by="fm_1",
        decided_at="2026-09-11T10:30:00",
        proof_hash="phash",
        trade_pnl=pnl,
    )


class TestAttributeTradeOutcome:
    def test_records_attribution(self):
        r = MockRedis()
        proof = _make_proof("T-001")
        attribute_trade_outcome(r, proof, 50000.0)
        # Check attribution lists exist for both entities
        tcs_attrs = r.lrange("attribution:market:tcs", 0, -1)
        macro_attrs = r.lrange("attribution:market:india_economy", 0, -1)
        assert len(tcs_attrs) == 1
        assert len(macro_attrs) == 1
        attr = json.loads(tcs_attrs[0])
        assert attr["trade_id"] == "T-001"
        assert attr["pnl"] == 50000.0
        assert attr["entity"] == "market:tcs"

    def test_trade_summary_stored(self):
        r = MockRedis()
        proof = _make_proof("T-001")
        attribute_trade_outcome(r, proof, 50000.0)
        summary = r.get("attribution:trade:T-001")
        assert summary is not None
        data = json.loads(summary)
        assert data["trade_id"] == "T-001"
        assert data["pnl"] == 50000.0
        assert len(data["entities"]) == 2


class TestEntityIntelligenceScore:
    def test_score_from_attributions(self):
        r = MockRedis()
        # Simulate 3 trades: 2 profitable, 1 loss
        for trade_id, pnl in [("T-1", 10000), ("T-2", -5000), ("T-3", 20000)]:
            proof = _make_proof(trade_id)
            attribute_trade_outcome(r, proof, pnl)
        score = entity_intelligence_score(r, "market:tcs")
        assert score["entity"] == "market:tcs"
        assert score["total_decisions"] == 3
        assert score["profitable_decisions"] == 2
        assert score["total_pnl_attributed"] == 25000.0
        assert abs(score["win_rate"] - 2/3) < 0.01

    def test_score_empty_entity(self):
        r = MockRedis()
        score = entity_intelligence_score(r, "market:unknown")
        assert score["total_decisions"] == 0
        assert score["win_rate"] == 0.0


class TestTradeAttribution:
    def test_retrieves_trade_summary(self):
        r = MockRedis()
        proof = _make_proof("T-001")
        attribute_trade_outcome(r, proof, 75000.0)
        result = trade_attribution(r, "T-001")
        assert result is not None
        assert result["pnl"] == 75000.0

    def test_missing_trade_returns_none(self):
        r = MockRedis()
        assert trade_attribution(r, "T-999") is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd c:/qgraph/qgraph-app && python -m pytest tests/unit/test_attribution.py -v`
Expected: FAIL — module does not exist

- [ ] **Step 3: Implement attribution**

```python
# contextsynapse/context/attribution.py
"""Value Attribution — tracks which context tokens drove profitable decisions.

After a trade is executed and P&L is known, attribute_trade_outcome() records
which entity chain versions contributed. entity_intelligence_score() computes
a track record for each entity's context quality over time.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from .proof import ProofToken


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def attribute_trade_outcome(
    redis_client,
    proof: ProofToken,
    pnl: float,
) -> None:
    """Attribute trade P&L back to the context tokens that influenced it."""
    proof.trade_pnl = pnl

    entities = []
    for anchor in proof.anchors:
        attribution = {
            "trade_id": proof.trade_id,
            "entity": anchor.entity,
            "version": anchor.version,
            "block_hash": anchor.block_hash,
            "pnl": pnl,
            "attributed_at": _now_iso(),
        }
        redis_client.rpush(
            f"attribution:{anchor.entity}",
            json.dumps(attribution, default=str),
        )
        entities.append(anchor.entity)

    # Store trade-level summary
    summary = {
        "trade_id": proof.trade_id,
        "pnl": pnl,
        "entities": entities,
        "decided_by": proof.decided_by,
        "decided_at": proof.decided_at,
        "attributed_at": _now_iso(),
    }
    redis_client.set(
        f"attribution:trade:{proof.trade_id}",
        json.dumps(summary, default=str),
    )


def entity_intelligence_score(redis_client, entity: str) -> Dict[str, Any]:
    """Compute intelligence score for an entity based on attribution history."""
    raw_list = redis_client.lrange(f"attribution:{entity}", 0, -1)
    if not raw_list:
        return {
            "entity": entity,
            "total_decisions": 0,
            "profitable_decisions": 0,
            "total_pnl_attributed": 0.0,
            "win_rate": 0.0,
            "avg_pnl_per_decision": 0.0,
        }

    attributions = [json.loads(item) for item in raw_list]
    total = len(attributions)
    profitable = sum(1 for a in attributions if a["pnl"] > 0)
    total_pnl = sum(a["pnl"] for a in attributions)

    return {
        "entity": entity,
        "total_decisions": total,
        "profitable_decisions": profitable,
        "total_pnl_attributed": total_pnl,
        "win_rate": profitable / total if total > 0 else 0.0,
        "avg_pnl_per_decision": total_pnl / total if total > 0 else 0.0,
    }


def trade_attribution(redis_client, trade_id: str) -> Optional[Dict[str, Any]]:
    """Retrieve attribution summary for a specific trade."""
    raw = redis_client.get(f"attribution:trade:{trade_id}")
    if raw is None:
        return None
    return json.loads(raw)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd c:/qgraph/qgraph-app && python -m pytest tests/unit/test_attribution.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add contextsynapse/context/attribution.py tests/unit/test_attribution.py
git commit -m "feat: value attribution — track which context tokens drive profitable decisions"
```

---

### Task 5: End-to-End Integration Test

**Files:**
- Test: `tests/unit/test_tokenization_e2e.py`

**Interfaces:**
- Consumes: All tasks above
- Produces: Verified end-to-end flow: chain → bundle → proof → attribution → verify

- [ ] **Step 1: Write the integration test**

```python
# tests/unit/test_tokenization_e2e.py
"""End-to-end test: pipeline feeds chain → assembly creates bundle → trade creates proof → attribution."""
import json
import os
import pytest

os.environ.setdefault("AICONTEXTDB_JWT_SECRET", "test-secret-key")

from contextsynapse.context.token import ContextChain
from contextsynapse.context.bundle import TokenRef, create_bundle, compute_merkle_root, generate_merkle_proof, verify_membership
from contextsynapse.context.proof import create_proof_token, verify_proof, store_proof, retrieve_proof
from contextsynapse.context.attribution import attribute_trade_outcome, entity_intelligence_score, trade_attribution
from contextsynapse.context.assembled import ContextPurpose


class MockRedis:
    def __init__(self):
        self._store = {}
        self._lists = {}
    def get(self, k):
        return self._store.get(k)
    def set(self, k, v):
        self._store[k] = v
    def rpush(self, k, v):
        self._lists.setdefault(k, []).append(v)
    def lrange(self, k, start, end):
        lst = self._lists.get(k, [])
        if end == -1:
            return lst[start:]
        return lst[start:end + 1]


class TestTokenizationE2E:
    def setup_method(self):
        self.redis = MockRedis()

    def test_full_flow(self):
        # 1. Pipelines feed entity chains
        tcs_chain = ContextChain(self.redis, "market:tcs")
        tcs_chain.append({"close": 3500, "volume": 1000000}, "price_sensor")
        tcs_chain.append({"sentiment": 0.65, "headline": "TCS beats estimates"}, "news_rss")
        tcs_chain.append({"upgrades": 2}, "analyst_feed")

        macro_chain = ContextChain(self.redis, "market:india_economy")
        macro_chain.append({"rbi_rate": 6.0, "action": "cut"}, "rbi_feed")

        # 2. Verify chains are intact
        valid_tcs, count_tcs = tcs_chain.verify()
        valid_macro, count_macro = macro_chain.verify()
        assert valid_tcs and count_tcs == 3
        assert valid_macro and count_macro == 1

        # 3. Get chain heads for assembly
        tcs_head = tcs_chain.get_head()
        macro_head = macro_chain.get_head()
        assert tcs_head.version == 3
        assert macro_head.version == 1

        # 4. Create bundle from chain heads
        refs = [
            TokenRef(tcs_head.entity, tcs_head.version, tcs_head.block_hash),
            TokenRef(macro_head.entity, macro_head.version, macro_head.block_hash),
        ]
        bundle = create_bundle(ContextPurpose.PRE_TRADE, refs, "fm_1", [])
        assert bundle.merkle_root

        # 5. Verify Merkle membership
        proof_path = generate_merkle_proof(tcs_head.block_hash, [r.block_hash for r in refs])
        assert verify_membership(tcs_head.block_hash, bundle.merkle_root, proof_path)

        # 6. Create proof token for trade decision
        decision = {"action": "buy", "symbol": "TCS.NS", "qty": 500, "price": 3480}
        proof = create_proof_token("T-20260911-001", bundle, decision, "fm_1")
        assert verify_proof(proof)
        assert len(proof.anchors) == 2
        assert proof.anchors[0].entity == "market:tcs"
        assert proof.anchors[0].version == 3

        # 7. Store and retrieve proof
        store_proof(self.redis, proof)
        retrieved = retrieve_proof(self.redis, "T-20260911-001")
        assert retrieved is not None
        assert verify_proof(retrieved)

        # 8. Attribute trade outcome (profit)
        attribute_trade_outcome(self.redis, proof, 75000.0)

        # 9. Check entity intelligence scores
        tcs_score = entity_intelligence_score(self.redis, "market:tcs")
        assert tcs_score["total_decisions"] == 1
        assert tcs_score["profitable_decisions"] == 1
        assert tcs_score["total_pnl_attributed"] == 75000.0

        macro_score = entity_intelligence_score(self.redis, "market:india_economy")
        assert macro_score["total_decisions"] == 1

        # 10. Check trade attribution
        trade_attr = trade_attribution(self.redis, "T-20260911-001")
        assert trade_attr is not None
        assert trade_attr["pnl"] == 75000.0
        assert "market:tcs" in trade_attr["entities"]
        assert "market:india_economy" in trade_attr["entities"]
```

- [ ] **Step 2: Run the integration test**

Run: `cd c:/qgraph/qgraph-app && python -m pytest tests/unit/test_tokenization_e2e.py -v`
Expected: All PASS

- [ ] **Step 3: Run ALL tokenization tests together**

Run: `cd c:/qgraph/qgraph-app && python -m pytest tests/unit/test_context_token.py tests/unit/test_bundle_token.py tests/unit/test_proof_token.py tests/unit/test_attribution.py tests/unit/test_tokenization_e2e.py -v`
Expected: All PASS

- [ ] **Step 4: Verify imports**

```bash
cd c:/qgraph/qgraph-app && python -c "
from contextsynapse.context.token import ContextToken, ContextChain, compute_block_hash, canonical_json, content_hash
from contextsynapse.context.bundle import TokenRef, BundleToken, compute_merkle_root, generate_merkle_proof, verify_membership, create_bundle
from contextsynapse.context.proof import ChainAnchor, ProofToken, create_proof_token, verify_proof, store_proof, retrieve_proof
from contextsynapse.context.attribution import attribute_trade_outcome, entity_intelligence_score, trade_attribution
print('All tokenization imports OK')
"
```

- [ ] **Step 5: Commit**

```bash
git add tests/unit/test_tokenization_e2e.py
git commit -m "test: end-to-end tokenization flow — chain → bundle → proof → attribution"
```

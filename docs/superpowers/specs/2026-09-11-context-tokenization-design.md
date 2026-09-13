# Context Tokenization Design — Blockchain-Inspired Verifiable Context Chain

**Date:** 2026-09-11
**Status:** Draft
**Scope:** ContextToken with hash chains, Merkle-root assembled bundles, verifiable proof tokens, value attribution
**Depends On:** Assembled Context + Security Hardening (2026-09-11, implemented)

---

## 1. Problem Statement

Atomic contexts (TCS, India Macro, Portfolio P001) currently store data without version history, provenance tracking, or tamper detection at the data level. The FrozenContext provides a snapshot at trade time, but there's no chain of custody showing how the context evolved to that state.

For SEBI-regulated fund management, we need to prove:
- **What data existed at each point in time** (version history)
- **That data hasn't been tampered with** (hash verification)
- **Which data influenced which decisions** (attribution)
- **Where the data came from** (pipeline provenance)

## 2. Design Goals

- Every atomic context version is a **block** in a per-entity hash chain
- Assembled contexts produce a **Merkle root** across their constituent atomics
- Frozen contexts (proof tokens) are **anchored** to specific chain versions
- Internal verification now, external verification possible later (no restructuring)
- No external blockchain dependency — private chains in Redis
- Value attribution tracks which context tokens drove profitable decisions

## 3. The Three Token Types

### 3.1 Context Token (Atomic)

One per entity. Each update creates a new version (block) linked to the previous via hash.

```python
@dataclass
class ContextToken:
    token_id: str                # "ct:market:tcs:v42"
    entity: str                  # "market:tcs"
    version: int                 # monotonically increasing

    # Block content
    content_hash: str            # SHA-256 of the content snapshot
    content_summary: Dict        # key metrics (price, sentiment, etc.)

    # Chain linkage (blockchain concept)
    prev_hash: Optional[str]     # hash of previous version (None for genesis)
    block_hash: str              # SHA-256(version + content_hash + prev_hash + timestamp)

    # Provenance
    pipelines: List[str]         # which pipelines contributed to this version
    pipeline_freshness: Dict[str, str]  # pipeline_name → last_refresh ISO timestamp

    # Metadata
    created_at: str              # ISO timestamp
    freshness_score: float       # 0.0 (stale) to 1.0 (fresh)

    # Attribution (updated post-trade)
    times_assembled: int         # how many bundle tokens included this version
    decisions_influenced: int    # how many proof tokens reference this version
```

### 3.2 Bundle Token (Assembled)

Created at runtime when atomics are fused for a purpose. Produces a Merkle root across all constituent tokens.

```python
@dataclass
class BundleToken:
    bundle_id: str               # "bt:pre_trade:tcs:p001:20260911T103000"
    purpose: ContextPurpose      # PRE_TRADE, MORNING_BRIEF, etc.

    # Constituent tokens (references, not copies)
    token_refs: List[TokenRef]   # [(entity, version, block_hash), ...]

    # Merkle root (blockchain concept)
    merkle_root: str             # hash of all constituent block_hashes
    merkle_tree: List[str]       # intermediate hashes for selective verification

    # Assembly metadata
    assembled_by: str            # user_id
    assembled_at: str            # ISO timestamp
    acl_denied: List[str]        # which atomics were denied (audit)

    # Bundle hash
    bundle_hash: str             # SHA-256(merkle_root + assembled_by + assembled_at)
```

### 3.3 Proof Token (Frozen)

Created when a trade is proposed. Immutable. Anchors a bundle to a financial decision.

```python
@dataclass
class ProofToken:
    proof_id: str                # "pt:T-20260911-001"
    trade_id: str

    # Anchored bundle
    bundle_id: str               # which bundle token this proves
    bundle_hash: str             # hash at time of freeze
    merkle_root: str             # Merkle root at time of freeze

    # Chain anchors (which version of each entity was seen)
    anchors: List[ChainAnchor]   # [(entity, version, block_hash), ...]

    # Decision metadata
    decision: Dict               # {action: "buy", symbol: "TCS", qty: 500, price: 3480}
    decided_by: str              # user_id
    decided_at: str              # ISO timestamp

    # Proof integrity
    proof_hash: str              # SHA-256(bundle_hash + decision + decided_at)

    # Value attribution (updated later)
    trade_pnl: Optional[float]   # P&L from this trade (filled post-execution)
    attribution_score: Optional[float]  # how much context contributed to outcome
```

## 4. Hash Chain Mechanics

### 4.1 Per-Entity Chain

Each atomic context (entity) maintains its own chain in Redis:

```
Redis keys:
  chain:market:tcs:head         → latest version number (int)
  chain:market:tcs:v1           → JSON of ContextToken v1 (genesis)
  chain:market:tcs:v2           → JSON of ContextToken v2
  chain:market:tcs:v{N}         → JSON of ContextToken vN (current)
```

### 4.2 Block Hash Computation

```python
def compute_block_hash(version: int, content_hash: str, prev_hash: str, timestamp: str) -> str:
    """Compute the block hash for a context token version.

    Mirrors blockchain block header hashing:
    block_hash = SHA-256(version || content_hash || prev_hash || timestamp)
    """
    block_data = f"{version}:{content_hash}:{prev_hash or 'genesis'}:{timestamp}"
    return hashlib.sha256(block_data.encode()).hexdigest()
```

### 4.3 Chain Verification

```python
def verify_chain(redis_client, entity: str) -> Tuple[bool, int]:
    """Walk the chain from genesis to head, verifying each link.

    Returns (is_valid, verified_count).
    An auditor calls this to confirm no version has been tampered with.
    """
    head = int(redis_client.get(f"chain:{entity}:head") or 0)
    prev_hash = None

    for v in range(1, head + 1):
        token = load_token(redis_client, entity, v)
        expected_hash = compute_block_hash(
            token.version, token.content_hash, prev_hash, token.created_at
        )
        if token.block_hash != expected_hash:
            return (False, v - 1)  # chain broken at version v
        if token.prev_hash != prev_hash:
            return (False, v - 1)  # prev_hash mismatch
        prev_hash = token.block_hash

    return (True, head)
```

### 4.4 Genesis Block

First version of any entity has `prev_hash = None`:

```python
def create_genesis(entity: str, content: Dict, pipelines: List[str]) -> ContextToken:
    content_hash = sha256(canonical_json(content))
    block_hash = compute_block_hash(1, content_hash, None, now_iso())
    return ContextToken(
        token_id=f"ct:{entity}:v1",
        entity=entity,
        version=1,
        content_hash=content_hash,
        content_summary=content,
        prev_hash=None,
        block_hash=block_hash,
        pipelines=pipelines,
        ...
    )
```

## 5. Merkle Root for Bundle Tokens

### 5.1 Why Merkle Trees

An assembled context (bundle) references N atomic tokens. The Merkle root lets you:
- Verify the entire bundle with one hash comparison
- Prove any single atomic was part of the bundle without revealing all others
- Detect if any constituent was swapped or modified

### 5.2 Merkle Root Computation

```python
def compute_merkle_root(block_hashes: List[str]) -> Tuple[str, List[str]]:
    """Compute Merkle root from a list of block hashes.

    Returns (root_hash, intermediate_hashes) for selective verification.
    """
    if not block_hashes:
        return (sha256("empty"), [])

    # Sort for deterministic ordering
    leaves = sorted(block_hashes)
    tree = list(leaves)

    level = leaves
    while len(level) > 1:
        next_level = []
        for i in range(0, len(level), 2):
            left = level[i]
            right = level[i + 1] if i + 1 < len(level) else left  # duplicate if odd
            parent = sha256(left + right)
            next_level.append(parent)
            tree.append(parent)
        level = next_level

    return (level[0], tree)
```

### 5.3 Selective Verification (Merkle Proof)

"Prove that TCS token was part of this assembled context without revealing all other tokens":

```python
def verify_membership(block_hash: str, merkle_root: str, proof_path: List[Tuple[str, str]]) -> bool:
    """Verify that a specific block_hash is part of a Merkle root.

    proof_path: list of (sibling_hash, side) pairs from leaf to root.
    """
    current = block_hash
    for sibling, side in proof_path:
        if side == "left":
            current = sha256(sibling + current)
        else:
            current = sha256(current + sibling)
    return current == merkle_root
```

## 6. Pipeline Integration

### 6.1 How Pipelines Feed the Chain

Each pipeline write creates a new version in the entity's chain:

```python
class ContextChain:
    """Manages the hash chain for an atomic context (entity)."""

    def __init__(self, redis_client, entity: str):
        self._r = redis_client
        self._entity = entity

    def append(self, content: Dict, pipeline: str) -> ContextToken:
        """Append a new version to the chain.

        Called by pipelines when they have new data for this entity.
        Multiple pipelines may feed the same entity — each write
        creates a new version incorporating the pipeline's contribution.
        """
        head = self._get_head()
        prev_token = self._get_version(head) if head > 0 else None

        # Merge pipeline data with existing content
        merged = self._merge_content(prev_token, content, pipeline)

        new_version = head + 1
        content_hash = sha256(canonical_json(merged))
        prev_hash = prev_token.block_hash if prev_token else None
        timestamp = now_iso()
        block_hash = compute_block_hash(new_version, content_hash, prev_hash, timestamp)

        # Compute freshness from all pipeline timestamps
        pipeline_freshness = {}
        if prev_token:
            pipeline_freshness = dict(prev_token.pipeline_freshness)
        pipeline_freshness[pipeline] = timestamp

        pipelines = list(set(
            (prev_token.pipelines if prev_token else []) + [pipeline]
        ))

        token = ContextToken(
            token_id=f"ct:{self._entity}:v{new_version}",
            entity=self._entity,
            version=new_version,
            content_hash=content_hash,
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

        # Store
        self._r.set(f"chain:{self._entity}:v{new_version}", token_to_json(token))
        self._r.set(f"chain:{self._entity}:head", new_version)

        return token
```

### 6.2 Content Merging Strategy

When a pipeline updates an entity, it merges with the previous version:

```python
def _merge_content(self, prev_token, new_content, pipeline):
    """Merge new pipeline data into existing entity content.

    Each pipeline writes to its own namespace within the content dict.
    This prevents pipelines from overwriting each other.
    """
    if prev_token is None:
        return {pipeline: new_content}

    merged = dict(prev_token.content_summary)
    merged[pipeline] = new_content
    return merged
```

Example — TCS entity after multiple pipeline writes:

```json
{
    "price_sensor": {
        "close": 3480, "change_pct": -2.3, "volume": 1500000,
        "rsi": 38, "ema_50": 3550
    },
    "news_rss": {
        "latest": "RBI rate cut positive for IT sector",
        "sentiment": 0.65, "article_count": 3
    },
    "analyst_feed": {
        "upgrades": 2, "downgrades": 0,
        "consensus_target": 3800
    },
    "ownership_feed": {
        "fii_change_pct": 0.3, "dii_change_pct": -0.1,
        "promoter_pct": 72.3
    }
}
```

### 6.3 Version Frequency Control

Not every data tick needs a new chain version. Configure per-entity:

```python
VERSION_POLICY = {
    "market:*":       {"min_interval_seconds": 300, "on_significant_change": True},
    "portfolio:*":    {"min_interval_seconds": 60,  "on_every_trade": True},
    "client:*":       {"min_interval_seconds": 3600, "on_profile_change": True},
}
```

- Price ticks every 5 seconds → new chain version at most every 5 minutes (or on significant change like >1% move)
- Portfolio → new version on every trade execution
- Client profile → new version only on profile changes

## 7. Value Attribution

### 7.1 Post-Trade Attribution

After a trade is executed and P&L is known, attribute value back to the context tokens:

```python
def attribute_trade_outcome(proof_token: ProofToken, pnl: float):
    """Attribute trade P&L back to the context tokens that influenced it.

    Each constituent token gets credit proportional to its contribution.
    This builds a track record for each entity's context quality.
    """
    proof_token.trade_pnl = pnl

    for anchor in proof_token.anchors:
        token = load_token(redis, anchor.entity, anchor.version)
        token.decisions_influenced += 1

        # Store attribution event
        attribution = {
            "trade_id": proof_token.trade_id,
            "entity": anchor.entity,
            "version": anchor.version,
            "pnl": pnl,
            "attributed_at": now_iso(),
        }
        redis.rpush(f"attribution:{anchor.entity}", json.dumps(attribution))
```

### 7.2 Entity Intelligence Score

Over time, each entity accumulates an attribution history:

```python
def entity_intelligence_score(entity: str) -> Dict:
    """How valuable is this entity's context for decision-making?"""
    attributions = load_attributions(redis, entity)
    return {
        "entity": entity,
        "total_decisions": len(attributions),
        "profitable_decisions": sum(1 for a in attributions if a["pnl"] > 0),
        "total_pnl_attributed": sum(a["pnl"] for a in attributions),
        "win_rate": profitable / total if total > 0 else 0,
        "avg_pnl_per_decision": total_pnl / total if total > 0 else 0,
    }
```

This answers: "Is the India Macro context actually helping our fund managers make better decisions?"

## 8. Integration with Existing System

### 8.1 ContextChain Wraps Existing Atomic Contexts

The chain doesn't replace existing `context:market:tcs` — it wraps it:

```
Existing (unchanged):
  context:market:tcs         → graph namespace (nodes, edges, metadata)
  g:market:tcs:*             → actual graph data in Redis

New (added alongside):
  chain:market:tcs:head      → latest version number
  chain:market:tcs:v1        → ContextToken JSON (genesis)
  chain:market:tcs:v{N}      → ContextToken JSON (current)
  attribution:market:tcs     → list of attribution events
```

### 8.2 AssembledContext Integration

`AssembledContext.create()` now produces a `BundleToken`:

```python
# In assembled.py — enhanced create()
def create(cls, purpose, user, subject, portfolio_id, ...):
    # ... existing ACL logic ...

    # Read current chain head for each granted atomic
    token_refs = []
    for atomic_path in granted:
        chain = ContextChain(redis, atomic_path)
        head_token = chain.get_head()
        if head_token:
            token_refs.append(TokenRef(
                entity=atomic_path,
                version=head_token.version,
                block_hash=head_token.block_hash,
            ))
            head_token.times_assembled += 1
            chain.update_token(head_token)

    # Compute Merkle root
    block_hashes = [ref.block_hash for ref in token_refs]
    merkle_root, merkle_tree = compute_merkle_root(block_hashes)

    bundle = BundleToken(
        bundle_id=f"bt:{purpose.value}:{subject}:{now_compact()}",
        purpose=purpose,
        token_refs=token_refs,
        merkle_root=merkle_root,
        merkle_tree=merkle_tree,
        assembled_by=user.sub,
        assembled_at=now_iso(),
        ...
    )
    return assembled_context, bundle
```

### 8.3 FrozenContext → ProofToken

`FrozenContext.to_snapshot()` now produces a `ProofToken`:

```python
def to_proof_token(frozen: FrozenContext, bundle: BundleToken, decision: Dict) -> ProofToken:
    return ProofToken(
        proof_id=f"pt:{frozen.trade_id}",
        trade_id=frozen.trade_id,
        bundle_id=bundle.bundle_id,
        bundle_hash=bundle.bundle_hash,
        merkle_root=bundle.merkle_root,
        anchors=[ChainAnchor(ref.entity, ref.version, ref.block_hash)
                 for ref in bundle.token_refs],
        decision=decision,
        decided_by=frozen.frozen_by,
        decided_at=frozen.frozen_at,
        proof_hash=compute_proof_hash(bundle.bundle_hash, decision, frozen.frozen_at),
    )
```

## 9. Verification API

### 9.1 Endpoints

```
GET  /verify/chain/{entity}              → verify entire chain integrity
GET  /verify/chain/{entity}/v/{version}  → verify specific version
GET  /verify/bundle/{bundle_id}          → verify bundle Merkle root
GET  /verify/proof/{trade_id}            → verify proof token integrity
GET  /verify/membership/{bundle_id}/{entity}  → Merkle proof that entity was in bundle
GET  /attribution/{entity}               → entity intelligence score
GET  /attribution/trade/{trade_id}       → which tokens influenced this trade
```

### 9.2 SEBI Auditor Workflow

```
1. Auditor: "Verify trade T-20260911-001"
   GET /verify/proof/T-20260911-001
   → proof_hash valid, bundle_hash matches, merkle_root matches

2. Auditor: "What context did the FM see?"
   → anchors: [TCS@v42, India_Macro@v15, P001@v8]

3. Auditor: "Was TCS context tampered with?"
   GET /verify/chain/market:tcs
   → chain valid, 42 versions, no tampering detected

4. Auditor: "Show me TCS at the time of decision"
   GET chain/market:tcs/v/42
   → full content snapshot with pipeline provenance

5. Auditor: "Prove TCS was part of this decision's context"
   GET /verify/membership/bt:pre_trade:tcs:20260911/market:tcs
   → Merkle proof valid — TCS was part of the assembled context
```

## 10. Redis Key Structure

```
# Chain storage (per entity)
chain:{entity}:head                  → int (latest version)
chain:{entity}:v{N}                  → JSON (ContextToken)

# Bundle storage
bundle:{bundle_id}                   → JSON (BundleToken)

# Proof storage (immutable)
proof:{trade_id}                     → JSON (ProofToken)

# Attribution
attribution:{entity}                 → LIST of JSON attribution events
attribution:trade:{trade_id}         → JSON summary

# Verification cache
verify:chain:{entity}                → JSON {valid: bool, verified_at, head_at_verify}
```

## 11. Files to Create/Modify

### New Files

| File | Purpose |
|---|---|
| `contextsynapse/context/token.py` | `ContextToken`, `ContextChain`, `compute_block_hash`, chain operations |
| `contextsynapse/context/bundle.py` | `BundleToken`, `TokenRef`, Merkle root/proof computation |
| `contextsynapse/context/proof.py` | `ProofToken`, `ChainAnchor`, proof creation and verification |
| `contextsynapse/context/attribution.py` | Value attribution tracking and entity intelligence scoring |
| `contextsynapse/api/verify_router.py` | Verification API endpoints |

### Modified Files

| File | Change |
|---|---|
| `contextsynapse/context/assembled.py` | Produce `BundleToken` alongside `AssembledContext` |
| `contextsynapse/context/frozen.py` | Produce `ProofToken` alongside `FrozenContext` |

## 12. Version Pruning (Storage Management)

Chains grow over time. Pruning policy:

- **Hot** (last 24 hours): all versions kept in Redis
- **Warm** (last 90 days): every 10th version kept in Redis, all on disk
- **Cold** (90+ days): only anchored versions (referenced by proof tokens) kept, rest on disk
- **Never pruned**: genesis block, any version referenced by a proof token

Proof tokens are never pruned (SEBI 5-year minimum retention).

## 13. Future: External Verification

To make tokens verifiable by external parties without Redis access:

1. Periodically publish a **checkpoint** — the head block_hash of every chain — to an external store (could be a public blockchain, a notary service, or even a signed RSS feed)
2. External verifier: "Is chain:market:tcs at version 42 with hash d2a8 legitimate?" → compare against published checkpoint
3. This requires zero restructuring — just an additional publish step on top of the existing chain

## 14. Testing Strategy

| Test | What It Validates |
|---|---|
| `test_genesis_block` | First version has prev_hash=None, valid block_hash |
| `test_chain_append` | New version links to previous via prev_hash |
| `test_chain_verify_clean` | Unmodified chain passes verification |
| `test_chain_verify_tampered` | Modified version breaks chain verification |
| `test_merkle_root_deterministic` | Same inputs produce same Merkle root |
| `test_merkle_membership_proof` | Valid member produces valid proof |
| `test_merkle_non_member_fails` | Non-member fails proof |
| `test_proof_token_integrity` | Proof hash verifies correctly |
| `test_proof_token_tamper_detected` | Modified proof fails verification |
| `test_attribution_records_pnl` | Post-trade P&L attributed to constituent tokens |
| `test_entity_intelligence_score` | Score computed from attribution history |
| `test_pipeline_merge` | Multiple pipelines write to same entity without overwriting |
| `test_version_frequency_control` | Rapid writes throttled per policy |
| `test_bundle_from_assembled_context` | AssembledContext produces valid BundleToken |
| `test_proof_from_frozen_context` | FrozenContext produces valid ProofToken with anchors |

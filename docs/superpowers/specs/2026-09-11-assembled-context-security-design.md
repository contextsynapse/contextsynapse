# Assembled Context + Security Hardening Design

**Date:** 2026-09-11
**Status:** Approved
**Scope:** Hierarchical context namespaces, AssembledContext composition, ACL, JWT auth, audit trail, frozen snapshots, encryption at rest

---

## 1. Problem Statement

ContextSynapse stores atomic contexts as flat `context:{name}` keys in Redis. For a SEBI-regulated PMS (Portfolio Management System), this has three gaps:

1. **No scope isolation** — market data, portfolio data, and client PII live in the same flat namespace with no access boundaries
2. **No formal composition** — context assembly happens ad-hoc through RuntimeContextAssembler and ContextHub, with no audit of what was assembled for whom
3. **No trade decision provenance** — SEBI requires proof of what information was available when a trade decision was made. Currently there's no frozen snapshot of the context a Fund Manager (FM) saw

## 2. Design Goals

- **Hierarchical namespaces** with scope-based access control (market / portfolio / client)
- **AssembledContext** as the single gate — no context reaches an FM, agent, or LLM without passing through it
- **Security built into composition**, not bolted on top
- **SEBI audit compliance** — frozen context snapshots with tamper-proof hashes
- **Hybrid deployment** — single-tenant now, multi-tenant ready (add tenant prefix later)

## 3. Hierarchical Context Namespace

### 3.1 Structure

```
context:{scope}:{name}
```

Three scopes:

| Scope | Pattern | Access Default | Examples |
|---|---|---|---|
| **market** | `context:market:{name}` | All roles (read-only) | `context:market:tcs`, `context:market:india_economy` |
| **portfolio** | `context:portfolio:{id}:{name}` | FM (own), Compliance (all), Admin (all) | `context:portfolio:p001:holdings` |
| **client** | `context:client:{id}:{name}` | Client (own, read-only), FM (own clients), Compliance (all) | `context:client:c001:profile` |

Future multi-tenant: `context:{tenant}:{scope}:{name}` — one constant change, no structural redesign.

### 3.2 Migration from Flat to Hierarchical

| Current | New | Scope |
|---|---|---|
| `context:tcs` | `context:market:tcs` | market |
| `context:tcs_price` | `context:market:tcs_price` | market |
| `context:india_economy` | `context:market:india_economy` | market |
| `context:regulatory_legal` | `context:market:regulatory_legal` | market |
| `context:nifty_50` | `context:market:nifty_50` | market |
| `context:vix` | `context:market:vix` | market |
| (new) portfolio holdings | `context:portfolio:{id}:holdings` | portfolio |
| (new) portfolio trades | `context:portfolio:{id}:trades` | portfolio |
| (new) portfolio NAV | `context:portfolio:{id}:nav` | portfolio |
| (new) client profile | `context:client:{id}:profile` | client |
| (new) client mandate | `context:client:{id}:mandate` | client |

### 3.3 Namespace Validation

`validate_namespace()` accepts hierarchical format with strict rules:

- **Valid:** `context:market:tcs`, `context:portfolio:p001:holdings`, `context:client:c001:profile`
- **Invalid:** `context:tcs` (no scope), `context:market:foo:bar:baz` (too deep), `context:market:*` (wildcards)
- Each segment: `[a-zA-Z0-9][a-zA-Z0-9_-]*`, max 128 chars per segment
- Max depth: 4 segments (scope + max 3 levels)

## 4. AssembledContext

### 4.1 Core Object

```python
@dataclass
class AssembledContext:
    id: str                          # "ac_{timestamp}_{purpose}_{subject}"
    purpose: ContextPurpose          # enum: PRE_TRADE, MORNING_BRIEF, etc.
    assembled_by: JWTIdentity        # who requested this assembly
    assembled_at: datetime           # when

    atomics: List[AtomicRef]         # which contexts were pulled in
    budget: ProjectionBudget         # token/latency tier

    frozen: bool = False             # immutable once trade is proposed
    snapshot_hash: Optional[str]     # SHA-256 of frozen content

    # Dynamic composition
    def include(self, *context_paths: str) -> "AssembledContext"
    def exclude(self, *context_paths: str) -> "AssembledContext"

    # Delivery
    def to_messages(self) -> List[Dict]     # LLM-ready (OpenAI/Anthropic format)
    def to_dict(self) -> Dict               # structured data
    def to_snapshot(self) -> FrozenContext   # immutable record for audit

    # Audit metadata
    atomics_granted: List[str]       # which atomics passed ACL
    atomics_denied: List[str]        # which were denied (logged, not delivered)
    token_count: int                 # actual tokens assembled
```

### 4.2 ContextPurpose Enum

```python
class ContextPurpose(str, Enum):
    PRE_TRADE = "pre_trade"              # before proposing a trade
    MORNING_BRIEF = "morning_brief"      # daily market open brief
    COMPLIANCE_AUDIT = "compliance_audit" # SEBI inspection
    REBALANCE = "rebalance"              # drift correction
    CLIENT_REPORT = "client_report"      # monthly client report
    STOCK_ANALYSIS = "stock_analysis"    # deep dive on single stock
    RISK_REVIEW = "risk_review"          # risk monitoring
    AD_HOC = "ad_hoc"                    # explicitly specified atomics
```

### 4.3 Purpose Templates

Each purpose auto-resolves which atomic contexts to include:

| Purpose | Auto-Includes | Optional (FM can add) | Budget |
|---|---|---|---|
| PRE_TRADE | `market:{stock}`, `market:{stock}_price`, sector, `market:regulatory_legal`, `market:negative_signals`, `portfolio:{id}:holdings` | `market:geopolitical_risk` | standard (3K tokens) |
| MORNING_BRIEF | All portfolio stock contexts + `market:india_economy` + `market:nifty_50` | sector contexts, `market:fii_dii_flows` | deep (8K tokens) |
| COMPLIANCE_AUDIT | `portfolio:{id}:holdings`, `portfolio:{id}:trades`, `market:regulatory_legal` | all market contexts | deep |
| REBALANCE | `portfolio:{id}:holdings`, model portfolio, drift data, all held stock contexts | tax lots | standard |
| CLIENT_REPORT | `portfolio:{id}:holdings`, `portfolio:{id}:nav`, `client:{id}:mandate` | benchmark contexts | standard |
| STOCK_ANALYSIS | `market:{stock}`, `market:{stock}_price`, sector, macro, signals | all sensors | deep |
| RISK_REVIEW | `portfolio:{id}:holdings`, `market:negative_signals`, `market:vix` | sector contexts | standard |
| AD_HOC | Explicitly specified by caller | — | configurable |

### 4.4 Assembly Flow

```
1. Request:  AssembledContext.create(purpose=PRE_TRADE, subject="tcs", portfolio="p001", user=jwt)

2. Resolve:  Purpose template expands to atomic context paths:
             ["market:tcs", "market:tcs_price", "market:it_services_sector",
              "market:regulatory_legal", "market:negative_signals", "portfolio:p001:holdings"]

3. ACL:      For each atomic, check:
             - Does user's role have scope access? (CONTEXT_ACL)
             - Does user own this portfolio/client? (OwnershipResolver)
             - Log denied atomics (visible in audit, not in output)

4. Assemble: RuntimeContextAssembler merges granted atomics within budget
             - Respects ProjectionBudget tier (token limit, latency limit)
             - Prioritizes by purpose (PRE_TRADE prioritizes risk signals)

5. Audit:    Write assembly event to AuditLogger (immutable)

6. Deliver:  Return AssembledContext with .to_messages() / .to_dict()
```

## 5. Access Control Layer

### 5.1 Scope-Based ACL

```python
CONTEXT_ACL = {
    "market": {
        Role.FUND_MANAGER:      {Permission.READ},
        Role.COMPLIANCE:        {Permission.READ},
        Role.RESEARCH_ANALYST:  {Permission.READ},
        Role.CLIENT_VIEWER:     {Permission.READ},
        Role.OPERATIONS:        {Permission.READ},
        Role.ADMIN:             {Permission.READ, Permission.WRITE},
    },
    "portfolio": {
        Role.FUND_MANAGER:      {Permission.READ},        # filtered to own
        Role.COMPLIANCE:        {Permission.READ},         # all (global view)
        Role.RESEARCH_ANALYST:  set(),                     # no access
        Role.CLIENT_VIEWER:     set(),                     # no direct access
        Role.OPERATIONS:        {Permission.READ},         # reports only
        Role.ADMIN:             {Permission.READ, Permission.WRITE},
    },
    "client": {
        Role.FUND_MANAGER:      {Permission.READ},        # filtered to own clients
        Role.COMPLIANCE:        {Permission.READ},         # all clients
        Role.CLIENT_VIEWER:     {Permission.READ},         # own data only
        Role.OPERATIONS:        {Permission.READ, Permission.WRITE},
        Role.ADMIN:             {Permission.READ, Permission.WRITE},
    },
}
```

### 5.2 Ownership Resolver

ACL says "FM can read portfolio scope" — but which portfolios?

```python
class OwnershipResolver:
    def can_access(self, user: JWTIdentity, context_path: str) -> bool:
        scope, *parts = parse_context_path(context_path)

        if scope == "market":
            return True  # all market contexts are readable

        if scope == "portfolio":
            portfolio_id = parts[0]
            if user.role in (Role.COMPLIANCE, Role.ADMIN):
                return True  # global access
            return portfolio_id in user.portfolio_ids

        if scope == "client":
            client_id = parts[0]
            if user.role in (Role.COMPLIANCE, Role.ADMIN):
                return True
            if user.role == Role.CLIENT_VIEWER:
                return client_id == user.client_id
            return client_id in user.client_ids

        return False
```

### 5.3 JWT Token Structure

Replace `X-User-Role` header with signed JWT:

```json
{
  "sub": "user_123",
  "role": "fund_manager",
  "tenant": "firm_abc",
  "portfolio_ids": ["p001", "p002", "p003"],
  "client_ids": ["c001", "c002"],
  "permissions": ["read:market", "read:portfolio", "propose_trade"],
  "iat": 1757500000,
  "exp": 1757503600
}
```

Signed with `AICONTEXTDB_JWT_SECRET` (RS256 for production, HS256 for dev). Token lifetime: 1 hour, refresh via `/auth/refresh`.

Backward compatibility: if no JWT present and `X-User-Role` header exists, create an ephemeral identity with that role (dev mode only, disabled in production via `AICONTEXTDB_ENV=production`).

## 6. Audit Trail & Frozen Snapshots

### 6.1 Assembly Audit Event

Every `AssembledContext.create()` writes an immutable audit entry:

```python
{
    "event": "context_assembled",
    "assembled_id": "ac_20260911_103000_pre_trade_tcs",
    "user": "user_123",
    "role": "fund_manager",
    "purpose": "PRE_TRADE",
    "subject": "tcs",
    "portfolio_id": "p001",
    "atomics_requested": ["market:tcs", "market:tcs_price", "portfolio:p001:holdings"],
    "atomics_granted": ["market:tcs", "market:tcs_price", "portfolio:p001:holdings"],
    "atomics_denied": [],
    "budget_tier": "standard",
    "token_count": 2847,
    "timestamp": "2026-09-11T10:30:00Z"
}
```

Uses existing `AuditLogger` with new category `CONTEXT_ASSEMBLY`.

### 6.2 Frozen Context (Trade Decision Record)

When a trade is proposed via `TradeManager.propose_trade()`, the assembled context is frozen:

```python
@dataclass
class FrozenContext:
    frozen_id: str                   # "frozen:{trade_id}"
    trade_id: str
    assembled_id: str                # link to assembly event
    atomics: List[str]               # which contexts were included
    content: Dict                    # the actual data FM saw
    content_hash: str                # SHA-256 of canonical JSON
    frozen_at: datetime
    frozen_by: str                   # user_id
```

Stored at Redis key: `context:frozen:{trade_id}`

**Tamper verification:**
```python
def verify_frozen(frozen: FrozenContext) -> bool:
    canonical = json.dumps(frozen.content, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode()).hexdigest() == frozen.content_hash
```

**SEBI auditor workflow:**
1. Retrieve `context:frozen:{trade_id}`
2. Call `verify_frozen()` to confirm integrity
3. Inspect `.content` to see exactly what signals, prices, news the FM saw
4. Cross-reference `.atomics` to understand information sources

### 6.3 Frozen Context Retention

- Minimum retention: 5 years (SEBI requirement for trade records)
- Frozen contexts are persisted to disk backup alongside trade audit entries
- Redis TTL: never expires for frozen contexts
- Disk backup: `contextsynapse_data/frozen/{trade_id}.json.enc` (encrypted)

## 7. Encryption at Rest

### 7.1 Per-Scope Encryption

| Scope | Encryption | Key Source |
|---|---|---|
| `market:*` | None (public data) | — |
| `portfolio:*` | AES-256 via Fernet | Per-tenant key from `AICONTEXTDB_TENANT_KEY` |
| `client:*` | AES-256 via Fernet | Per-client key derived from tenant key + client_id |
| `frozen:*` | AES-256 + SHA-256 hash | Per-tenant key |

### 7.2 Key Derivation

```python
def derive_client_key(tenant_key: bytes, client_id: str) -> bytes:
    """Derive a per-client encryption key from the tenant master key."""
    import hashlib, base64
    derived = hashlib.pbkdf2_hmac("sha256", tenant_key, client_id.encode(), 100_000)
    return base64.urlsafe_b64encode(derived)
```

### 7.3 Transparent Encryption

Encryption is handled at the context storage layer — callers never see encrypted data:

```python
class ScopedEncryptor:
    def on_write(self, context_path: str, data: Dict) -> bytes:
        scope = parse_scope(context_path)
        if scope in ("portfolio", "client", "frozen"):
            return self._encrypt(data, self._key_for(context_path))
        return serialize(data)  # plaintext for market scope

    def on_read(self, context_path: str, raw: bytes) -> Dict:
        scope = parse_scope(context_path)
        if scope in ("portfolio", "client", "frozen"):
            return self._decrypt(raw, self._key_for(context_path))
        return deserialize(raw)
```

### 7.4 Key Rotation

- Rotation interval: configurable, default 90 days
- Process: re-encrypt all portfolio/client contexts with new key, keep old key for reading frozen snapshots (frozen contexts are never re-encrypted — they use the key from their creation time, stored in key history)
- Key history stored in `CredentialStore` (existing encrypted SQLite)

## 8. Files to Create/Modify

### New Files

| File | Purpose |
|---|---|
| `contextsynapse/context/assembled.py` | `AssembledContext`, `ContextPurpose`, purpose templates, assembly orchestration |
| `contextsynapse/context/acl.py` | `ContextACL`, `OwnershipResolver`, scope permission checks |
| `contextsynapse/context/frozen.py` | `FrozenContext`, snapshot creation, hash verification, retrieval |
| `contextsynapse/security/scoped_encryption.py` | `ScopedEncryptor`, per-scope encrypt/decrypt, key derivation |
| `contextsynapse/security/jwt_auth.py` | `JWTIdentity`, token validation, identity extraction middleware |

### Modified Files

| File | Change |
|---|---|
| `contextsynapse/security/sanitize.py` | Update `validate_namespace()` for hierarchical format (3-4 segments, scope validation) |
| `contextsynapse/core/redis_registry.py` | Support hierarchical `context:{scope}:{name}` keys, scope-aware listing |
| `contextsynapse/api/auth.py` | Wire JWT middleware, backward compat with `X-User-Role` in dev mode |
| `contextsynapse/api/pms_router.py` | Use `AssembledContext` in trade, analysis, briefing endpoints |
| `contextsynapse/pms/trade.py` | Call `assembled.to_snapshot()` in `propose_trade()`, store frozen context |
| `contextsynapse/pms/audit.py` | Add `CONTEXT_ASSEMBLY` audit category |
| `contextsynapse/intelligence/runtime_context.py` | Integrate with ACL checks, called by AssembledContext internally |
| `contextsynapse/context/projection.py` | Accept hierarchical paths, respect budget from AssembledContext |

## 9. Migration Plan

### Phase 1: Namespace Migration (non-breaking)
- Update `validate_namespace()` to accept both flat and hierarchical
- Add `_migrate_flat_to_hierarchical()` in registry — renames `context:tcs` to `context:market:tcs`
- All existing code continues to work (flat names resolve to `market:` scope by default)

### Phase 2: AssembledContext + ACL
- Implement `AssembledContext`, `ContextACL`, `OwnershipResolver`
- Wire into PMS router endpoints
- Add assembly audit logging

### Phase 3: JWT + Frozen Snapshots
- Implement JWT auth middleware (with backward compat)
- Implement `FrozenContext` with hash verification
- Wire into trade proposal flow

### Phase 4: Encryption at Rest
- Implement `ScopedEncryptor` with per-scope key management
- Encrypt portfolio and client contexts
- Add key rotation support

## 10. Testing Strategy

| Test | What It Validates |
|---|---|
| `test_namespace_validation` | Hierarchical format accepted, invalid formats rejected |
| `test_assembled_context_pre_trade` | PRE_TRADE purpose resolves correct atomics |
| `test_acl_fm_own_portfolio` | FM can access own portfolio, denied others |
| `test_acl_compliance_global` | Compliance officer sees all portfolios |
| `test_acl_client_own_data` | Client viewer sees only own data |
| `test_assembly_audit_logged` | Every assembly creates audit entry |
| `test_frozen_context_integrity` | Frozen snapshot hash verifies correctly |
| `test_frozen_context_tamper_detected` | Modified content fails hash check |
| `test_scoped_encryption_roundtrip` | Encrypt portfolio data, decrypt transparently |
| `test_jwt_valid_token` | Valid JWT extracts identity correctly |
| `test_jwt_expired_rejected` | Expired JWT returns 401 |
| `test_jwt_wrong_portfolio_denied` | FM accessing other FM's portfolio returns 403 |
| `test_purpose_template_expansion` | Each purpose expands to correct atomic list |
| `test_dynamic_include_exclude` | Add/remove atomics from assembled context |

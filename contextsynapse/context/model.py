"""Context Model — the 3-layer formal model.

Layer 1: AtomicContext  — single entity with past/present/future
Layer 2: CompositeContext — assembles atomics, .past() = memory
Layer 3: DecisionContext — conclusion + reasoning chain + confidence

Every atomic has temporal depth:
  .past()    → what happened (from time_travel, session_memory)
  .present() → what's happening now (from polyglot puller)
  .future()  → what might happen (from projection, prediction)
  .chain()   → temporal edge traversal (from propagation)

Usage:
    from contextsynapse.context.model import ContextEngine

    engine = ContextEngine(graph_registry)

    # Atomic with temporal depth
    stock = engine.atomic("stock", "tcs")
    stock.present()  → {fusion_score: 0.34, rsi: 62, ...}
    stock.past(days=30) → {price_history: [...], past_fusions: [...]}
    stock.future() → {earnings_in: "3 weeks", projection: ...}

    # Composite (memory = combined past)
    composite = engine.composite("trade_decision", {
        stock="tcs", client_id="cl_abc", portfolio_id="pf_xyz"
    })
    composite.present() → {stock: {...}, client: {...}, portfolio: {...}}
    composite.memory()  → combined past of all atomics
    composite.chain()   → reasoning chain backward through time

    # Decision
    decision = engine.decide(composite)
    decision.conclusion → "BUY"
    decision.confidence → 0.72
    decision.reasoning  → [chain of events that led here]
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Any

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════
# Layer 1: Atomic Context
# ═══════════════════════════════════════════════════════════════

@dataclass
class AtomicContext:
    """A single entity with past, present, future."""

    context_type: str          # "stock", "client", "portfolio", etc.
    key: str                   # "tcs", "cl_abc", "pf_xyz"
    _engine: Any = None        # back-reference to ContextEngine

    # Cached data
    _present_data: dict = field(default_factory=dict)
    _past_data: dict = field(default_factory=dict)
    _future_data: dict = field(default_factory=dict)

    def present(self) -> dict:
        """Current state — from polyglot puller."""
        if not self._present_data and self._engine:
            self._present_data = self._engine._pull_present(self.context_type, self.key)
        return self._present_data

    def past(self, days: int = 30) -> dict:
        """What happened before — from time_travel + memory."""
        if not self._past_data and self._engine:
            self._past_data = self._engine._pull_past(self.context_type, self.key, days)
        return self._past_data

    def future(self) -> dict:
        """What might happen — from projection + prediction."""
        if not self._future_data and self._engine:
            self._future_data = self._engine._pull_future(self.context_type, self.key)
        return self._future_data

    def full(self, days: int = 30) -> dict:
        """All three temporal dimensions."""
        return {
            "type": self.context_type,
            "key": self.key,
            "past": self.past(days),
            "present": self.present(),
            "future": self.future(),
        }

    def to_dict(self) -> dict:
        return {
            "type": self.context_type,
            "key": self.key,
            "present": self._present_data,
            "past": self._past_data,
            "future": self._future_data,
        }


# ═══════════════════════════════════════════════════════════════
# Layer 2: Composite Context
# ═══════════════════════════════════════════════════════════════

@dataclass
class CompositeContext:
    """Multiple atomics assembled for a purpose."""

    purpose: str               # "trade_decision", "client_review"
    atomics: dict[str, AtomicContext] = field(default_factory=dict)
    redacted: list[str] = field(default_factory=list)
    assembled_at: str = ""
    duration_ms: float = 0

    def present(self) -> dict:
        """Current state of all atomics."""
        return {k: a.present() for k, a in self.atomics.items()}

    def memory(self) -> dict:
        """Combined past of all atomics = MEMORY."""
        return {k: a.past() for k, a in self.atomics.items()}

    def prediction(self) -> dict:
        """Combined future of all atomics."""
        return {k: a.future() for k, a in self.atomics.items()}

    def full(self) -> dict:
        """All temporal dimensions for all atomics."""
        return {k: a.full() for k, a in self.atomics.items()}

    def chain(self) -> list[dict]:
        """Temporal edge traversal — reasoning chain backward through time."""
        # Collect all past events across atomics, sort by time
        events = []
        for key, atomic in self.atomics.items():
            past = atomic.past()
            for event in past.get("events", past.get("history", [])):
                if isinstance(event, dict):
                    events.append({**event, "_source": key})
        events.sort(key=lambda e: e.get("timestamp", e.get("date", "")), reverse=True)
        return events

    def to_dict(self) -> dict:
        return {
            "purpose": self.purpose,
            "assembled_at": self.assembled_at,
            "duration_ms": round(self.duration_ms, 1),
            "atomics": {k: a.to_dict() for k, a in self.atomics.items()},
            "redacted": self.redacted,
        }


# ═══════════════════════════════════════════════════════════════
# Layer 3: Decision Context
# ═══════════════════════════════════════════════════════════════

@dataclass
class DecisionContext:
    """Conclusion from a composite — the actionable output."""

    purpose: str
    conclusion: str = ""       # "BUY", "SELL", "HOLD", "ESCALATE", "REBALANCE"
    confidence: float = 0.0    # 0-1
    reasoning: list[dict] = field(default_factory=list)   # the chain
    violations: list[dict] = field(default_factory=list)  # rule violations
    risk: str = ""             # what could go wrong
    memory_note: str = ""      # what to remember for next time
    composite: CompositeContext = None

    def to_dict(self) -> dict:
        return {
            "purpose": self.purpose,
            "conclusion": self.conclusion,
            "confidence": self.confidence,
            "reasoning": self.reasoning,
            "violations": self.violations,
            "risk": self.risk,
            "memory_note": self.memory_note,
        }


# ═══════════════════════════════════════════════════════════════
# Registries — verticals register their types
# ═══════════════════════════════════════════════════════════════

@dataclass
class AtomicTypeDef:
    """Definition of an atomic context type."""
    name: str                  # "stock", "client"
    label: str                 # "Stock", "Client"
    polyglot: list[str] = field(default_factory=list)  # ["graph", "columnar", "redis", "pg"]
    sensors: list[str] = field(default_factory=list)    # sensor types for this atomic
    on_create: str = ""        # skill to run on creation (e.g., "onboard-stock")
    vertical: str = ""


@dataclass
class CompositeTypeDef:
    """Definition of a composite context type."""
    name: str                  # "trade_decision"
    label: str                 # "Trade Decision"
    description: str = ""
    atomics: list[dict] = field(default_factory=list)  # [{type, key_pattern, temporal}]
    decision_type: str = ""    # which decision type to produce
    vertical: str = ""


class ContextRegistry:
    """Central registry for context types — verticals register here."""

    def __init__(self):
        self._atomic_types: dict[str, AtomicTypeDef] = {}
        self._composite_types: dict[str, CompositeTypeDef] = {}

    def register_atomic(self, typedef: AtomicTypeDef):
        self._atomic_types[typedef.name] = typedef
        logger.info("[CTX-REG] Atomic: %s (%s)", typedef.name, typedef.label)

    def register_composite(self, typedef: CompositeTypeDef):
        self._composite_types[typedef.name] = typedef
        logger.info("[CTX-REG] Composite: %s (%s)", typedef.name, typedef.label)

    def get_atomic(self, name: str) -> AtomicTypeDef | None:
        return self._atomic_types.get(name)

    def get_composite(self, name: str) -> CompositeTypeDef | None:
        return self._composite_types.get(name)

    def list_atomics(self, vertical: str = "") -> list[dict]:
        return [
            {"name": t.name, "label": t.label, "polyglot": t.polyglot,
             "sensors": t.sensors, "vertical": t.vertical}
            for t in self._atomic_types.values()
            if not vertical or t.vertical == vertical or t.vertical == ""
        ]

    def list_composites(self, vertical: str = "") -> list[dict]:
        return [
            {"name": t.name, "label": t.label, "description": t.description,
             "atomics_count": len(t.atomics), "vertical": t.vertical}
            for t in self._composite_types.values()
            if not vertical or t.vertical == vertical or t.vertical == ""
        ]


_registry: ContextRegistry | None = None

def get_context_registry() -> ContextRegistry:
    global _registry
    if _registry is None:
        _registry = ContextRegistry()
    return _registry


# ═══════════════════════════════════════════════════════════════
# Context Engine — the unified API
# ═══════════════════════════════════════════════════════════════

class ContextEngine:
    """Unified context engine — atomic, composite, decision with temporal depth."""

    def __init__(self, graph_registry=None):
        self._graph_registry = graph_registry
        self._registry = get_context_registry()

    def atomic(self, context_type: str, key: str) -> AtomicContext:
        """Get an atomic context with temporal depth."""
        return AtomicContext(context_type=context_type, key=key, _engine=self)

    def composite(
        self,
        purpose: str,
        params: dict,
        requester: dict = None,
        temporal: list[str] = None,
    ) -> CompositeContext:
        """Assemble a composite context."""
        start = time.time()
        temporal = temporal or ["present"]

        # Get composite definition
        typedef = self._registry.get_composite(purpose)
        if not typedef:
            # Fallback to assembly.py PURPOSE_TEMPLATES
            return self._assemble_legacy(purpose, params, requester, temporal)

        # Build atomics
        atomics = {}
        redacted = []

        for atomic_def in typedef.atomics:
            ctx_type = atomic_def.get("type", "")
            key_pattern = atomic_def.get("key", "")
            try:
                key = key_pattern.format(**params)
            except KeyError:
                continue
            if not key or "{" in key:
                continue

            ctx_id = f"{ctx_type}:{key}"
            atom = self.atomic(ctx_type, key)

            # Access check
            if requester:
                present = atom.present()
                from .assembly import can_access_context, Requester
                req = Requester.from_user(requester) if isinstance(requester, dict) else requester
                if not can_access_context(req, present):
                    redacted.append(ctx_id)
                    continue

            # Pull requested temporal dimensions
            if "present" in temporal:
                atom.present()
            if "past" in temporal:
                atom.past()
            if "future" in temporal:
                atom.future()

            atomics[ctx_id] = atom

        return CompositeContext(
            purpose=purpose,
            atomics=atomics,
            redacted=redacted,
            assembled_at=datetime.now(timezone.utc).isoformat(),
            duration_ms=(time.time() - start) * 1000,
        )

    def decide(self, composite: CompositeContext) -> DecisionContext:
        """Produce a decision from a composite."""
        # Use the existing fusion functions from assembly.py
        try:
            from .assembly import ContextAssemblyEngine
            engine = ContextAssemblyEngine(self._graph_registry)

            # Extract present data for fusion
            contexts = {k: a.present() for k, a in composite.atomics.items()}
            params = {}
            for k in composite.atomics:
                parts = k.split(":")
                if len(parts) == 2:
                    params[parts[0]] = parts[1]

            fusion = engine._compute_fusion(
                composite.purpose.replace("_decision", "").replace("_review", ""),
                contexts, params,
            )

            # Build reasoning from chain
            chain = composite.chain()

            return DecisionContext(
                purpose=composite.purpose,
                conclusion=fusion.get("decision", fusion.get("health", fusion.get("status", "HOLD"))),
                confidence=abs(fusion.get("fusion_score", fusion.get("confidence", 0.5))),
                reasoning=chain[:10],
                violations=fusion.get("violations", []),
                risk=fusion.get("risk", ""),
                memory_note=f"Decision: {fusion.get('decision', '?')} at {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')}",
                composite=composite,
            )
        except Exception as e:
            return DecisionContext(
                purpose=composite.purpose,
                conclusion="ERROR",
                confidence=0,
                reasoning=[{"error": str(e)}],
            )

    def _assemble_legacy(self, purpose, params, requester, temporal):
        """Fallback to assembly.py for unregistered composites."""
        from .assembly import ContextAssemblyEngine, Requester as AsmRequester
        engine = ContextAssemblyEngine(self._graph_registry)
        result = engine.assemble(purpose, requester or {}, params)

        # Convert to CompositeContext
        atomics = {}
        for ctx_id, data in result.contexts.items():
            parts = ctx_id.split(":", 1)
            ctx_type = parts[0] if parts else "unknown"
            key = parts[1] if len(parts) > 1 else ctx_id
            atom = AtomicContext(context_type=ctx_type, key=key, _engine=self)
            atom._present_data = data
            if "past" in temporal:
                atom._past_data = self._pull_past(ctx_type, key, 30)
            if "future" in temporal:
                atom._future_data = self._pull_future(ctx_type, key)
            atomics[ctx_id] = atom

        return CompositeContext(
            purpose=purpose,
            atomics=atomics,
            redacted=result.redacted,
            assembled_at=result.assembled_at,
            duration_ms=result.duration_ms,
        )

    # ── Temporal pullers ─────────────────────────────────────────

    def _pull_present(self, context_type: str, key: str) -> dict:
        """Pull current state from polyglot stores."""
        from .assembly import PolyglotPuller
        puller = PolyglotPuller(self._graph_registry)
        return puller.pull(context_type, key)

    def _pull_past(self, context_type: str, key: str, days: int = 30) -> dict:
        """Pull historical data — price history, past decisions, patterns."""
        past = {"_temporal": "past", "_days": days}

        if context_type == "stock":
            # Price history from yfinance (columnar)
            try:
                import yfinance as yf
                ticker = f"{key.upper()}.NS"
                hist = yf.Ticker(ticker).history(period=f"{days}d", interval="1d")
                if not hist.empty:
                    past["price_history"] = [
                        {"date": idx.strftime("%Y-%m-%d"),
                         "close": round(float(row["Close"]), 2),
                         "volume": int(row["Volume"])}
                        for idx, row in list(hist.iterrows())[-10:]  # last 10 points
                    ]
                    past["price_30d_ago"] = round(float(hist.iloc[0]["Close"]), 2)
                    past["price_change_pct"] = round(
                        (float(hist.iloc[-1]["Close"]) - float(hist.iloc[0]["Close"]))
                        / float(hist.iloc[0]["Close"]) * 100, 2
                    )
            except Exception:
                pass

            # Past fusion scores from Redis
            try:
                import redis, os
                r = redis.Redis.from_url(
                    os.environ.get("AICONTEXTDB_REDIS_URL", "redis://localhost:6379/0"),
                    socket_connect_timeout=1, socket_timeout=1,
                )
                cached = r.get(f"fusion:{key}:7")
                if cached:
                    past["last_fusion"] = json.loads(cached).get("fused_score", 0)
            except Exception:
                pass

        elif context_type == "client":
            # Client history from PG
            try:
                from contextsynapse.db.pms_client import PmsClientManager
                client = PmsClientManager().get(key)
                if client:
                    past["onboarded"] = str(client.get("onboarded_date", ""))
                    past["mandate_type"] = client.get("mandate_type", "")
                    past["initial_investment"] = float(client.get("investment_amount", 0))
            except Exception:
                pass

        elif context_type == "portfolio":
            # Portfolio history
            try:
                from contextsynapse.db.pms_portfolio import PmsPortfolioManager
                pf = PmsPortfolioManager().get(key)
                if pf:
                    past["inception"] = str(pf.get("inception_date", ""))
                    past["initial_nav"] = 100.0
                    past["current_nav"] = float(pf.get("nav", 100))
            except Exception:
                pass

        elif context_type in ("fii", "dii"):
            try:
                from contextsynapse.connectors.financial.fii_dii_live import FIIDIIContext
                ctx = FIIDIIContext(self._graph_registry)
                cat = "FII" if context_type == "fii" else "DII"
                past["streak"] = ctx.get_streak(cat)
                flow = ctx.get_flow_summary(days)
                past["net_flow"] = flow.get(context_type, {}).get("net_flow_cr", 0)
            except Exception:
                pass

        return past

    def _pull_future(self, context_type: str, key: str) -> dict:
        """Pull projections and predictions."""
        future = {"_temporal": "future"}

        if context_type == "stock":
            # Upcoming events
            future["upcoming"] = []
            # Check if earnings season
            try:
                from contextsynapse.db.stock_master import StockMaster
                master = StockMaster().get(key.upper())
                if master:
                    future["sector"] = master.get("sector", "")
                    future["market_cap"] = master.get("market_cap_category", "")
            except Exception:
                pass

        elif context_type == "portfolio":
            # Projected drift, rebalance needs
            try:
                from contextsynapse.db.pms_portfolio import PmsPortfolioManager
                pf = PmsPortfolioManager().get(key)
                if pf:
                    cash_pct = float(pf.get("cash_balance", 0)) / max(float(pf.get("total_value", 1)), 1) * 100
                    future["cash_runway_months"] = round(cash_pct / 2, 1)  # rough estimate
                    future["max_single_stock"] = float(pf.get("max_single_stock_pct", 25))
            except Exception:
                pass

        elif context_type == "client":
            # Churn risk, next review
            try:
                from contextsynapse.db.pms_client import PmsClientManager
                client = PmsClientManager().get(key)
                if client:
                    tenure = 0
                    try:
                        from datetime import date
                        onboarded = client.get("onboarded_date")
                        if onboarded:
                            tenure = (date.today() - date.fromisoformat(str(onboarded))).days // 30
                    except Exception:
                        pass
                    future["tenure_months"] = tenure
                    future["next_review"] = "Q4 2026"
            except Exception:
                pass

        return future

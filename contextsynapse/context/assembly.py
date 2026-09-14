"""Context Assembly Engine — polyglot data pull + access filtering + fusion output.

The single entry point for composing decision-ready context from multiple
atomic contexts stored across the polyglot graph (CSR, columnar, document,
vector, PostgreSQL, Redis).

Flow:
  1. Resolve purpose → list of atomic context paths
  2. For each atomic → check access (visibility, roles, groups, agents)
  3. For accessible atomics → pull data from optimal store
  4. Assemble into composite context
  5. Optionally run rules + compute fusion output
  6. Return decision-ready context (with redacted markers for denied)

Usage:
    engine = ContextAssemblyEngine(graph_registry)

    # Trade decision — assembles Stock + Client + Portfolio + Rules
    result = engine.assemble(
        purpose="trade_decision",
        requester={"user_id": "...", "roles": ["fund_manager"], "scoped_ids": [...]},
        params={"stock": "TCS", "client_id": "cl_abc", "portfolio_id": "pf_xyz"},
    )
    # result.contexts → dict of pulled data per atomic
    # result.fusion → decision-ready output
    # result.redacted → list of contexts user couldn't see
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════
# Access Model
# ═══════════════════════════════════════════════════════════════

VISIBILITY_PUBLIC = "public"  # all authenticated users
VISIBILITY_SCOPED = "scoped"  # only users in access_groups or scoped_ids
VISIBILITY_PRIVATE = "private"  # only owner
VISIBILITY_CONFIDENTIAL = "confidential"  # named roles only

# Access groups — team-level
ACCESS_GROUPS = {
    "pms_investment_team": ["cio", "fund_manager", "research_analyst"],
    "pms_compliance": ["cio", "compliance_officer", "risk_manager"],
    "pms_operations": ["operations"],
    "pms_client_facing": ["relationship_manager"],
}


@dataclass
class Requester:
    """Who is requesting this context assembly."""

    user_id: str = ""
    roles: list[str] = field(default_factory=list)
    scoped_ids: list[str] = field(default_factory=list)  # assigned client/portfolio IDs
    delegated_ids: list[str] = field(default_factory=list)
    agent_id: str = ""  # if an agent is requesting

    @property
    def all_scoped_ids(self) -> list[str]:
        return list(set(self.scoped_ids + self.delegated_ids))

    @property
    def is_global(self) -> bool:
        return any(r in ("admin", "cio", "compliance_officer", "risk_manager") for r in self.roles)

    @classmethod
    def from_user(cls, user: dict) -> Requester:
        """Build from login response user dict."""
        return cls(
            user_id=user.get("user_id", user.get("id", "")),
            roles=user.get("roles", [user.get("role", "")]),
            scoped_ids=user.get("scoped_ids", []),
            delegated_ids=user.get("delegated_ids", []),
            agent_id=user.get("agent_id", ""),
        )


def can_access_context(requester: Requester, context_meta: dict) -> bool:
    """Check if requester can access a context node based on its access properties."""
    visibility = context_meta.get("_visibility", VISIBILITY_PUBLIC)

    if visibility == VISIBILITY_PUBLIC:
        return True

    if visibility == VISIBILITY_PRIVATE:
        return requester.user_id == context_meta.get("_owner", "")

    if visibility == VISIBILITY_CONFIDENTIAL:
        allowed_roles = set(context_meta.get("_access_roles", []))
        return bool(allowed_roles & set(requester.roles))

    if visibility == VISIBILITY_SCOPED:
        # Check roles
        allowed_roles = set(context_meta.get("_access_roles", []))
        if allowed_roles & set(requester.roles):
            return True
        # Check owner
        if requester.user_id == context_meta.get("_owner", ""):
            return True
        # Check access groups
        for group in context_meta.get("_access_groups", []):
            group_roles = ACCESS_GROUPS.get(group, [])
            if set(group_roles) & set(requester.roles):
                return True
        # Check agent
        if requester.agent_id and requester.agent_id in context_meta.get("_access_agents", []):
            return True
        # Check scoped IDs — direct match (client_id in scoped_ids)
        entity_id = context_meta.get("_pg_id", context_meta.get("_entity_id", ""))
        if entity_id and entity_id in requester.all_scoped_ids:
            return True
        # Cascade: portfolio owned by a scoped client → FM gets access
        owner_id = context_meta.get("_owner", "")
        if owner_id and owner_id in requester.all_scoped_ids:
            return True
        # Cascade: context linked to a scoped client via client_id field
        client_id = context_meta.get("client_id", "")
        if client_id and client_id in requester.all_scoped_ids:
            return True
        return bool(requester.is_global)

    return True


# ═══════════════════════════════════════════════════════════════
# Purpose Templates (what atomics each purpose needs)
# ═══════════════════════════════════════════════════════════════

PURPOSE_TEMPLATES: dict[str, dict] = {
    "trade_decision": {
        "description": "Should I buy/sell this stock for this client?",
        "atomics": [
            {"type": "stock", "key": "{stock}", "visibility": "public"},
            {"type": "client", "key": "{client_id}", "visibility": "scoped"},
            {"type": "portfolio", "key": "{portfolio_id}", "visibility": "scoped"},
            {"type": "sector", "key": "{sector}", "visibility": "public"},
            {"type": "rules", "key": "{portfolio_id}", "visibility": "scoped"},
        ],
        "fusion_type": "trade",
    },
    "client_review": {
        "description": "Quarterly client review — performance, allocation, risks",
        "atomics": [
            {"type": "client", "key": "{client_id}", "visibility": "scoped"},
            {"type": "portfolio", "key": "{portfolio_id}", "visibility": "scoped"},
            {"type": "performance", "key": "{portfolio_id}", "visibility": "scoped"},
        ],
        "fusion_type": "client",
    },
    "stock_analysis": {
        "description": "Deep dive on a stock — fusion, sensors, signals",
        "atomics": [
            {"type": "stock", "key": "{stock}", "visibility": "public"},
            {"type": "sector", "key": "{sector}", "visibility": "public"},
            {"type": "macro", "key": "india_economy", "visibility": "public"},
        ],
        "fusion_type": "stock",
    },
    "risk_assessment": {
        "description": "Portfolio risk — drift, concentration, compliance",
        "atomics": [
            {"type": "portfolio", "key": "{portfolio_id}", "visibility": "scoped"},
            {"type": "rules", "key": "{portfolio_id}", "visibility": "scoped"},
            {"type": "holdings_fusion", "key": "{portfolio_id}", "visibility": "scoped"},
        ],
        "fusion_type": "portfolio",
    },
    "morning_brief": {
        "description": "FM's daily morning brief — market, sectors, alerts",
        "atomics": [
            {"type": "macro", "key": "india_economy", "visibility": "public"},
            {"type": "macro", "key": "global_markets", "visibility": "public"},
            {"type": "sector_rotation", "key": "all", "visibility": "public"},
            {"type": "alerts", "key": "{user_id}", "visibility": "scoped"},
        ],
        "fusion_type": "brief",
    },
    "rebalance_plan": {
        "description": "Generate rebalancing recommendations",
        "atomics": [
            {"type": "portfolio", "key": "{portfolio_id}", "visibility": "scoped"},
            {"type": "holdings_fusion", "key": "{portfolio_id}", "visibility": "scoped"},
            {"type": "rules", "key": "{portfolio_id}", "visibility": "scoped"},
        ],
        "fusion_type": "rebalance",
    },
}


# ═══════════════════════════════════════════════════════════════
# Data Pullers — fetch from each polyglot store
# ═══════════════════════════════════════════════════════════════


class PolyglotPuller:
    """Pulls data from the right store based on context type."""

    def __init__(self, graph_registry=None):
        self._registry = graph_registry

    def pull(self, context_type: str, key: str) -> dict:
        """Pull data for a context type + key. Routes to optimal store."""
        pullers = {
            "stock": self._pull_stock,
            "client": self._pull_client,
            "portfolio": self._pull_portfolio,
            "sector": self._pull_sector,
            "macro": self._pull_macro,
            "rules": self._pull_rules,
            "performance": self._pull_performance,
            "holdings_fusion": self._pull_holdings_fusion,
            "sector_rotation": self._pull_sector_rotation,
            "alerts": self._pull_alerts,
        }
        puller = pullers.get(context_type, self._pull_generic)
        try:
            return puller(key)
        except Exception as e:
            logger.warning("[ASSEMBLY] Pull failed for %s:%s — %s", context_type, key, e)
            return {"error": str(e), "_type": context_type, "_key": key}

    def _pull_stock(self, key: str) -> dict:
        """Pull stock context: fusion (cache-first) + KB + master data."""
        data = {"_type": "stock", "_key": key, "_visibility": "public"}

        # Fusion — cache first (Redis), skip expensive recompute
        try:
            import json as _json
            import os

            import redis

            r = redis.Redis.from_url(
                os.environ.get("AICONTEXTDB_REDIS_URL", "redis://localhost:6379/0"),
                socket_connect_timeout=1,
                socket_timeout=2,
            )
            cached = r.get(f"fusion:{key}:7")
            if cached:
                fusion = _json.loads(cached)
                data["fusion_score"] = fusion.get("fused_score", 0)
                data["direction"] = fusion.get("direction", "neutral")
                data["sensor_summary"] = fusion.get("sensor_summary", {})
                data["event_signals"] = fusion.get("event_signals", [])[:5]
                bs = fusion.get("behavioral_state", {})
                data["behavioral_state"] = bs.get("state") if isinstance(bs, dict) else bs
                data["_fusion_source"] = "cache"
            else:
                data["fusion_score"] = 0
                data["direction"] = "unknown"
                data["_fusion_source"] = "none"
        except Exception:
            data["fusion_score"] = 0
            data["direction"] = "unknown"
            data["_fusion_source"] = "error"

        # Entity KB from graph
        if self._registry:
            try:
                from plugins.stock_analysis.entity_kb import EntityKB

                kb = EntityKB(self._registry)
                data["aliases"] = kb.get_all_aliases(key)
            except Exception:
                pass

        # Stock master from PostgreSQL
        try:
            from contextsynapse.db.stock_master import StockMaster

            sm = StockMaster()
            master = sm.get(key.upper().replace(".NS", "").replace(".BO", ""))
            if master:
                data["company_name"] = master.get("company_name", "")
                data["sector"] = master.get("sector", "")
                data["market_cap"] = master.get("market_cap_category", "")
        except Exception:
            pass

        return data

    def _pull_client(self, key: str) -> dict:
        """Pull client context from PostgreSQL."""
        data = {"_type": "client", "_key": key, "_visibility": "scoped", "_pg_table": "pms_clients", "_pg_id": key}
        try:
            from contextsynapse.db.pms_client import PmsClientManager

            client = PmsClientManager().get(key)
            if client:
                data["name"] = client.get("name", "")
                data["risk_profile"] = client.get("risk_profile", "")
                data["mandate_type"] = client.get("mandate_type", "")
                data["investment_amount"] = float(client.get("investment_amount", 0))
                data["constraints"] = client.get("constraints", [])
                data["benchmark"] = client.get("benchmark", "")
                data["kyc_status"] = client.get("kyc_status", "")
                data["_owner"] = client.get("relationship_manager", "")
        except Exception as e:
            data["error"] = str(e)
        return data

    def _pull_portfolio(self, key: str) -> dict:
        """Pull portfolio context — PG master + graph holdings."""
        data = {
            "_type": "portfolio",
            "_key": key,
            "_visibility": "scoped",
            "_pg_table": "pms_portfolios",
            "_pg_id": key,
        }
        try:
            from contextsynapse.db.pms_portfolio import PmsPortfolioManager

            pf = PmsPortfolioManager().get(key)
            if pf:
                data["name"] = pf.get("name", "")
                data["strategy"] = pf.get("strategy", "")
                data["benchmark"] = pf.get("benchmark", "")
                data["total_value"] = float(pf.get("total_value", 0))
                data["cash_balance"] = float(pf.get("cash_balance", 0))
                data["max_single_stock_pct"] = float(pf.get("max_single_stock_pct", 25))
                data["max_sector_pct"] = float(pf.get("max_sector_pct", 40))
                data["client_id"] = pf.get("client_id", "")
                data["_owner"] = pf.get("client_id", "")
        except Exception as e:
            data["error"] = str(e)

        # Holdings from graph
        if self._registry:
            try:
                from verticals.pms.backend.portfolio import PortfolioManager

                db = self._registry.get_graph("pms_data", load_if_missing=True)
                if db:
                    mgr = PortfolioManager(db)
                    holdings = mgr.get_holdings(key)
                    data["holdings"] = holdings
                    data["holdings_count"] = len(holdings)
            except Exception:
                pass

        return data

    def _pull_sector(self, key: str) -> dict:
        """Pull sector context — aggregated from stocks in sector."""
        return {"_type": "sector", "_key": key, "_visibility": "public", "sector": key}

    def _pull_macro(self, key: str) -> dict:
        """Pull macro context from graph."""
        return {"_type": "macro", "_key": key, "_visibility": "public"}

    def _pull_rules(self, key: str) -> dict:
        """Pull compliance rules for a portfolio from PostgreSQL."""
        data = {
            "_type": "rules",
            "_key": key,
            "_visibility": "scoped",
            "_access_groups": ["pms_investment_team", "pms_compliance"],
        }
        try:
            from contextsynapse.db.rules import RulesStore

            store = RulesStore()
            rules = store.list_rules(vertical="pms", enabled=True)
            data["rules"] = rules
            data["total_rules"] = len(rules)
        except Exception as e:
            data["error"] = str(e)

        # Link to portfolio's client for cascading access
        try:
            from contextsynapse.db.pms_portfolio import PmsPortfolioManager

            pf = PmsPortfolioManager().get(key)
            if pf:
                data["client_id"] = pf.get("client_id", "")
        except Exception:
            pass
        return data

    def _pull_performance(self, key: str) -> dict:
        """Pull portfolio performance — columnar data."""
        return {"_type": "performance", "_key": key, "_visibility": "scoped"}

    def _pull_holdings_fusion(self, key: str) -> dict:
        """Pull fusion scores for all holdings in a portfolio."""
        data = {"_type": "holdings_fusion", "_key": key, "_visibility": "scoped", "holdings_fusion": []}

        # Get portfolio holdings, then fuse each stock
        portfolio_data = self._pull_portfolio(key)
        holdings = portfolio_data.get("holdings", [])

        for h in holdings[:20]:  # cap at 20 to avoid timeout
            symbol = h.get("stock_symbol", "")
            if not symbol:
                continue
            stock_data = self._pull_stock(symbol.replace(".NS", "").replace(".BO", "").lower())
            data["holdings_fusion"].append(
                {
                    "stock": symbol,
                    "weight": h.get("weight", 0),
                    "fusion_score": stock_data.get("fusion_score", 0),
                    "direction": stock_data.get("direction", "neutral"),
                }
            )

        return data

    def _pull_sector_rotation(self, key: str) -> dict:
        return {"_type": "sector_rotation", "_key": key, "_visibility": "public"}

    def _pull_alerts(self, key: str) -> dict:
        return {"_type": "alerts", "_key": key, "_visibility": "scoped"}

    def _pull_generic(self, key: str) -> dict:
        return {"_type": "generic", "_key": key, "_visibility": "public"}


# ═══════════════════════════════════════════════════════════════
# Assembly Result
# ═══════════════════════════════════════════════════════════════


@dataclass
class AssemblyResult:
    """The output of context assembly."""

    purpose: str
    requester_id: str
    requester_roles: list[str]
    assembled_at: str = ""
    duration_ms: float = 0

    # Data
    contexts: dict[str, dict] = field(default_factory=dict)  # type:key → pulled data
    redacted: list[str] = field(default_factory=list)  # type:key → access denied

    # Fusion output (purpose-specific)
    fusion: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if not self.assembled_at:
            self.assembled_at = datetime.now(timezone.utc).isoformat()

    def to_dict(self) -> dict:
        return {
            "purpose": self.purpose,
            "requester": self.requester_id,
            "roles": self.requester_roles,
            "assembled_at": self.assembled_at,
            "duration_ms": round(self.duration_ms, 1),
            "contexts_count": len(self.contexts),
            "redacted_count": len(self.redacted),
            "contexts": self.contexts,
            "redacted": self.redacted,
            "fusion": self.fusion,
        }


# ═══════════════════════════════════════════════════════════════
# Assembly Engine
# ═══════════════════════════════════════════════════════════════


class ContextAssemblyEngine:
    """Assembles composite contexts from polyglot atomics with access control."""

    def __init__(self, graph_registry=None):
        self._registry = graph_registry
        self._puller = PolyglotPuller(graph_registry)

    def assemble(
        self,
        purpose: str,
        requester: dict | Requester,
        params: dict = None,
    ) -> AssemblyResult:
        """Assemble a composite context for a purpose.

        Args:
            purpose: "trade_decision", "client_review", "stock_analysis", etc.
            requester: user dict from login or Requester object
            params: {stock, client_id, portfolio_id, sector, user_id}
        """
        start = time.time()
        params = params or {}

        # Build requester
        if isinstance(requester, dict):
            req = Requester.from_user(requester)
        else:
            req = requester

        # Get purpose template
        template = PURPOSE_TEMPLATES.get(purpose)
        if not template:
            return AssemblyResult(
                purpose=purpose,
                requester_id=req.user_id,
                requester_roles=req.roles,
                fusion={"error": f"Unknown purpose: {purpose}"},
            )

        result = AssemblyResult(
            purpose=purpose,
            requester_id=req.user_id,
            requester_roles=req.roles,
        )

        # Auto-resolve sector from stock if not provided
        if "{sector}" in str(template) and "sector" not in params:
            stock = params.get("stock", "")
            if stock:
                try:
                    from plugins.stock_analysis.entity_kb import STOCK_SECTORS

                    params["sector"] = STOCK_SECTORS.get(stock.lower(), "unknown")
                except Exception:
                    params["sector"] = "unknown"

        # Resolve and pull each atomic
        for atomic_def in template.get("atomics", []):
            ctx_type = atomic_def["type"]
            key_pattern = atomic_def["key"]

            # Resolve key from params
            try:
                key = key_pattern.format(**params)
            except KeyError:
                continue  # missing param, skip this atomic

            if not key or "{" in key:
                continue

            context_id = f"{ctx_type}:{key}"

            # Pull data
            data = self._puller.pull(ctx_type, key)

            # Apply default visibility from template
            if "_visibility" not in data:
                data["_visibility"] = atomic_def.get("visibility", "public")

            # Access check
            if can_access_context(req, data):
                # Strip internal access metadata from output
                clean = {k: v for k, v in data.items() if not k.startswith("_access") and k != "_owner"}
                result.contexts[context_id] = clean
            else:
                result.redacted.append(context_id)

        # Compute fusion output
        result.fusion = self._compute_fusion(
            template.get("fusion_type", "generic"),
            result.contexts,
            params,
        )

        result.duration_ms = (time.time() - start) * 1000
        return result

    def list_purposes(self) -> list[dict]:
        """List available assembly purposes."""
        return [
            {
                "purpose": k,
                "description": v["description"],
                "atomics": len(v["atomics"]),
                "fusion_type": v["fusion_type"],
            }
            for k, v in PURPOSE_TEMPLATES.items()
        ]

    def _compute_fusion(self, fusion_type: str, contexts: dict, params: dict) -> dict:
        """Compute purpose-specific fusion output from assembled contexts."""
        if fusion_type == "trade":
            return self._fuse_trade(contexts, params)
        elif fusion_type == "stock":
            return self._fuse_stock(contexts, params)
        elif fusion_type == "client":
            return self._fuse_client(contexts, params)
        elif fusion_type == "portfolio":
            return self._fuse_portfolio(contexts, params)
        elif fusion_type == "rebalance":
            return self._fuse_rebalance(contexts, params)
        return {}

    def _fuse_trade(self, contexts: dict, params: dict) -> dict:
        """Trade decision fusion — combines stock + client + portfolio + rules."""
        stock_key = f"stock:{params.get('stock', '')}"
        client_key = f"client:{params.get('client_id', '')}"
        portfolio_key = f"portfolio:{params.get('portfolio_id', '')}"
        rules_key = f"rules:{params.get('portfolio_id', '')}"

        stock = contexts.get(stock_key, {})
        client = contexts.get(client_key, {})
        portfolio = contexts.get(portfolio_key, {})
        contexts.get(rules_key, {})

        fusion_score = stock.get("fusion_score", 0)
        direction = stock.get("direction", "neutral")

        # Check constraints
        violations = []
        constraints = client.get("constraints", [])
        stock_sector = stock.get("sector", "").lower()
        for c in constraints if isinstance(constraints, list) else []:
            excluded = c.replace("no_", "").lower()
            if excluded in stock_sector:
                violations.append(f"Client mandate excludes {excluded} sector")

        # Check concentration
        portfolio.get("max_single_stock_pct", 25)
        # TODO: compute post-trade weight

        decision = "HOLD"
        if fusion_score > 0.15 and not violations:
            decision = "BUY"
        elif fusion_score < -0.15:
            decision = "SELL"
        if violations:
            decision = "BLOCKED"

        return {
            "decision": decision,
            "fusion_score": fusion_score,
            "direction": direction,
            "stock": params.get("stock", ""),
            "client": client.get("name", ""),
            "portfolio": portfolio.get("name", ""),
            "risk_profile": client.get("risk_profile", ""),
            "violations": violations,
            "confidence": abs(fusion_score),
        }

    def _fuse_stock(self, contexts: dict, params: dict) -> dict:
        stock_key = f"stock:{params.get('stock', '')}"
        stock = contexts.get(stock_key, {})
        return {
            "fusion_score": stock.get("fusion_score", 0),
            "direction": stock.get("direction", "neutral"),
            "company": stock.get("company_name", ""),
            "sector": stock.get("sector", ""),
            "sensor_summary": stock.get("sensor_summary", {}),
        }

    def _fuse_client(self, contexts: dict, params: dict) -> dict:
        client_key = f"client:{params.get('client_id', '')}"
        client = contexts.get(client_key, {})
        return {
            "name": client.get("name", ""),
            "status": "OK" if client.get("kyc_status") == "verified" else "REVIEW",
            "aum": client.get("investment_amount", 0),
            "risk": client.get("risk_profile", ""),
        }

    def _fuse_portfolio(self, contexts: dict, params: dict) -> dict:
        pf_key = f"portfolio:{params.get('portfolio_id', '')}"
        pf = contexts.get(pf_key, {})
        return {
            "name": pf.get("name", ""),
            "total_value": pf.get("total_value", 0),
            "holdings": pf.get("holdings_count", 0),
            "health": "HEALTHY",
        }

    def _fuse_rebalance(self, contexts: dict, params: dict) -> dict:
        hf_key = f"holdings_fusion:{params.get('portfolio_id', '')}"
        hf = contexts.get(hf_key, {})
        recommendations = []
        for h in hf.get("holdings_fusion", []):
            if h.get("fusion_score", 0) < -0.2:
                recommendations.append(
                    {
                        "action": "TRIM",
                        "stock": h["stock"],
                        "reason": f"Bearish fusion ({h['fusion_score']:.2f})",
                    }
                )
            elif h.get("fusion_score", 0) > 0.3 and h.get("weight", 0) < 10:
                recommendations.append(
                    {
                        "action": "INCREASE",
                        "stock": h["stock"],
                        "reason": f"Bullish fusion ({h['fusion_score']:.2f}), underweight",
                    }
                )
        return {"recommendations": recommendations, "total": len(recommendations)}

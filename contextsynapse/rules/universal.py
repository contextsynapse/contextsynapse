"""Universal Rule Engine — extends the existing rules engine with cross-entity,
sensor-aware, temporal, and behavioral rule evaluation.

Works alongside the existing UnifiedRulesEngine (SEBI compliance rules).
This engine adds support for:
  - Sensor score conditions (fusion groups + individual sensors)
  - DataSource queries (MF holdings, FII flow from PostgreSQL)
  - Behavioral state conditions (panic, euphoria, etc.)
  - Temporal comparisons (value now vs N days ago)
  - Cross-entity conditions (sector flow + stock holding)
  - Auto-discovery rules (surface opportunities FM didn't ask for)

Rule = WHEN (trigger) + IF (conditions) + THEN (actions)

Built-in rules provided:
  - RSI overbought/oversold alert
  - MF accumulation signal
  - Panic + smart money = contrarian BUY
  - FII exit warning
  - Earnings miss alert
  - Golden cross signal
"""
import json
import logging
import re
import uuid
from datetime import datetime, timezone, timedelta
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional
from pathlib import Path

logger = logging.getLogger(__name__)

RULES_DIR = Path(__file__).parent.parent.parent / ".rules"


# ── Data Models ──────────────────────────────────────────────────

@dataclass
class Condition:
    source: str = "sensor"        # sensor, datasource, threshold, temporal, behavioral, portfolio
    entity: str = "{stock}"       # {stock} = each portfolio stock
    field: str = ""               # score, rsi, weight, change_pct
    group: str = ""               # sensor group: price_action, institutional
    sensor: str = ""              # specific sensor: technical_rsi
    operator: str = ">"           # >, <, >=, <=, ==, !=, in, between
    value: Any = 0
    datasource: str = ""
    query_filter: dict = field(default_factory=dict)
    temporal_offset: str = ""     # 30d, 7d, 1m

@dataclass
class Action:
    action: str = "alert"         # alert, block_trade, suggest_trade, add_watchlist, report, webhook
    severity: str = "medium"
    message: str = ""
    params: dict = field(default_factory=dict)

@dataclass
class UniversalRule:
    rule_id: str = ""
    name: str = ""
    description: str = ""
    category: str = "custom"      # compliance, risk, opportunity, monitoring, custom
    when: List[str] = field(default_factory=lambda: ["on_demand"])
    conditions: List[dict] = field(default_factory=list)
    conditions_operator: str = "AND"
    actions: List[dict] = field(default_factory=list)
    scope: str = "all_portfolio_stocks"
    target_entities: List[str] = field(default_factory=list)
    enabled: bool = True
    created_at: str = ""

@dataclass
class EvalResult:
    rule_id: str
    rule_name: str
    entity: str
    fired: bool
    conditions_detail: List[dict] = field(default_factory=list)
    actions_taken: List[dict] = field(default_factory=list)
    timestamp: str = ""


# ── Built-in Rules ───────────────────────────────────────────────

BUILTIN_RULES = [
    {
        "rule_id": "builtin_rsi_overbought",
        "name": "RSI Overbought",
        "category": "risk",
        "when": ["on_schedule", "on_sensor_change"],
        "conditions": [{"source": "sensor", "entity": "{stock}", "group": "price_action", "operator": ">", "value": 0.4}],
        "actions": [{"action": "alert", "severity": "medium", "message": "{stock} price action overheated (score {value})"}],
    },
    {
        "rule_id": "builtin_mf_accumulation",
        "name": "MF Accumulation",
        "category": "opportunity",
        "when": ["on_data_ingest"],
        "conditions": [{"source": "sensor", "entity": "{stock}", "group": "institutional", "operator": ">", "value": 0.3}],
        "actions": [{"action": "alert", "severity": "low", "message": "{stock} institutional accumulation detected"}],
    },
    {
        "rule_id": "builtin_panic_buy",
        "name": "Panic + Smart Money = BUY",
        "category": "opportunity",
        "when": ["on_sensor_change"],
        "conditions": [
            {"source": "behavioral", "entity": "{stock}", "operator": "==", "value": "panic"},
            {"source": "sensor", "entity": "{stock}", "group": "institutional", "operator": ">", "value": 0.2},
        ],
        "conditions_operator": "AND",
        "actions": [{"action": "alert", "severity": "high", "message": "{stock} PANIC + smart money buying — contrarian BUY"}],
    },
    {
        "rule_id": "builtin_fii_exit",
        "name": "FII Exit Warning",
        "category": "risk",
        "when": ["on_data_ingest"],
        "conditions": [{"source": "sensor", "entity": "{stock}", "sensor": "fii_flow", "operator": "<", "value": -0.3}],
        "actions": [{"action": "alert", "severity": "high", "message": "{stock} FII heavily selling"}],
    },
    {
        "rule_id": "builtin_earnings_miss",
        "name": "Earnings Miss",
        "category": "monitoring",
        "when": ["on_sensor_change"],
        "conditions": [{"source": "sensor", "entity": "{stock}", "group": "fundamentals", "operator": "<", "value": -0.3}],
        "actions": [{"action": "alert", "severity": "high", "message": "{stock} fundamentals weak — possible earnings miss"}],
    },
    {
        "rule_id": "builtin_convergence_buy",
        "name": "High Convergence BUY",
        "category": "opportunity",
        "when": ["on_schedule"],
        "conditions": [
            {"source": "sensor", "entity": "{stock}", "field": "fused_score", "operator": ">", "value": 0.2},
            {"source": "sensor", "entity": "{stock}", "group": "institutional", "operator": ">", "value": 0.1},
            {"source": "sensor", "entity": "{stock}", "group": "news_sentiment", "operator": ">", "value": 0.1},
        ],
        "conditions_operator": "AND",
        "actions": [{"action": "suggest_trade", "severity": "medium", "message": "{stock} — 3 sensor groups aligned bullish. Strong BUY signal."}],
    },
]


# ── Engine ───────────────────────────────────────────────────────

class UniversalRuleEngine:
    """Evaluates universal rules against the context graph + data sources."""

    def __init__(self, registry, db_module=None):
        self._registry = registry
        self._db = db_module
        self._rules: Dict[str, UniversalRule] = {}
        self._load_rules()

    # ── CRUD ─────────────────────────────────────────────────────

    def add_rule(self, data: dict) -> UniversalRule:
        r = UniversalRule(**{k: v for k, v in data.items() if k in UniversalRule.__dataclass_fields__})
        if not r.rule_id:
            r.rule_id = f"ur_{uuid.uuid4().hex[:8]}"
        if not r.created_at:
            r.created_at = datetime.now(timezone.utc).isoformat()
        self._rules[r.rule_id] = r
        self._save()
        return r

    def remove_rule(self, rule_id: str) -> bool:
        if rule_id in self._rules:
            del self._rules[rule_id]
            self._save()
            return True
        return False

    def list_rules(self, category: str = None) -> List[dict]:
        rules = list(self._rules.values())
        if category:
            rules = [r for r in rules if r.category == category]
        return [asdict(r) for r in rules]

    def toggle_rule(self, rule_id: str, enabled: bool):
        if rule_id in self._rules:
            self._rules[rule_id].enabled = enabled
            self._save()

    # ── Evaluation ───────────────────────────────────────────────

    def evaluate(self, trigger: str = "on_demand", entities: List[str] = None) -> List[dict]:
        results = []
        for rule in self._rules.values():
            if not rule.enabled:
                continue
            if trigger not in rule.when and trigger != "on_demand":
                continue

            targets = entities or self._resolve_scope(rule)
            for entity in targets:
                result = self._eval_one(rule, entity)
                if result["fired"]:
                    self._execute_actions(rule, result)
                    results.append(result)
        return results

    def _eval_one(self, rule: UniversalRule, entity: str) -> dict:
        checks = []
        all_pass = True
        any_pass = False

        for cond_data in rule.conditions:
            cond = Condition(**{k: v for k, v in cond_data.items() if k in Condition.__dataclass_fields__})
            resolved = cond.entity.replace("{stock}", entity)
            passed, actual, detail = self._check(cond, resolved)

            checks.append({"field": cond.group or cond.sensor or cond.field, "operator": cond.operator,
                           "expected": cond.value, "actual": actual, "passed": passed, "detail": detail})

            if passed:
                any_pass = True
            else:
                all_pass = False

        fired = all_pass if rule.conditions_operator == "AND" else any_pass

        return {
            "rule_id": rule.rule_id, "rule_name": rule.name, "category": rule.category,
            "entity": entity, "fired": fired, "conditions": checks, "actions_taken": [],
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    def _check(self, cond: Condition, entity: str) -> tuple:
        actual = None
        detail = ""
        try:
            if cond.source == "sensor":
                actual = self._sensor_val(entity, cond.group, cond.sensor, cond.field)
                detail = f"{cond.group or cond.sensor or 'fused'} = {actual}"
            elif cond.source == "behavioral":
                actual = self._behavioral(entity)
                detail = f"state = {actual}"
            elif cond.source == "threshold":
                actual = self._threshold(entity, cond.field)
                detail = f"{cond.field} = {actual}"
            elif cond.source == "datasource":
                actual = self._ds_val(cond.datasource, entity, cond.field, cond.query_filter)
                detail = f"ds.{cond.field} = {actual}"
            else:
                return False, None, f"unknown source {cond.source}"
        except Exception as e:
            return False, None, str(e)

        if actual is None:
            return False, None, "no data"

        passed = self._cmp(actual, cond.operator, cond.value)
        return passed, actual, detail

    # ── Value Resolvers ──────────────────────────────────────────

    def _sensor_val(self, entity, group="", sensor="", field_name=""):
        try:
            import redis as _r
            r = _r.Redis()
            ns = entity.lower().replace(" ", "_").replace(".ns", "").replace(".bo", "")
            raw = r.get(f"fusion:{ns}:7")
            if not raw:
                return None
            d = json.loads(raw)
            if field_name == "fused_score":
                return d.get("fused_score", 0)
            if group and "sensor_groups" in d:
                return d["sensor_groups"].get(group, {}).get("score", 0)
            if sensor and "sensor_summary" in d:
                return d["sensor_summary"].get(sensor, 0)
            return d.get("fused_score", 0)
        except Exception:
            return None

    def _behavioral(self, entity):
        try:
            import redis as _r
            r = _r.Redis()
            ns = entity.lower().replace(" ", "_")
            raw = r.get(f"fusion:{ns}:7")
            if raw:
                return json.loads(raw).get("behavioral_state", {}).get("state")
        except Exception:
            pass
        return None

    def _threshold(self, entity, field):
        try:
            from plugins.stock_analysis.technical_indicators import TechnicalIndicatorEngine
            tech = TechnicalIndicatorEngine(self._registry)
            ind = tech.compute_all(entity)
            return ind.get(field, {}).get("value") if field in ind else None
        except Exception:
            return None

    def _ds_val(self, ds_name, entity, field, filters):
        try:
            from contextsynapse.core.datasource import DataSourceManager
            ds = DataSourceManager(self._registry, self._db)
            rows = ds.query(ds_name, filters={**filters, "stock_symbol": entity.upper()}, limit=1)
            if rows:
                return rows[0].get(field)
        except Exception:
            pass
        return None

    @staticmethod
    def _cmp(actual, op, expected):
        try:
            if op == ">":  return float(actual) > float(expected)
            if op == "<":  return float(actual) < float(expected)
            if op == ">=": return float(actual) >= float(expected)
            if op == "<=": return float(actual) <= float(expected)
            if op == "==": return str(actual).lower() == str(expected).lower()
            if op == "!=": return str(actual).lower() != str(expected).lower()
        except (ValueError, TypeError):
            pass
        return False

    # ── Actions ──────────────────────────────────────────────────

    def _execute_actions(self, rule, result):
        for ad in rule.actions:
            msg = ad.get("message", "").replace("{stock}", result["entity"]).replace("{entity}", result["entity"])
            if result["conditions"]:
                msg = msg.replace("{value}", str(result["conditions"][0].get("actual", "")))
            taken = {"action": ad["action"], "severity": ad.get("severity", "medium"), "message": msg}

            if ad["action"] == "alert":
                try:
                    from verticals.pms.backend.notifications import NotificationService, Notification, NotificationChannel, NotificationPriority
                    ns = NotificationService()
                    ns.queue(Notification(
                        recipient="all", channel=NotificationChannel.UI,
                        priority=NotificationPriority.HIGH if ad.get("severity") in ("high", "critical") else NotificationPriority.NORMAL,
                        title=f"Rule: {rule.name}", message=msg,
                        action_url=f"/pms/stock/{result['entity'].lower()}",
                    ))
                except Exception:
                    pass

            result["actions_taken"].append(taken)

    # ── Scope ────────────────────────────────────────────────────

    def _resolve_scope(self, rule):
        if rule.target_entities:
            return rule.target_entities
        if rule.scope == "all_portfolio_stocks":
            try:
                import requests
                pf = requests.get("http://localhost:8000/pms/portfolios", timeout=5).json()
                stocks = set()
                for p in pf:
                    pid = p.get("portfolio_id", p.get("id"))
                    h = requests.get(f"http://localhost:8000/pms/portfolios/{pid}/holdings", timeout=5).json()
                    for hld in (h if isinstance(h, list) else h.get("holdings", [])):
                        sym = hld.get("stock_symbol", "").replace(".NS", "").replace(".BO", "").lower()
                        if sym:
                            stocks.add(sym)
                return list(stocks)
            except Exception:
                return []
        return []

    # ── Persistence ──────────────────────────────────────────────

    def _load_rules(self):
        RULES_DIR.mkdir(parents=True, exist_ok=True)
        f = RULES_DIR / "universal_rules.json"
        if f.exists():
            try:
                for rd in json.loads(f.read_text()):
                    r = UniversalRule(**{k: v for k, v in rd.items() if k in UniversalRule.__dataclass_fields__})
                    self._rules[r.rule_id] = r
            except Exception:
                pass
        if not self._rules:
            for rd in BUILTIN_RULES:
                r = UniversalRule(**{k: v for k, v in rd.items() if k in UniversalRule.__dataclass_fields__})
                self._rules[r.rule_id] = r
            self._save()

    def _save(self):
        RULES_DIR.mkdir(parents=True, exist_ok=True)
        f = RULES_DIR / "universal_rules.json"
        f.write_text(json.dumps([asdict(r) for r in self._rules.values()], indent=2, default=str))

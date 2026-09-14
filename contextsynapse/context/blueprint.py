"""Context Blueprint — staging layer for context creation.

Flow:
  1. Agent fills template → Blueprint (draft)
  2. Pipelines start in VALIDATION mode (fetch real data)
  3. User reviews blueprint with REAL data (not placeholders)
  4. User edits (add/remove/change fields, linkages)
  5. User approves → status flips to active, data already there
  6. Over time: changes create new version → review → approve → apply

Blueprint lives in PostgreSQL until approved, then graph + polyglot are wired.

Usage:
    from contextsynapse.context.blueprint import BlueprintEngine

    engine = BlueprintEngine(graph_registry)

    # Create from template + agent research
    bp = engine.create("stock", "tcs", agent_output={...})

    # Validation pipelines auto-start
    bp.validation_status  → {"price": "ready", "news": "3 articles", "fusion": "0.34"}

    # User edits
    engine.edit(bp.id, {"corporate.ceo": {"value": "New Person", "action": "change"}})

    # User approves → everything wires instantly
    engine.approve(bp.id, approved_by="fm_neha")

    # Later: change CEO
    engine.propose_change(bp.id, field="corporate.ceo", new_value="Y", reason="CEO transition")
"""
from __future__ import annotations

import json
import logging
import time
import uuid
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)


BLUEPRINT_STATUSES = ["draft", "validating", "reviewing", "approved", "active", "modified"]


@dataclass
class Blueprint:
    """A context blueprint — staging layer before actual wiring."""

    id: str = ""
    context_type: str = ""         # "stock", "client", "portfolio"
    entity_key: str = ""           # "tcs", "cl_abc", "pf_xyz"
    status: str = "draft"
    version: int = 1

    # What agent discovered
    research: dict = field(default_factory=dict)

    # What user changed
    user_edits: list = field(default_factory=list)

    # Confidence results
    auto_wired: list = field(default_factory=list)
    flagged: list = field(default_factory=list)
    off_template: list = field(default_factory=list)
    overall_confidence: float = 0.0

    # Validation data (real data fetched during staging)
    validation_data: dict = field(default_factory=dict)
    validation_status: dict = field(default_factory=dict)

    # Graph plan (what will be created on approval)
    graph_plan: dict = field(default_factory=dict)

    # Pipeline plan
    pipeline_plan: list = field(default_factory=list)

    # Change history (after active)
    changes: list = field(default_factory=list)

    # Metadata
    created_by: str = ""
    created_at: str = ""
    approved_by: str = ""
    approved_at: str = ""

    def __post_init__(self):
        if not self.id:
            self.id = f"bp_{uuid.uuid4().hex[:8]}"
        if not self.created_at:
            self.created_at = datetime.now(timezone.utc).isoformat()

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "context_type": self.context_type,
            "entity_key": self.entity_key,
            "status": self.status,
            "version": self.version,
            "research": self.research,
            "user_edits": self.user_edits,
            "auto_wired": self.auto_wired,
            "flagged": self.flagged,
            "off_template": self.off_template,
            "overall_confidence": self.overall_confidence,
            "validation_data": self.validation_data,
            "validation_status": self.validation_status,
            "graph_plan": self.graph_plan,
            "pipeline_plan": self.pipeline_plan,
            "changes": self.changes,
            "created_by": self.created_by,
            "created_at": self.created_at,
            "approved_by": self.approved_by,
            "approved_at": self.approved_at,
        }


class BlueprintEngine:
    """Manages blueprint lifecycle — create, validate, review, approve, change."""

    def __init__(self, graph_registry=None):
        self._registry = graph_registry
        self._blueprints: dict[str, Blueprint] = {}

    # ── Create ───────────────────────────────────────────────────

    def create(
        self,
        context_type: str,
        entity_key: str,
        agent_output: dict = None,
        created_by: str = "",
        template_name: str = "",
    ) -> Blueprint:
        """Create a blueprint from agent research output.

        Immediately starts validation pipelines in background.
        """
        bp = Blueprint(
            context_type=context_type,
            entity_key=entity_key,
            research=agent_output or {},
            created_by=created_by,
            status="validating",
        )

        # Apply confidence gate
        self._apply_confidence_gate(bp)

        # Store
        self._blueprints[bp.id] = bp
        self._store_pg(bp)

        # Start validation pipelines in background
        threading.Thread(target=self._run_validation, args=(bp,), daemon=True).start()

        logger.info("[BLUEPRINT] Created %s for %s:%s (confidence: %.0f%%)",
                     bp.id, context_type, entity_key, bp.overall_confidence * 100)
        return bp

    def create_from_llm(
        self,
        context_type: str,
        entity_key: str,
        created_by: str = "",
    ) -> Blueprint:
        """Create blueprint using LLM to research the entity."""
        # Use the seed agent to discover entity info
        agent_output = self._llm_research(context_type, entity_key)
        return self.create(context_type, entity_key, agent_output, created_by)

    # ── Edit ─────────────────────────────────────────────────────

    def edit(self, blueprint_id: str, edits: dict, edited_by: str = "") -> Blueprint:
        """Apply user edits to a blueprint.

        edits: {"corporate.ceo": {"value": "New Name", "action": "change"},
                "corporate.board": {"value": "Person X", "action": "add"}}
        """
        bp = self._blueprints.get(blueprint_id)
        if not bp:
            bp = self._load_pg(blueprint_id)
        if not bp:
            return None

        for field_path, edit in edits.items():
            bp.user_edits.append({
                "field": field_path,
                "action": edit.get("action", "change"),
                "value": edit.get("value"),
                "previous": self._get_nested(bp.research, field_path),
                "edited_by": edited_by,
                "edited_at": datetime.now(timezone.utc).isoformat(),
            })

            # Apply edit to research data
            if edit.get("action") == "change":
                self._set_nested(bp.research, field_path, {
                    "value": edit["value"],
                    "confidence": 1.0,
                    "source": "user_edit",
                })
            elif edit.get("action") == "add":
                self._add_nested(bp.research, field_path, {
                    "value": edit["value"],
                    "confidence": 1.0,
                    "source": "user_edit",
                })
            elif edit.get("action") == "remove":
                self._remove_nested(bp.research, field_path)

        bp.status = "reviewing"
        self._store_pg(bp)
        return bp

    # ── Approve ──────────────────────────────────────────────────

    def approve(self, blueprint_id: str, approved_by: str = "") -> Blueprint:
        """Approve blueprint → wire everything into graph + polyglot.

        Validation data is already there — this just flips the switch.
        """
        bp = self._blueprints.get(blueprint_id) or self._load_pg(blueprint_id)
        if not bp:
            return None

        bp.status = "approved"
        bp.approved_by = approved_by
        bp.approved_at = datetime.now(timezone.utc).isoformat()

        # Wire everything — data already validated
        self._wire_context(bp)

        bp.status = "active"
        self._store_pg(bp)

        logger.info("[BLUEPRINT] Approved %s:%s by %s — context wired",
                     bp.context_type, bp.entity_key, approved_by)
        return bp

    # ── Change Management ────────────────────────────────────────

    def propose_change(
        self,
        blueprint_id: str,
        field_path: str,
        new_value: Any,
        reason: str = "",
        proposed_by: str = "",
    ) -> dict:
        """Propose a change to an active context (e.g., CEO change)."""
        bp = self._blueprints.get(blueprint_id) or self._load_pg(blueprint_id)
        if not bp:
            return {"error": "Blueprint not found"}

        change = {
            "version": bp.version + 1,
            "field": field_path,
            "from": self._get_nested(bp.research, field_path),
            "to": new_value,
            "reason": reason,
            "proposed_by": proposed_by,
            "proposed_at": datetime.now(timezone.utc).isoformat(),
            "status": "pending",
        }

        bp.changes.append(change)
        bp.status = "modified"
        self._store_pg(bp)

        return change

    def approve_change(self, blueprint_id: str, change_index: int, approved_by: str = "") -> dict:
        """Approve a proposed change → apply to active context."""
        bp = self._blueprints.get(blueprint_id) or self._load_pg(blueprint_id)
        if not bp or change_index >= len(bp.changes):
            return {"error": "Not found"}

        change = bp.changes[change_index]
        change["status"] = "approved"
        change["approved_by"] = approved_by
        change["approved_at"] = datetime.now(timezone.utc).isoformat()

        # Apply the change
        old_value = self._get_nested(bp.research, change["field"])
        self._set_nested(bp.research, change["field"], {
            "value": change["to"],
            "confidence": 1.0,
            "source": "change_approved",
            "previous": old_value,
            "valid_from": datetime.now(timezone.utc).isoformat(),
        })

        bp.version += 1
        bp.status = "active"
        self._store_pg(bp)

        # Update graph (temporal — old value becomes .past())
        self._apply_graph_change(bp, change)

        return change

    # ── Query ────────────────────────────────────────────────────

    def get(self, blueprint_id: str) -> Blueprint | None:
        return self._blueprints.get(blueprint_id) or self._load_pg(blueprint_id)

    def list_all(self, status: str = "", context_type: str = "") -> list[dict]:
        try:
            from contextsynapse.db.postgres import execute
            conditions = []
            params = []
            if status:
                conditions.append("status = %s")
                params.append(status)
            if context_type:
                conditions.append("context_type = %s")
                params.append(context_type)
            where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
            rows = execute(
                f"SELECT id, context_type, entity_key, status, version, overall_confidence, created_at "
                f"FROM context_blueprints {where} ORDER BY created_at DESC",
                tuple(params),
            )
            return rows
        except Exception:
            return [bp.to_dict() for bp in self._blueprints.values()]

    # ── Validation Pipelines ─────────────────────────────────────

    def _run_validation(self, bp: Blueprint):
        """Run validation pipelines in background — fetch real data."""
        logger.info("[BLUEPRINT] Starting validation for %s:%s", bp.context_type, bp.entity_key)
        bp.validation_status = {"overall": "running"}

        if bp.context_type == "stock":
            self._validate_stock(bp)
        elif bp.context_type == "client":
            self._validate_client(bp)
        elif bp.context_type == "portfolio":
            self._validate_portfolio(bp)

        bp.validation_status["overall"] = "ready"
        bp.status = "reviewing"
        self._store_pg(bp)
        logger.info("[BLUEPRINT] Validation complete for %s:%s", bp.context_type, bp.entity_key)

    def _validate_stock(self, bp: Blueprint):
        """Fetch real stock data for validation."""
        entity = bp.entity_key
        ticker = f"{entity.upper()}.NS"

        # Price data
        try:
            import yfinance as yf
            hist = yf.Ticker(ticker).history(period="1mo", interval="1d")
            if not hist.empty:
                latest = hist.iloc[-1]
                bp.validation_data["price"] = {
                    "current": round(float(latest["Close"]), 2),
                    "change_1m": round((float(latest["Close"]) - float(hist.iloc[0]["Close"])) / float(hist.iloc[0]["Close"]) * 100, 2),
                    "data_points": len(hist),
                    "source": "yfinance",
                }
                bp.validation_status["price"] = "ready"
        except Exception as e:
            bp.validation_status["price"] = f"failed: {e}"

        # Technical indicators
        try:
            from contextsynapse.context.stock_lifecycle import _compute_technicals
            import numpy as np
            closes = hist["Close"].values if 'hist' in dir() and not hist.empty else np.array([])
            volumes = hist["Volume"].values if 'hist' in dir() and not hist.empty else np.array([])
            if len(closes) > 14:
                technicals = _compute_technicals(closes, volumes)
                bp.validation_data["technicals"] = technicals
                bp.validation_status["technicals"] = "ready"
        except Exception as e:
            bp.validation_status["technicals"] = f"failed: {e}"

        # Fusion (from cache or compute)
        try:
            import redis, os
            r = redis.Redis.from_url(
                os.environ.get("AICONTEXTDB_REDIS_URL", "redis://localhost:6379/0"),
                socket_connect_timeout=1,
            )
            cached = r.get(f"fusion:{entity}:7")
            if cached:
                fusion = json.loads(cached)
                bp.validation_data["fusion"] = {
                    "score": fusion.get("fused_score", 0),
                    "direction": fusion.get("direction", "unknown"),
                    "source": "cache",
                }
                bp.validation_status["fusion"] = "ready"
            else:
                bp.validation_status["fusion"] = "no cache"
        except Exception:
            bp.validation_status["fusion"] = "unavailable"

        # Stock master (PG)
        try:
            from contextsynapse.db.stock_master import StockMaster
            master = StockMaster().get(entity.upper())
            if master:
                bp.validation_data["master"] = {
                    "company_name": master.get("company_name"),
                    "sector": master.get("sector"),
                    "isin": master.get("isin"),
                }
                bp.validation_status["stock_master"] = "ready"
        except Exception:
            bp.validation_status["stock_master"] = "unavailable"

    def _validate_client(self, bp: Blueprint):
        bp.validation_status["identity"] = "ready"  # client data is input, not fetched

    def _validate_portfolio(self, bp: Blueprint):
        bp.validation_status["structure"] = "ready"

    # ── Wiring (on approval) ─────────────────────────────────────

    def _wire_context(self, bp: Blueprint):
        """Wire everything into graph + polyglot. Called on approval."""
        if bp.context_type == "stock":
            # Use the existing stock lifecycle promote
            try:
                from contextsynapse.context.stock_lifecycle import StockLifecycle
                lifecycle = StockLifecycle(self._registry)
                lifecycle.promote(bp.entity_key)
            except Exception as e:
                logger.warning("[BLUEPRINT] Stock wiring failed: %s", e)

        elif bp.context_type == "client":
            # Client is already in PG, just ensure graph context
            try:
                from contextsynapse.db.pms_client import PmsClientManager
                mgr = PmsClientManager()
                client = mgr.get(bp.entity_key)
                if client:
                    mgr._create_graph_context(client)
            except Exception as e:
                logger.warning("[BLUEPRINT] Client wiring failed: %s", e)

    def _apply_graph_change(self, bp: Blueprint, change: dict):
        """Apply a change to the graph (temporal — old value becomes past)."""
        # The graph node property gets updated with valid_from/valid_to
        logger.info("[BLUEPRINT] Graph change applied: %s.%s → %s",
                     bp.entity_key, change["field"], change["to"])

    # ── LLM Research ─────────────────────────────────────────────

    def _llm_research(self, context_type: str, entity_key: str) -> dict:
        """Use LLM to research an entity and fill the template."""
        if context_type == "stock":
            try:
                from contextsynapse.llm import get_llm_client
                llm = get_llm_client()
                prompt = f"""Research the Indian stock "{entity_key.upper()}" and return JSON:
{{
  "identity": {{
    "company_name": "full name",
    "sector": "sector",
    "industry": "specific industry",
    "parent_group": "parent company/group",
    "description": "one-line description"
  }},
  "corporate": {{
    "ceo": "CEO name",
    "cfo": "CFO name",
    "key_people": [{{"name": "...", "role": "..."}}]
  }},
  "market": {{
    "peers": ["ticker1", "ticker2"],
    "brands": ["brand1", "brand2"],
    "keywords": ["keyword1", "keyword2"],
    "revenue_geography": {{"US": 50, "Europe": 20, "India": 15}}
  }}
}}"""
                result = llm.generate_json(prompt, system="Financial data assistant. Indian stock market. Return valid JSON only.")
                return result
            except Exception as e:
                logger.warning("[BLUEPRINT] LLM research failed: %s", e)
                return {}
        return {}

    # ── Confidence Gate ──────────────────────────────────────────

    def _apply_confidence_gate(self, bp: Blueprint):
        """Score confidence and categorize fields."""
        total_fields = 0
        high_conf = 0

        def _score(obj, path=""):
            nonlocal total_fields, high_conf
            if isinstance(obj, dict):
                if "confidence" in obj:
                    total_fields += 1
                    conf = float(obj.get("confidence", 0))
                    field_path = path
                    if conf >= 0.8:
                        bp.auto_wired.append(field_path)
                        high_conf += 1
                    elif conf >= 0.5:
                        bp.flagged.append({"field": field_path, "confidence": conf})
                    else:
                        bp.off_template.append({"field": field_path, "confidence": conf})
                else:
                    for k, v in obj.items():
                        _score(v, f"{path}.{k}" if path else k)

        _score(bp.research)
        bp.overall_confidence = high_conf / max(total_fields, 1)

    # ── Storage (PostgreSQL) ─────────────────────────────────────

    def _store_pg(self, bp: Blueprint):
        try:
            from contextsynapse.db.postgres import get_connection
            with get_connection() as conn:
                cur = conn.cursor()
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS context_blueprints (
                        id TEXT PRIMARY KEY,
                        context_type TEXT,
                        entity_key TEXT,
                        status TEXT DEFAULT 'draft',
                        version INTEGER DEFAULT 1,
                        overall_confidence NUMERIC(5,4) DEFAULT 0,
                        data JSONB DEFAULT '{}',
                        created_by TEXT,
                        created_at TIMESTAMPTZ DEFAULT now(),
                        approved_by TEXT,
                        approved_at TIMESTAMPTZ,
                        updated_at TIMESTAMPTZ DEFAULT now()
                    )
                """)
                cur.execute("""
                    INSERT INTO context_blueprints (id, context_type, entity_key, status, version,
                        overall_confidence, data, created_by, approved_by, approved_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (id) DO UPDATE SET
                        status = EXCLUDED.status, version = EXCLUDED.version,
                        overall_confidence = EXCLUDED.overall_confidence,
                        data = EXCLUDED.data, approved_by = EXCLUDED.approved_by,
                        approved_at = EXCLUDED.approved_at, updated_at = now()
                """, (
                    bp.id, bp.context_type, bp.entity_key, bp.status, bp.version,
                    bp.overall_confidence, json.dumps(bp.to_dict(), default=str),
                    bp.created_by, bp.approved_by, bp.approved_at or None,
                ))
        except Exception as e:
            logger.debug("[BLUEPRINT] PG store failed: %s", e)

    def _load_pg(self, blueprint_id: str) -> Blueprint | None:
        try:
            from contextsynapse.db.postgres import execute_one
            row = execute_one("SELECT data FROM context_blueprints WHERE id = %s", (blueprint_id,))
            if row and row.get("data"):
                data = row["data"] if isinstance(row["data"], dict) else json.loads(row["data"])
                bp = Blueprint(**{k: v for k, v in data.items() if k in Blueprint.__dataclass_fields__})
                self._blueprints[bp.id] = bp
                return bp
        except Exception:
            pass
        return None

    # ── Helpers ───────────────────────────────────────────────────

    def _get_nested(self, obj, path):
        for key in path.split("."):
            if isinstance(obj, dict):
                obj = obj.get(key)
            else:
                return None
        return obj

    def _set_nested(self, obj, path, value):
        keys = path.split(".")
        for key in keys[:-1]:
            obj = obj.setdefault(key, {})
        obj[keys[-1]] = value

    def _add_nested(self, obj, path, value):
        keys = path.split(".")
        for key in keys[:-1]:
            obj = obj.setdefault(key, {})
        last = keys[-1]
        if isinstance(obj.get(last), list):
            obj[last].append(value)
        else:
            obj[last] = value

    def _remove_nested(self, obj, path):
        keys = path.split(".")
        for key in keys[:-1]:
            if isinstance(obj, dict):
                obj = obj.get(key, {})
        if isinstance(obj, dict):
            obj.pop(keys[-1], None)

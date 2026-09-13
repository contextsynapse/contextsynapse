"""Persistence layer for intelligence subsystem.

Saves and loads: signal hierarchy, collection plans, reactive rules,
watchdog config — to/from context manager (SQLite) and Redis.

Everything persists through server restarts:
- Hierarchy: saved as JSON in a special "__hierarchy__" context config
- Collection plans: saved in each context's config["pipelines"] + config["collection"]
- Reactive rules: saved in "__hierarchy__" context config
- Watchdog: saved in "__hierarchy__" context config

Usage:
    from contextsynapse.intelligence.persistence import IntelligencePersistence

    store = IntelligencePersistence(context_manager)
    store.save_hierarchy(hierarchy)
    store.save_collection_plan(plan)

    # On startup:
    hierarchy = store.load_hierarchy()
    plans = store.load_all_plans()
"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Special context name for storing hierarchy + global config
_HIERARCHY_CONTEXT = "__intelligence_config__"


class IntelligencePersistence:
    """Persists intelligence config to context manager (SQLite)."""

    def __init__(self, context_manager):
        self._cm = context_manager
        self._ensure_config_context()

    def _find_by_name(self, name: str):
        """Find a context by name (case-insensitive)."""
        try:
            contexts = self._cm.list_contexts(status="active")
            for ctx in contexts:
                if ctx.name.lower() == name.lower():
                    return ctx
        except Exception:
            pass
        return None

    def _ensure_config_context(self):
        """Create the hidden config context if it doesn't exist."""
        ctx = self._find_by_name(_HIERARCHY_CONTEXT)
        if not ctx:
            try:
                self._cm.create_context(
                    name=_HIERARCHY_CONTEXT,
                    context_type="system",
                    description="Intelligence subsystem configuration — hierarchy, rules, watchdog",
                    source="system",
                    config={},
                )
                logger.info("[PERSIST] Created intelligence config context")
            except Exception as exc:
                logger.warning("[PERSIST] Could not create config context: %s", exc)

    def _get_config(self) -> Dict[str, Any]:
        """Get the config dict from the hidden context."""
        ctx = self._find_by_name(_HIERARCHY_CONTEXT)
        if ctx:
            return ctx.config or {}
        return {}

    def _save_config(self, config: Dict[str, Any]):
        """Save config to the hidden context."""
        ctx = self._find_by_name(_HIERARCHY_CONTEXT)
        if ctx:
            self._cm.update_context(ctx.context_id, config=config)

    # ── Hierarchy ──

    def save_hierarchy(self, hierarchy) -> bool:
        """Save signal hierarchy to persistent storage."""
        try:
            config = self._get_config()
            config["hierarchy"] = hierarchy.to_dict()
            self._save_config(config)
            logger.info("[PERSIST] Hierarchy saved (%d links)", len(hierarchy._links))
            return True
        except Exception as exc:
            logger.error("[PERSIST] Failed to save hierarchy: %s", exc)
            return False

    def load_hierarchy(self):
        """Load signal hierarchy from persistent storage."""
        from .signal_hierarchy import SignalHierarchy
        config = self._get_config()
        data = config.get("hierarchy")
        if data:
            h = SignalHierarchy.from_dict(data)
            logger.info("[PERSIST] Hierarchy loaded (%d links)", len(h._links))
            return h
        return None

    # ── Collection Plans ──

    def save_collection_plan(self, plan) -> bool:
        """Save a collection plan to the target context's config.

        The plan is stored in context.config["collection"] for the
        context matching plan.context_name. The pipeline configs are
        stored in context.config["pipelines"] for the executor.
        """
        try:
            ctx = self._find_by_name(plan.context_name)
            if not ctx:
                # Create the context
                ctx = self._cm.create_context(
                    name=plan.context_name,
                    description=plan.context_description,
                    context_type="knowledge_base",
                    source="collection_plan",
                    tags=plan.tags,
                    config={},
                )

            config = dict(ctx.config or {})
            config["collection"] = {
                "template": plan.template,
                "tags": plan.tags,
                "description": plan.context_description,
            }
            config["pipelines"] = [p.to_dict() for p in plan.pipelines]

            # Set up schedule for ContextPipelineScheduler
            # Find the fastest enabled pipeline interval
            intervals_mins = []
            for p in plan.pipelines:
                if p.enabled and p.source_type not in ("manual", "upload"):
                    from .collector import _INTERVAL_MINUTES
                    mins = _INTERVAL_MINUTES.get(p.interval, 1440)
                    intervals_mins.append(mins)

            if intervals_mins:
                fastest = min(intervals_mins)
                config["schedule"] = {
                    "trigger_type": "scheduled",
                    "status": "active",
                    "interval_minutes": fastest,
                }

            self._cm.update_context(ctx.context_id, config=config,
                                      description=plan.context_description,
                                      tags=plan.tags)
            logger.info("[PERSIST] Plan saved: '%s' (%d pipelines)",
                         plan.context_name, len(plan.pipelines))
            return True
        except Exception as exc:
            logger.error("[PERSIST] Failed to save plan '%s': %s", plan.context_name, exc)
            return False

    def load_collection_plan(self, context_name: str):
        """Load a collection plan from a context's config."""
        from .collector import CollectionPlan, PipelineConfig
        ctx = self._find_by_name(context_name)
        if not ctx:
            return None

        config = ctx.config or {}
        pipelines_data = config.get("pipelines", [])
        collection_data = config.get("collection", {})

        if not pipelines_data:
            return None

        return CollectionPlan(
            context_name=context_name,
            context_description=collection_data.get("description", ctx.description),
            tags=collection_data.get("tags", ctx.tags or []),
            template=collection_data.get("template", ""),
            pipelines=[PipelineConfig.from_dict(p) for p in pipelines_data],
        )

    def load_all_plans(self) -> list:
        """Load all collection plans from all contexts."""
        plans = []
        try:
            contexts = self._cm.list_contexts(status="active")
            for ctx in contexts:
                config = ctx.config or {}
                if config.get("pipelines"):
                    plan = self.load_collection_plan(ctx.name)
                    if plan:
                        plans.append(plan)
        except Exception as exc:
            logger.error("[PERSIST] Failed to load plans: %s", exc)
        return plans

    # ── Reactive Rules ──

    def save_reactive_rules(self, reactive) -> bool:
        """Save reactive controller rules."""
        try:
            config = self._get_config()
            rules_data = {}
            for signal_type, rules in reactive._rules.items():
                rules_data[signal_type] = [r.to_dict() for r in rules]
            config["reactive_rules"] = rules_data
            self._save_config(config)
            logger.info("[PERSIST] Reactive rules saved")
            return True
        except Exception as exc:
            logger.error("[PERSIST] Failed to save reactive rules: %s", exc)
            return False

    def load_reactive_rules(self, reactive) -> bool:
        """Load reactive rules into a controller."""
        from .reactive import ReactionRule
        config = self._get_config()
        rules_data = config.get("reactive_rules", {})
        if not rules_data:
            return False

        for signal_type, rules_list in rules_data.items():
            for rule_dict in rules_list:
                reactive.add_rule(ReactionRule.from_dict(rule_dict))
        logger.info("[PERSIST] Reactive rules loaded")
        return True

    # ── Watchdog Config ──

    def save_watchdog_config(self, watchdog) -> bool:
        """Save watchdog source configuration."""
        try:
            config = self._get_config()
            config["watchdog"] = {
                "context": watchdog._watchdog_context,
                "sources": [
                    {"url": s.url, "name": s.name, "source_type": s.source_type}
                    for s in watchdog._sources
                ],
            }
            self._save_config(config)
            logger.info("[PERSIST] Watchdog config saved (%d sources)", len(watchdog._sources))
            return True
        except Exception as exc:
            logger.error("[PERSIST] Failed to save watchdog config: %s", exc)
            return False

    def load_watchdog_config(self, watchdog) -> bool:
        """Load watchdog sources from persistent storage."""
        config = self._get_config()
        wd_config = config.get("watchdog", {})
        if not wd_config:
            return False

        watchdog._watchdog_context = wd_config.get("context", "Global Macro")
        for src in wd_config.get("sources", []):
            watchdog.add_source(
                url=src.get("url", ""),
                name=src.get("name", ""),
                source_type=src.get("source_type", "rss"),
            )
        logger.info("[PERSIST] Watchdog config loaded (%d sources)", len(watchdog._sources))
        return True

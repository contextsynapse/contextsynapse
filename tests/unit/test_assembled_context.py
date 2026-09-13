"""Tests for AssembledContext composition and purpose templates."""
import pytest
from contextcore.context.assembled import (
    AssembledContext, ContextPurpose, PURPOSE_TEMPLATES, resolve_atomics,
)
from contextcore.context.acl import JWTIdentity, ContextPermission
from contextcore.security.rbac import Role


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

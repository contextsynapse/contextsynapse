"""Integration test: PMS rules YAML → UnifiedRulesEngine → portfolio evaluation."""
import os
import pytest
from dataclasses import dataclass, field
from typing import Dict, Any, List
from unittest.mock import MagicMock

from contextcore.rules.engine import UnifiedRulesEngine
from contextcore.rules.models import RuleAction, RuleSeverity, RuleTrigger


@dataclass
class FakeNode:
    id: str
    label: str
    properties: Dict[str, Any] = field(default_factory=dict)


def make_db(nodes: List[FakeNode]):
    db = MagicMock()
    def get_all_nodes(node_type=None):
        if node_type:
            return [n for n in nodes if n.label == node_type]
        return nodes
    db.get_all_nodes = get_all_nodes
    db.csr_adapter = db
    return db


RULES_PATH = os.path.join(
    os.path.dirname(__file__), "..", "..", "contextcore", "config", "schemas", "pms_rules.yaml"
)


@pytest.fixture
def engine():
    if not os.path.isfile(RULES_PATH):
        pytest.skip("pms_rules.yaml not found")
    return UnifiedRulesEngine.from_yaml(RULES_PATH)


class TestComplianceIntegration:

    def test_compliant_portfolio_passes(self, engine):
        """A properly diversified portfolio should trigger no blocks."""
        holdings = [
            FakeNode("h1", "Holding", {"stock_symbol": "TCS", "weight": 0.08, "sector": "IT", "related_party_group": "tata_group"}),
            FakeNode("h2", "Holding", {"stock_symbol": "INFY", "weight": 0.07, "sector": "IT", "related_party_group": "infy_group"}),
            FakeNode("h3", "Holding", {"stock_symbol": "HDFC", "weight": 0.09, "sector": "Banking", "related_party_group": "hdfc_group"}),
            FakeNode("h4", "Holding", {"stock_symbol": "RELIANCE", "weight": 0.06, "sector": "Energy", "related_party_group": "ril_group"}),
            FakeNode("h5", "Holding", {"stock_symbol": "SBI", "weight": 0.05, "sector": "Banking", "related_party_group": "psu_group"}),
        ]
        db = make_db(holdings)
        results = engine.evaluate(RuleTrigger.ON_TRADE, db)
        blocks = [r for r in results if r.action == RuleAction.BLOCK]
        assert len(blocks) == 0

    def test_single_stock_breach_blocked(self, engine):
        """A stock at 12% should trigger SEBI single stock block."""
        holdings = [
            FakeNode("h1", "Holding", {"stock_symbol": "TCS", "weight": 0.12, "sector": "IT"}),
            FakeNode("h2", "Holding", {"stock_symbol": "INFY", "weight": 0.07, "sector": "IT"}),
        ]
        db = make_db(holdings)
        results = engine.evaluate(RuleTrigger.ON_TRADE, db)
        blocks = [r for r in results if r.action == RuleAction.BLOCK]
        assert any("TCS" in b.message for b in blocks)

    def test_sector_breach_blocked(self, engine):
        """IT sector at 27% should trigger SEBI sector block."""
        holdings = [
            FakeNode("h1", "Holding", {"stock_symbol": "TCS", "weight": 0.10, "sector": "IT"}),
            FakeNode("h2", "Holding", {"stock_symbol": "INFY", "weight": 0.09, "sector": "IT"}),
            FakeNode("h3", "Holding", {"stock_symbol": "WIPRO", "weight": 0.08, "sector": "IT"}),
            FakeNode("h4", "Holding", {"stock_symbol": "HDFC", "weight": 0.05, "sector": "Banking"}),
        ]
        db = make_db(holdings)
        results = engine.evaluate(RuleTrigger.ON_TRADE, db)
        sector_blocks = [r for r in results
                         if r.action == RuleAction.BLOCK and "sector" in r.message.lower()]
        assert len(sector_blocks) >= 1

    def test_restricted_stock_blocked(self, engine):
        """Trading a restricted stock should be blocked."""
        nodes = [
            FakeNode("r1", "RestrictedStock", {"stock_symbol": "BAD.NS", "reason": "SEBI ban"}),
        ]
        db = make_db(nodes)
        results = engine.evaluate(
            RuleTrigger.ON_TRADE, db,
            action_context={"stock_symbol": "BAD.NS"},
        )
        blocks = [r for r in results if r.action == RuleAction.BLOCK]
        assert len(blocks) >= 1

    def test_top5_concentration_warns(self, engine):
        """Top 5 holdings above 40% should trigger a warning."""
        holdings = [
            FakeNode("h1", "Holding", {"stock_symbol": "A", "weight": 0.12}),
            FakeNode("h2", "Holding", {"stock_symbol": "B", "weight": 0.10}),
            FakeNode("h3", "Holding", {"stock_symbol": "C", "weight": 0.09}),
            FakeNode("h4", "Holding", {"stock_symbol": "D", "weight": 0.08}),
            FakeNode("h5", "Holding", {"stock_symbol": "E", "weight": 0.07}),
            FakeNode("h6", "Holding", {"stock_symbol": "F", "weight": 0.04}),
        ]
        db = make_db(holdings)
        results = engine.evaluate(RuleTrigger.ON_TRADE, db)
        warns = [r for r in results if r.action == RuleAction.WARN]
        assert len(warns) >= 1

    def test_results_ordered_critical_first(self, engine):
        """Results should be sorted by severity — critical before medium."""
        holdings = [
            FakeNode("h1", "Holding", {"stock_symbol": "TCS", "weight": 0.15, "sector": "IT"}),
            FakeNode("h2", "Holding", {"stock_symbol": "INFY", "weight": 0.10, "sector": "IT"}),
            FakeNode("h3", "Holding", {"stock_symbol": "WIPRO", "weight": 0.08, "sector": "IT"}),
        ]
        db = make_db(holdings)
        results = engine.evaluate(RuleTrigger.ON_TRADE, db)
        if len(results) >= 2:
            severities = [r.severity for r in results]
            # CRITICAL should appear before MEDIUM/LOW
            crit_indices = [i for i, s in enumerate(severities) if s == RuleSeverity.CRITICAL]
            other_indices = [i for i, s in enumerate(severities) if s != RuleSeverity.CRITICAL]
            if crit_indices and other_indices:
                assert max(crit_indices) < min(other_indices)

# tests/unit/test_audit_logger.py
"""Tests for AuditLogger — append-only compliance audit trail."""
import pytest

from contextcore.security.audit_logger import AuditLogger, AuditEntry


class TestAuditLog:

    def test_log_entry(self):
        logger = AuditLogger()
        entry = logger.log(
            who="user_001", action="trade_proposed",
            resource="portfolio/pf_001", role="fund_manager",
            details={"stock": "TCS.NS", "action": "buy", "qty": 100},
        )
        assert entry.who == "user_001"
        assert entry.action == "trade_proposed"
        assert entry.resource == "portfolio/pf_001"
        assert entry.timestamp != ""

    def test_multiple_entries(self):
        logger = AuditLogger()
        logger.log(who="u1", action="login", resource="session")
        logger.log(who="u1", action="view_portfolio", resource="pf_001")
        logger.log(who="u1", action="propose_trade", resource="pf_001")
        entries = logger.get_entries()
        assert len(entries) == 3

    def test_filter_by_resource(self):
        logger = AuditLogger()
        logger.log(who="u1", action="view", resource="pf_001")
        logger.log(who="u1", action="view", resource="pf_002")
        logger.log(who="u1", action="trade", resource="pf_001")
        entries = logger.get_entries(resource="pf_001")
        assert len(entries) == 2

    def test_filter_by_action(self):
        logger = AuditLogger()
        logger.log(who="u1", action="login", resource="session")
        logger.log(who="u1", action="trade", resource="pf_001")
        logger.log(who="u2", action="trade", resource="pf_002")
        entries = logger.get_entries(action="trade")
        assert len(entries) == 2

    def test_limit_entries(self):
        logger = AuditLogger()
        for i in range(20):
            logger.log(who="u1", action=f"action_{i}", resource="r")
        entries = logger.get_entries(limit=5)
        assert len(entries) == 5
        # Most recent first
        assert entries[0].action == "action_19"

    def test_entry_to_dict(self):
        logger = AuditLogger()
        entry = logger.log(
            who="u1", action="trade_executed", resource="pf_001",
            details={"trade_id": "trd_001"}, skill_chain="/pre-trade-check → /compliance-check",
            role="fund_manager",
        )
        d = entry.to_dict()
        assert d["who"] == "u1"
        assert d["action"] == "trade_executed"
        assert d["details"]["trade_id"] == "trd_001"
        assert d["skill_chain"] == "/pre-trade-check → /compliance-check"
        assert "timestamp" in d

    def test_append_only_no_delete(self):
        logger = AuditLogger()
        logger.log(who="u1", action="trade", resource="pf_001")
        # AuditLogger has no delete method — this is by design
        assert not hasattr(logger, 'delete')
        assert not hasattr(logger, 'update')
        assert not hasattr(logger, 'clear')

    def test_log_with_ip_address(self):
        logger = AuditLogger()
        entry = logger.log(
            who="u1", action="login", resource="session",
            ip_address="192.168.1.1",
        )
        assert entry.ip_address == "192.168.1.1"

    def test_entries_ordered_by_time(self):
        logger = AuditLogger()
        logger.log(who="u1", action="first", resource="r")
        logger.log(who="u1", action="second", resource="r")
        logger.log(who="u1", action="third", resource="r")
        entries = logger.get_entries()
        assert entries[0].action == "third"  # most recent first
        assert entries[2].action == "first"

    def test_total_entries_count(self):
        logger = AuditLogger()
        for i in range(10):
            logger.log(who="u1", action="a", resource="r")
        assert logger.total_entries == 10

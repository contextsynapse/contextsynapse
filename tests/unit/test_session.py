"""Tests for Context Session — attach/detach + insight write-back."""
import pytest


class TestContextSession:

    def test_attach_detach(self):
        from contextcore.intelligence.session import ContextSession
        session = ContextSession("Test")
        session.attach("tcs")
        session.attach("india_economy")
        assert session.list_attached() == ["tcs", "india_economy"]

        session.detach("india_economy")
        assert session.list_attached() == ["tcs"]

    def test_no_duplicate_attach(self):
        from contextcore.intelligence.session import ContextSession
        session = ContextSession("Test")
        assert session.attach("tcs") is True
        assert session.attach("tcs") is False
        assert len(session.list_attached()) == 1

    def test_add_insight(self):
        from contextcore.intelligence.session import ContextSession
        session = ContextSession("Test")
        ins = session.add_insight(
            insight="TCS outperforms when USD weakens",
            produced_by="agent:claude",
            confidence=0.85,
            evidence=["Q1 revenue up 4.2%"],
        )
        assert ins.insight == "TCS outperforms when USD weakens"
        assert ins.produced_by == "agent:claude"
        assert ins.confidence == 0.85

    def test_get_insights_filtered(self):
        from contextcore.intelligence.session import ContextSession
        session = ContextSession("Test")
        session.add_insight("Insight A", produced_by="agent:claude")
        session.add_insight("Insight B", produced_by="platform:fusion")
        session.add_insight("Insight C", produced_by="agent:claude")

        claude = session.get_insights(produced_by="agent:claude")
        assert len(claude) == 2

        all_ins = session.get_insights()
        assert len(all_ins) == 3

    def test_status(self):
        from contextcore.intelligence.session import ContextSession
        session = ContextSession("IT Analysis")
        session.attach("tcs")
        session.add_insight("Test insight", produced_by="test")

        status = session.status()
        assert status["name"] == "IT Analysis"
        assert status["context_count"] == 1
        assert status["insight_count"] == 1

    def test_to_dict(self):
        from contextcore.intelligence.session import ContextSession
        session = ContextSession("Test")
        session.attach("tcs")
        session.add_insight("Test", produced_by="agent")

        d = session.to_dict()
        assert "insights" in d
        assert len(d["insights"]) == 1
        assert d["attached_contexts"] == ["tcs"]


class TestSessionManager:

    def test_create_and_list(self):
        from contextcore.intelligence.session import SessionManager
        mgr = SessionManager()
        s1 = mgr.create("Session A")
        s2 = mgr.create("Session B")

        sessions = mgr.list_sessions()
        assert len(sessions) == 2

    def test_get_session(self):
        from contextcore.intelligence.session import SessionManager
        mgr = SessionManager()
        s = mgr.create("Test")
        assert mgr.get(s.session_id) is not None
        assert mgr.get("nonexistent") is None

    def test_delete_session(self):
        from contextcore.intelligence.session import SessionManager
        mgr = SessionManager()
        s = mgr.create("Test")
        assert mgr.delete(s.session_id) is True
        assert mgr.get(s.session_id) is None
        assert mgr.delete("nonexistent") is False

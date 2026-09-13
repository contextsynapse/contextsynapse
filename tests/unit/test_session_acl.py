"""
Unit tests for session access control with tag-based grants.
"""

import pytest
from contextcore.context.session import ContextSessionManager


@pytest.fixture
def session_mgr(tmp_db_path):
    return ContextSessionManager(db_path=tmp_db_path)


@pytest.fixture
def session(session_mgr):
    return session_mgr.create_session("test-session", owner_agent_id="owner-1")


class TestBasicAccessControl:

    def test_owner_gets_admin(self, session_mgr, session):
        assert session_mgr.check_access(session.session_id, "owner-1", "admin")

    def test_unknown_agent_has_no_access(self, session_mgr, session):
        assert not session_mgr.check_access(session.session_id, "stranger", "read")

    def test_grant_read(self, session_mgr, session):
        session_mgr.grant_access(session.session_id, "reader-1", "read")
        assert session_mgr.check_access(session.session_id, "reader-1", "read")
        assert not session_mgr.check_access(session.session_id, "reader-1", "write")

    def test_grant_write(self, session_mgr, session):
        session_mgr.grant_access(session.session_id, "writer-1", "write")
        assert session_mgr.check_access(session.session_id, "writer-1", "read")
        assert session_mgr.check_access(session.session_id, "writer-1", "write")
        assert not session_mgr.check_access(session.session_id, "writer-1", "admin")

    def test_revoke_access(self, session_mgr, session):
        session_mgr.grant_access(session.session_id, "temp-1", "write")
        assert session_mgr.check_access(session.session_id, "temp-1", "read")
        session_mgr.revoke_access(session.session_id, "temp-1")
        assert not session_mgr.check_access(session.session_id, "temp-1", "read")


class TestTagBasedACL:

    def test_grant_with_allowed_tags(self, session_mgr, session):
        session_mgr.grant_access(
            session.session_id, "analyst-1", "read",
            allowed_tags=["financial", "pii"],
        )
        info = session_mgr.get_agent_access(session.session_id, "analyst-1")
        assert info is not None
        assert info["access_level"] == "read"
        assert "financial" in info["allowed_tags"]
        assert "pii" in info["allowed_tags"]

    def test_grant_without_tags_defaults_empty(self, session_mgr, session):
        session_mgr.grant_access(session.session_id, "basic-1", "read")
        info = session_mgr.get_agent_access(session.session_id, "basic-1")
        assert info is not None
        assert info["allowed_tags"] == []

    def test_update_tags_on_regrant(self, session_mgr, session):
        session_mgr.grant_access(session.session_id, "agent-x", "read", allowed_tags=["a"])
        session_mgr.grant_access(session.session_id, "agent-x", "write", allowed_tags=["a", "b"])
        info = session_mgr.get_agent_access(session.session_id, "agent-x")
        assert info["access_level"] == "write"
        assert set(info["allowed_tags"]) == {"a", "b"}

    def test_get_agent_access_nonexistent(self, session_mgr, session):
        assert session_mgr.get_agent_access(session.session_id, "ghost") is None

    def test_access_list_includes_tags(self, session_mgr, session):
        session_mgr.grant_access(session.session_id, "a1", "read", allowed_tags=["x"])
        session_mgr.grant_access(session.session_id, "a2", "write", allowed_tags=["y", "z"])
        acl = session_mgr.get_access_list(session.session_id)
        by_agent = {entry["agent_id"]: entry for entry in acl}
        assert by_agent["a1"]["allowed_tags"] == ["x"]
        assert set(by_agent["a2"]["allowed_tags"]) == {"y", "z"}


class TestMigrationIdempotency:

    def test_double_init_no_error(self, tmp_db_path):
        """Creating the manager twice should not fail (migrations are idempotent)."""
        mgr1 = ContextSessionManager(db_path=tmp_db_path)
        mgr1.create_session("s1")
        mgr2 = ContextSessionManager(db_path=tmp_db_path)
        sessions = mgr2.list_sessions()
        assert any(s.name == "s1" for s in sessions)

"""Tests for hierarchical context namespace validation."""
import pytest
from contextcore.security.sanitize import validate_namespace, parse_context_path, VALID_SCOPES


class TestValidateNamespace:
    """validate_namespace() accepts flat and hierarchical formats."""

    def test_flat_name_accepted(self):
        """Backward compat: flat names like 'tcs' still pass."""
        assert validate_namespace("tcs") == "tcs"

    def test_flat_name_with_underscores(self):
        assert validate_namespace("india_economy") == "india_economy"

    def test_hierarchical_market(self):
        assert validate_namespace("market:tcs") == "market:tcs"

    def test_hierarchical_market_price(self):
        assert validate_namespace("market:tcs_price") == "market:tcs_price"

    def test_hierarchical_portfolio(self):
        assert validate_namespace("portfolio:p001:holdings") == "portfolio:p001:holdings"

    def test_hierarchical_client(self):
        assert validate_namespace("client:c001:profile") == "client:c001:profile"

    def test_hierarchical_frozen(self):
        assert validate_namespace("frozen:T-20260911-001") == "frozen:T-20260911-001"

    def test_rejects_invalid_scope(self):
        with pytest.raises(ValueError, match="Invalid scope"):
            validate_namespace("badscope:tcs")

    def test_rejects_too_deep(self):
        with pytest.raises(ValueError, match="too deep"):
            validate_namespace("market:a:b:c:d")

    def test_rejects_wildcard_in_segment(self):
        with pytest.raises(ValueError, match="only letters"):
            validate_namespace("market:tcs*")

    def test_rejects_empty(self):
        with pytest.raises(ValueError):
            validate_namespace("")

    def test_rejects_spaces_in_segment(self):
        with pytest.raises(ValueError):
            validate_namespace("market:tcs stock")

    def test_segment_max_length(self):
        long_seg = "a" * 129
        with pytest.raises(ValueError, match="too long"):
            validate_namespace(f"market:{long_seg}")

    def test_rejects_reserved_flat(self):
        with pytest.raises(ValueError, match="Reserved"):
            validate_namespace("lru")


class TestParseContextPath:
    """parse_context_path() splits hierarchical paths into (scope, segments)."""

    def test_flat_defaults_to_market(self):
        scope, parts = parse_context_path("tcs")
        assert scope == "market"
        assert parts == ["tcs"]

    def test_market_scope(self):
        scope, parts = parse_context_path("market:india_economy")
        assert scope == "market"
        assert parts == ["india_economy"]

    def test_portfolio_scope(self):
        scope, parts = parse_context_path("portfolio:p001:holdings")
        assert scope == "portfolio"
        assert parts == ["p001", "holdings"]

    def test_client_scope(self):
        scope, parts = parse_context_path("client:c001:mandate")
        assert scope == "client"
        assert parts == ["c001", "mandate"]

    def test_frozen_scope(self):
        scope, parts = parse_context_path("frozen:T-001")
        assert scope == "frozen"
        assert parts == ["T-001"]


class TestValidScopes:
    def test_contains_all_scopes(self):
        assert VALID_SCOPES == {"market", "portfolio", "client", "frozen"}

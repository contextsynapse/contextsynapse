"""Tests for AIQL input sanitization — prevents SQL/AIQL injection."""

import pytest
from contextcore.security.sanitize import (
    sanitize_aiql_identifier,
    sanitize_aiql_value,
    build_safe_where_clause,
)


class TestSanitizeIdentifier:
    def test_valid_identifiers(self):
        assert sanitize_aiql_identifier("Fact") == "Fact"
        assert sanitize_aiql_identifier("my_label") == "my_label"
        assert sanitize_aiql_identifier("Node123") == "Node123"
        assert sanitize_aiql_identifier("code-file") == "code-file"

    def test_rejects_empty(self):
        with pytest.raises(ValueError):
            sanitize_aiql_identifier("")

    def test_rejects_special_chars(self):
        with pytest.raises(ValueError):
            sanitize_aiql_identifier('Fact"; DROP TABLE')
        with pytest.raises(ValueError):
            sanitize_aiql_identifier("label with spaces")
        with pytest.raises(ValueError):
            sanitize_aiql_identifier("a.b.c")

    def test_rejects_aiql_keywords(self):
        with pytest.raises(ValueError):
            sanitize_aiql_identifier("SELECT")
        with pytest.raises(ValueError):
            sanitize_aiql_identifier("delete")
        with pytest.raises(ValueError):
            sanitize_aiql_identifier("DROP")

    def test_rejects_too_long(self):
        with pytest.raises(ValueError):
            sanitize_aiql_identifier("a" * 200)

    def test_rejects_starting_with_digit(self):
        with pytest.raises(ValueError):
            sanitize_aiql_identifier("123abc")


class TestSanitizeValue:
    def test_normal_values(self):
        assert sanitize_aiql_value("hello") == "hello"
        assert sanitize_aiql_value(42) == "42"
        assert sanitize_aiql_value(True) == "True"

    def test_escapes_quotes(self):
        result = sanitize_aiql_value('value with "quotes"')
        assert '\\"' in result
        assert '"quotes"' not in result

    def test_escapes_backslashes(self):
        result = sanitize_aiql_value("path\\to\\file")
        assert "\\\\" in result

    def test_strips_semicolons(self):
        result = sanitize_aiql_value("value; DROP TABLE users")
        assert ";" not in result

    def test_strips_null_bytes(self):
        result = sanitize_aiql_value("hello\x00world")
        assert "\x00" not in result

    def test_none_returns_empty(self):
        assert sanitize_aiql_value(None) == ""

    def test_truncates_long_values(self):
        long_val = "x" * 10000
        result = sanitize_aiql_value(long_val)
        assert len(result) <= 4096

    def test_injection_payload_1(self):
        """Classic SQL injection attempt."""
        result = sanitize_aiql_value('" OR 1=1 --')
        # The double quote should be escaped
        assert result.startswith('\\"')

    def test_injection_payload_2(self):
        """Statement chaining attempt."""
        result = sanitize_aiql_value('value"; DELETE NODE "x')
        assert ";" not in result
        assert 'DELETE' in result  # DELETE is fine as a value, just can't chain


class TestBuildSafeWhereClause:
    def test_empty_dict(self):
        assert build_safe_where_clause({}) == ""
        assert build_safe_where_clause(None) == ""

    def test_single_condition(self):
        result = build_safe_where_clause({"name": "Alice"})
        assert result == ' WHERE name = "Alice"'

    def test_multiple_conditions(self):
        result = build_safe_where_clause({"name": "Alice", "status": "active"})
        assert "name" in result
        assert "status" in result
        assert "AND" in result

    def test_escapes_values(self):
        result = build_safe_where_clause({"name": 'Bob"; DROP TABLE'})
        assert ";" not in result
        assert '\\"' in result

    def test_rejects_bad_keys(self):
        with pytest.raises(ValueError):
            build_safe_where_clause({"bad key!": "value"})

    def test_rejects_keyword_keys(self):
        with pytest.raises(ValueError):
            build_safe_where_clause({"SELECT": "value"})

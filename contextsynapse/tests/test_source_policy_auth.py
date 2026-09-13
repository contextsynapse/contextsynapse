"""Tests for source policy domain matching and auth error message safety."""

from __future__ import annotations

import inspect
import re

import pytest

from contextsynapse.context.source_policy import (
    ALWAYS_BLOCKED_DOMAINS,
    ALWAYS_BLOCKED_IP_PREFIXES,
    check_source_allowed,
)
from contextsynapse.api import auth as auth_module


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class FakeContext:
    """Minimal context object with a config dict and optional attributes."""

    def __init__(self, config=None, source=None, context_type=None):
        self.config = config or {}
        self.source = source
        self.context_type = context_type


def _policy(**overrides):
    """Build a config dict wrapping a source_policy."""
    return {"source_policy": overrides}


# ===========================================================================
# Source Policy — Domain Matching
# ===========================================================================

class TestExactDomainMatch:
    def test_exact_domain_allowed(self):
        ctx = FakeContext(config=_policy(allowed_domains=["example.com"]))
        allowed, reason = check_source_allowed(ctx, source_url="https://example.com/page")
        assert allowed is True

    def test_exact_domain_rejected(self):
        ctx = FakeContext(config=_policy(allowed_domains=["example.com"]))
        allowed, reason = check_source_allowed(ctx, source_url="https://other.com/page")
        assert allowed is False
        assert "not in allowed" in reason


class TestWwwPrefixStripping:
    def test_www_prefix_stripped_and_matched(self):
        ctx = FakeContext(config=_policy(allowed_domains=["example.com"]))
        allowed, _ = check_source_allowed(ctx, source_url="https://www.example.com/page")
        assert allowed is True

    def test_allowed_has_www_source_does_not(self):
        ctx = FakeContext(config=_policy(allowed_domains=["www.example.com"]))
        allowed, _ = check_source_allowed(ctx, source_url="https://example.com/page")
        assert allowed is True


class TestSubdomainMatching:
    def test_subdomain_of_allowed_domain(self):
        ctx = FakeContext(config=_policy(allowed_domains=["example.com"]))
        allowed, _ = check_source_allowed(ctx, source_url="https://docs.example.com/api")
        assert allowed is True

    def test_deep_subdomain_of_allowed_domain(self):
        ctx = FakeContext(config=_policy(allowed_domains=["example.com"]))
        allowed, _ = check_source_allowed(ctx, source_url="https://a.b.c.example.com/x")
        assert allowed is True

    def test_unrelated_domain_with_same_suffix_rejected(self):
        ctx = FakeContext(config=_policy(allowed_domains=["example.com"]))
        allowed, _ = check_source_allowed(ctx, source_url="https://notexample.com/page")
        assert allowed is False


class TestRelatedDomainMatch:
    def test_related_domain_name_in_subdomain(self):
        """timesofindia.com should match timesofindia.indiatimes.com."""
        ctx = FakeContext(config=_policy(allowed_domains=["timesofindia.com"]))
        allowed, _ = check_source_allowed(
            ctx, source_url="https://timesofindia.indiatimes.com/article"
        )
        assert allowed is True

    def test_related_domain_different_tld(self):
        ctx = FakeContext(config=_policy(allowed_domains=["mycompany.com"]))
        allowed, _ = check_source_allowed(
            ctx, source_url="https://mycompany.co.uk/page"
        )
        assert allowed is True


class TestShortDomainNoLooseMatch:
    def test_short_name_three_chars_no_loose_match(self):
        """Domain names <= 3 chars should NOT get the loose related-domain matching."""
        ctx = FakeContext(config=_policy(allowed_domains=["abc.com"]))
        # "abc.otherdomain.com" should NOT match because "abc" is only 3 chars
        allowed, _ = check_source_allowed(
            ctx, source_url="https://abc.otherdomain.com/page"
        )
        assert allowed is False

    def test_four_char_name_does_match(self):
        ctx = FakeContext(config=_policy(allowed_domains=["abcd.com"]))
        allowed, _ = check_source_allowed(
            ctx, source_url="https://abcd.otherdomain.com/page"
        )
        assert allowed is True


class TestBlockedDomains:
    def test_blocked_domain_rejected(self):
        ctx = FakeContext(config=_policy(
            allowed_domains=["example.com"],
            blocked_domains=["evil.com"],
        ))
        allowed, reason = check_source_allowed(ctx, source_url="https://evil.com/x")
        assert allowed is False
        assert "blocked" in reason.lower()

    def test_blocked_subdomain_rejected(self):
        ctx = FakeContext(config=_policy(blocked_domains=["evil.com"]))
        allowed, _ = check_source_allowed(ctx, source_url="https://sub.evil.com/x")
        assert allowed is False

    def test_blocked_takes_priority_over_allowed(self):
        ctx = FakeContext(config=_policy(
            allowed_domains=["evil.com"],
            blocked_domains=["evil.com"],
        ))
        allowed, _ = check_source_allowed(ctx, source_url="https://evil.com/x")
        assert allowed is False


class TestPathPrefixMatching:
    def test_path_prefix_allowed(self):
        ctx = FakeContext(config=_policy(allowed_domains=["github.com/myorg"]))
        allowed, _ = check_source_allowed(
            ctx, source_url="https://github.com/myorg/repo"
        )
        assert allowed is True

    def test_path_prefix_different_org_rejected(self):
        ctx = FakeContext(config=_policy(allowed_domains=["github.com/myorg"]))
        allowed, _ = check_source_allowed(
            ctx, source_url="https://github.com/otherorg/repo"
        )
        assert allowed is False

    def test_path_prefix_exact_path(self):
        ctx = FakeContext(config=_policy(allowed_domains=["github.com/myorg"]))
        allowed, _ = check_source_allowed(
            ctx, source_url="https://github.com/myorg"
        )
        assert allowed is True


class TestSecurityBlocklist:
    def test_localhost_always_blocked(self):
        ctx = FakeContext()  # No policy at all
        allowed, reason = check_source_allowed(ctx, source_url="http://localhost/admin")
        assert allowed is False
        assert "restricted" in reason.lower() or "blocked" in reason.lower()

    def test_aws_metadata_blocked(self):
        ctx = FakeContext()
        allowed, _ = check_source_allowed(
            ctx, source_url="http://169.254.169.254/latest/meta-data/"
        )
        assert allowed is False

    def test_gcp_metadata_blocked(self):
        ctx = FakeContext()
        allowed, _ = check_source_allowed(
            ctx, source_url="http://metadata.google.internal/computeMetadata/v1/"
        )
        assert allowed is False

    def test_private_ip_10_blocked(self):
        ctx = FakeContext()
        allowed, _ = check_source_allowed(ctx, source_url="http://10.0.0.1/secret")
        assert allowed is False

    def test_private_ip_192_168_blocked(self):
        ctx = FakeContext()
        allowed, _ = check_source_allowed(
            ctx, source_url="http://192.168.1.1/admin"
        )
        assert allowed is False

    def test_private_ip_172_16_blocked(self):
        ctx = FakeContext()
        allowed, _ = check_source_allowed(ctx, source_url="http://172.16.0.1/x")
        assert allowed is False

    def test_loopback_127_blocked(self):
        ctx = FakeContext()
        allowed, _ = check_source_allowed(ctx, source_url="http://127.0.0.1/x")
        assert allowed is False

    def test_security_blocklist_overrides_allowed_domains(self):
        """Even if policy allows localhost, security blocklist wins."""
        ctx = FakeContext(config=_policy(allowed_domains=["localhost"]))
        allowed, _ = check_source_allowed(ctx, source_url="http://localhost/x")
        assert allowed is False


class TestNoPolicyOpenByDefault:
    def test_no_policy_allows_external_url(self):
        ctx = FakeContext()
        allowed, reason = check_source_allowed(
            ctx, source_url="https://anything.example.com/data"
        )
        assert allowed is True
        assert "no source policy" in reason.lower()

    def test_no_policy_allows_files(self):
        ctx = FakeContext()
        allowed, _ = check_source_allowed(ctx, filename="report.pdf")
        assert allowed is True

    def test_no_policy_still_blocks_security_threats(self):
        ctx = FakeContext()
        allowed, _ = check_source_allowed(ctx, source_url="http://0.0.0.0/x")
        assert allowed is False


class TestFileExtensionFiltering:
    def test_allowed_extension_passes(self):
        ctx = FakeContext(config=_policy(allowed_file_extensions=[".pdf", ".md"]))
        allowed, _ = check_source_allowed(ctx, filename="report.pdf")
        assert allowed is True

    def test_disallowed_extension_rejected(self):
        ctx = FakeContext(config=_policy(allowed_file_extensions=[".pdf", ".md"]))
        allowed, reason = check_source_allowed(ctx, filename="malware.exe")
        assert allowed is False
        assert ".exe" in reason

    def test_case_insensitive_extension(self):
        ctx = FakeContext(config=_policy(allowed_file_extensions=[".pdf"]))
        allowed, _ = check_source_allowed(ctx, filename="report.PDF")
        assert allowed is True

    def test_no_extension_filter_allows_all(self):
        ctx = FakeContext(config=_policy(allowed_domains=["example.com"]))
        allowed, _ = check_source_allowed(ctx, filename="anything.xyz")
        assert allowed is True


class TestFileSizeFiltering:
    def test_within_limit_allowed(self):
        ctx = FakeContext(config=_policy(max_file_size_mb=50))
        allowed, _ = check_source_allowed(ctx, file_size_mb=25.0)
        assert allowed is True

    def test_exceeds_limit_rejected(self):
        ctx = FakeContext(config=_policy(max_file_size_mb=50))
        allowed, reason = check_source_allowed(ctx, file_size_mb=75.0)
        assert allowed is False
        assert "exceeds" in reason.lower()

    def test_exact_limit_allowed(self):
        ctx = FakeContext(config=_policy(max_file_size_mb=50))
        allowed, _ = check_source_allowed(ctx, file_size_mb=50.0)
        assert allowed is True

    def test_no_size_limit_allows_large_files(self):
        ctx = FakeContext(config=_policy(allowed_domains=["example.com"]))
        allowed, _ = check_source_allowed(ctx, file_size_mb=9999.0)
        assert allowed is True


# ===========================================================================
# Auth Error Messages — No Implementation Details Leaked
# ===========================================================================

def _collect_auth_error_messages() -> list[str]:
    """Extract all HTTPException detail strings from the auth module source."""
    source = inspect.getsource(auth_module)
    # Match detail="..." and detail='...'
    messages = re.findall(r'detail\s*=\s*["\']([^"\']+)["\']', source)
    return messages


class TestAuthErrorMessagesNoLeaks:
    """Verify that error messages in auth.py do not leak implementation details."""

    @pytest.fixture(autouse=True)
    def _load_messages(self):
        self.messages = _collect_auth_error_messages()
        # Sanity: we should have found at least a few messages
        assert len(self.messages) >= 3, (
            f"Expected at least 3 error messages, found {len(self.messages)}"
        )

    def test_no_bearer_usage_instructions(self):
        """Error messages must not tell attackers the exact auth header format."""
        for msg in self.messages:
            assert "Use: Authorization: Bearer" not in msg, (
                f"Error message leaks auth format: {msg!r}"
            )
            assert "Authorization: Bearer" not in msg, (
                f"Error message leaks auth header name: {msg!r}"
            )

    def test_no_env_var_names_leaked(self):
        """Error messages must not reveal internal env var names."""
        env_vars = [
            "CONTEXTSYNAPSE_ADMIN_KEY",
            "AICONTEXTDB_ADMIN_KEY",
            "CONTEXTSYNAPSE_JWT_SECRET",
            "AICONTEXTDB_JWT_SECRET",
            "CONTEXTSYNAPSE_CORS_ORIGINS",
            "AICONTEXTDB_CORS_ORIGINS",
        ]
        for msg in self.messages:
            for var in env_vars:
                assert var not in msg, (
                    f"Error message leaks env var '{var}': {msg!r}"
                )

    def test_no_stack_trace_hints(self):
        """Error messages should not contain stacktrace-like content."""
        bad_patterns = ["traceback", "File \"", "line ", "raise ", "Exception("]
        for msg in self.messages:
            for pat in bad_patterns:
                assert pat not in msg, (
                    f"Error message contains implementation hint '{pat}': {msg!r}"
                )

    def test_messages_are_user_friendly(self):
        """All error messages should be human-readable sentences (not code)."""
        for msg in self.messages:
            # Should end with a period (proper sentence)
            assert msg.rstrip().endswith("."), (
                f"Error message is not a proper sentence (no period): {msg!r}"
            )
            # Should not contain Python code artifacts
            assert "==" not in msg
            assert "!=" not in msg
            assert "None" not in msg

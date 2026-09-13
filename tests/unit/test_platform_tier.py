import pytest


class TestTierForPlatform:
    def _fn(self):
        from contextcore.api.agent_worker_router import _tier_for_platform
        return _tier_for_platform

    def test_mobile_maps_to_fast(self):
        assert self._fn()("mobile", None) == "fast"

    def test_embedded_maps_to_instant(self):
        assert self._fn()("embedded", None) == "instant"

    def test_desktop_maps_to_standard(self):
        assert self._fn()("desktop", None) == "standard"

    def test_app_maps_to_standard(self):
        assert self._fn()("app", None) == "standard"

    def test_browser_maps_to_fast(self):
        assert self._fn()("browser", None) == "fast"

    def test_unknown_platform_maps_to_standard(self):
        assert self._fn()("wearable", None) == "standard"

    def test_explicit_tier_overrides_platform(self):
        # Even mobile gets "deep" if caller explicitly requested it
        assert self._fn()("mobile", "deep") == "deep"

    def test_explicit_tier_overrides_embedded(self):
        assert self._fn()("embedded", "standard") == "standard"

    def test_none_platform_defaults_to_standard(self):
        assert self._fn()(None, None) == "standard"

    def test_empty_string_platform_defaults_to_standard(self):
        assert self._fn()("", None) == "standard"


class TestPlatformHints:
    def _hints(self, platform, resolved_tier, explicit):
        from contextcore.api.agent_worker_router import _build_platform_hints
        return _build_platform_hints(platform, resolved_tier, explicit)

    def test_hints_include_platform(self):
        hints = self._hints("mobile", "fast", False)
        assert hints["platform"] == "mobile"

    def test_hints_fast_budget(self):
        hints = self._hints("mobile", "fast", False)
        assert hints["token_budget"] == 1500
        assert hints["time_budget_ms"] == 200

    def test_hints_instant_budget(self):
        hints = self._hints("embedded", "instant", False)
        assert hints["token_budget"] == 500
        assert hints["time_budget_ms"] == 50

    def test_hints_deep_budget(self):
        hints = self._hints("desktop", "deep", True)
        assert hints["token_budget"] == 8000
        assert hints["time_budget_ms"] == 5000

    def test_auto_selected_note(self):
        hints = self._hints("mobile", "fast", False)
        assert "auto-selected" in hints["note"]
        assert "mobile" in hints["note"]

    def test_explicit_tier_note(self):
        hints = self._hints("mobile", "deep", True)
        assert "explicitly" in hints["note"]

    def test_none_platform_in_note(self):
        hints = self._hints(None, "standard", False)
        assert "unknown" in hints["note"] or "unknown" in hints["platform"]


class TestOrientAgentTierResolution:
    """Verify orient_agent resolves tier before calling project_context."""

    def test_orient_uses_platform_tier_when_no_explicit_tier(self):
        """Mobile agent without explicit tier should receive 'fast' tier."""

        from contextcore.api import agent_worker_router as m
        # Call _tier_for_platform directly as proxy for the integration
        resolved = m._tier_for_platform("mobile", None)
        assert resolved == "fast"


class TestEndToEndPlatformFlow:
    def test_all_platforms_resolve_to_valid_tiers(self):
        from contextcore.api.agent_worker_router import _tier_for_platform
        valid_tiers = {"instant", "fast", "standard", "deep"}
        platforms = ["desktop", "mobile", "app", "browser", "embedded", "wearable", None, ""]
        for p in platforms:
            t = _tier_for_platform(p, None)
            assert t in valid_tiers, f"Platform {p!r} resolved to invalid tier {t!r}"

    def test_hints_budgets_cover_all_tiers(self):
        from contextcore.api.agent_worker_router import _build_platform_hints
        for tier in ["instant", "fast", "standard", "deep"]:
            hints = _build_platform_hints("desktop", tier, False)
            assert hints["token_budget"] > 0
            assert hints["time_budget_ms"] > 0

    def test_explicit_tier_beats_embedded_default(self):
        from contextcore.api.agent_worker_router import _tier_for_platform, _build_platform_hints
        tier = _tier_for_platform("embedded", "deep")
        assert tier == "deep"
        hints = _build_platform_hints("embedded", tier, tier_was_explicit=True)
        assert hints["token_budget"] == 8000
        assert "explicitly" in hints["note"]

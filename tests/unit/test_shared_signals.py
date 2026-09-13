"""Tests for contextcore.ingestion.universal.shared_signals."""

import pytest

from contextcore.ingestion.universal.shared_signals import (
    Signal,
    detect_signals,
    significance_score,
)


# ── detect_signals ──────────────────────────────────────────────────────────


class TestDetectSignals:

    def test_decision_signal(self):
        sigs = detect_signals("We decided to use PostgreSQL.")
        types = [s.type for s in sigs]
        assert "decision" in types

    def test_action_signal(self):
        sigs = detect_signals("We need to update the deployment script.")
        types = [s.type for s in sigs]
        assert "action" in types

    def test_question_signal(self):
        sigs = detect_signals("Should we migrate to the new API?")
        types = [s.type for s in sigs]
        assert "question" in types

    def test_problem_signal(self):
        sigs = detect_signals("The server crashed after the deploy.")
        types = [s.type for s in sigs]
        assert "problem" in types

    def test_solution_signal(self):
        sigs = detect_signals("I fixed the timeout issue.")
        types = [s.type for s in sigs]
        assert "solution" in types

    def test_clinical_finding_signal(self):
        sigs = detect_signals("Patient was diagnosed with diabetes. HbA1c is 7.2.")
        types = [s.type for s in sigs]
        assert "clinical_finding" in types
        # Should detect both "diagnosed with" and "HbA1c"
        clinical = [s for s in sigs if s.type == "clinical_finding"]
        assert len(clinical) >= 2

    def test_contraindication_signal(self):
        sigs = detect_signals("Metformin is contraindicated in renal failure.")
        types = [s.type for s in sigs]
        assert "contraindication" in types

    def test_guideline_signal(self):
        sigs = detect_signals("Per ADA guidelines recommend annual screening.")
        types = [s.type for s in sigs]
        assert "guideline" in types

    def test_multiple_signals(self):
        text = "There's a bug in login. I fixed it by updating the token check."
        sigs = detect_signals(text)
        types = {s.type for s in sigs}
        assert "problem" in types
        assert "solution" in types

    def test_filter_by_type(self):
        text = "We decided to fix the bug with a workaround."
        sigs = detect_signals(text, types=["decision"])
        assert all(s.type == "decision" for s in sigs)

    def test_empty_text(self):
        assert detect_signals("") == []

    def test_no_signals(self):
        sigs = detect_signals("The weather is nice today.")
        # No decision/action/problem/solution/clinical patterns
        assert len(sigs) == 0

    def test_confidence_range(self):
        sigs = detect_signals("We decided to fix the error using a workaround.")
        for s in sigs:
            assert 0.0 <= s.confidence <= 1.0


# ── significance_score ──────────────────────────────────────────────────────


class TestSignificanceScore:

    def test_ack_ok(self):
        assert significance_score("ok") == pytest.approx(0.1)

    def test_ack_thanks(self):
        assert significance_score("thanks") == pytest.approx(0.1)

    def test_ack_got_it(self):
        assert significance_score("got it") == pytest.approx(0.1)

    def test_ack_yes(self):
        assert significance_score("yes") == pytest.approx(0.1)

    def test_ack_no(self):
        assert significance_score("no") == pytest.approx(0.1)

    def test_short_message(self):
        assert significance_score("hi there") == pytest.approx(0.2)

    def test_question_boost(self):
        score = significance_score("What database should we use for this project?")
        assert score >= 0.7  # 0.5 base + 0.2 question

    def test_decision_boost(self):
        score = significance_score("We decided to use Redis for caching.")
        assert score >= 0.7  # 0.5 base + 0.2 decision

    def test_action_boost(self):
        score = significance_score("We need to refactor the authentication module.")
        assert score >= 0.6  # 0.5 base + 0.1 action

    def test_decision_and_question(self):
        score = significance_score("We decided to use Redis, right?")
        assert score == pytest.approx(0.9, abs=1e-9)  # 0.5 + 0.2 + 0.2

    def test_capped_at_one(self):
        # Decision + question + action should still cap at 1.0
        score = significance_score(
            "We decided to go with X — need to deploy it, right?"
        )
        assert score <= 1.0

    def test_empty_string(self):
        assert significance_score("") == 0.0

    def test_whitespace_only(self):
        assert significance_score("   ") == 0.0

    def test_base_score(self):
        score = significance_score(
            "The application uses a microservices architecture."
        )
        assert score == pytest.approx(0.5)

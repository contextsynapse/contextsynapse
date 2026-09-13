"""Tests for Sentiment Decay Model."""
import pytest
from datetime import datetime, timezone, timedelta


class TestSentimentSignal:

    def test_positive_high_impact(self):
        from contextcore.intelligence.sentiment_decay import SentimentSignal
        sig = SentimentSignal(entity="TCS", sentiment="positive", impact="high")
        assert sig.value == 1.0  # positive(1.0) × high(1.0)
        assert sig.decay_rate == 0.10  # logarithmic α for high impact

    def test_negative_medium_impact(self):
        from contextcore.intelligence.sentiment_decay import SentimentSignal
        sig = SentimentSignal(entity="TCS", sentiment="negative", impact="medium")
        assert sig.value == -0.7  # negative(-1.0) × medium(0.7)

    def test_no_decay_within_silence_threshold(self):
        from contextcore.intelligence.sentiment_decay import SentimentSignal, SILENCE_THRESHOLD_HOURS
        sig = SentimentSignal(entity="TCS", sentiment="positive", impact="medium",
                              timestamp="2026-08-31T10:00:00+00:00")
        t0 = datetime(2026, 8, 31, 10, 0, tzinfo=timezone.utc)
        t_within = t0 + timedelta(hours=SILENCE_THRESHOLD_HOURS - 1)

        # No decay within silence threshold
        assert sig.decayed_value(t_within, silence_start=t0) == sig.value

    def test_gradual_decay_after_silence(self):
        from contextcore.intelligence.sentiment_decay import SentimentSignal
        sig = SentimentSignal(entity="TCS", sentiment="positive", impact="medium",
                              timestamp="2026-08-31T10:00:00+00:00")
        t0 = datetime(2026, 8, 31, 10, 0, tzinfo=timezone.utc)
        t_1day = t0 + timedelta(days=1)
        t_1week = t0 + timedelta(weeks=1)
        t_1month = t0 + timedelta(days=30)

        val_1day = sig.decayed_value(t_1day, silence_start=t0)
        val_1week = sig.decayed_value(t_1week, silence_start=t0)
        val_1month = sig.decayed_value(t_1month, silence_start=t0)

        # Logarithmic: gradual fade, never hits zero
        assert val_1day > val_1week > val_1month  # monotonically decreasing
        assert val_1day > sig.value * 0.40    # still substantial after 1 day
        assert val_1week > sig.value * 0.20   # still present after 1 week
        assert val_1month > 0                 # never hits zero

    def test_breaking_news_decays_slowly(self):
        from contextcore.intelligence.sentiment_decay import SentimentSignal
        breaking = SentimentSignal(entity="TCS", sentiment="positive", impact="breaking",
                                    timestamp="2026-08-31T10:00:00+00:00")
        rumor = SentimentSignal(entity="TCS", sentiment="positive", impact="rumor",
                                 timestamp="2026-08-31T10:00:00+00:00")

        t_later = datetime(2026, 8, 31, 20, 0, tzinfo=timezone.utc)  # 10 hours

        # Breaking news retains more value than rumor
        assert breaking.decayed_value(t_later) > rumor.decayed_value(t_later)


class TestSentimentDecayEngine:

    def test_single_signal(self):
        from contextcore.intelligence.sentiment_decay import SentimentDecayEngine
        engine = SentimentDecayEngine()
        engine.add_signal("TCS", "positive", "high", evidence="Won $2.5B deal")
        score = engine.get_score("TCS")
        assert score.direction == "positive"
        assert score.normalized > 0.5

    def test_reinforcement(self):
        from contextcore.intelligence.sentiment_decay import SentimentDecayEngine
        engine = SentimentDecayEngine()
        engine.add_signal("TCS", "positive", "high")
        score1 = engine.get_score("TCS")

        engine.add_signal("TCS", "positive", "medium")
        score2 = engine.get_score("TCS")

        # Reinforced should be stronger
        assert score2.score > score1.score

    def test_opposite_signals(self):
        from contextcore.intelligence.sentiment_decay import SentimentDecayEngine
        engine = SentimentDecayEngine()
        engine.add_signal("TCS", "positive", "high")
        engine.add_signal("TCS", "negative", "high")
        score = engine.get_score("TCS")

        # Should roughly cancel out
        assert abs(score.normalized) < 0.3

    def test_holds_value_short_term(self):
        from contextcore.intelligence.sentiment_decay import SentimentDecayEngine
        engine = SentimentDecayEngine()
        recent = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
        engine.add_signal("TCS", "positive", "high", timestamp=recent)

        score = engine.get_score("TCS")
        # Only 2 hours ago — should still be strong (within silence threshold)
        assert score.normalized > 0.8
        assert score.direction == "positive"

    def test_gradual_fade_long_term(self):
        from contextcore.intelligence.sentiment_decay import SentimentDecayEngine
        engine = SentimentDecayEngine()
        old = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
        engine.add_signal("TCS", "positive", "medium", timestamp=old)

        score = engine.get_score("TCS")
        # 7 days old with no new signals — should have faded noticeably
        assert score.normalized < 0.8
        assert score.normalized > 0.2  # but not gone
        assert score.direction == "positive"

    def test_no_signals_neutral(self):
        from contextcore.intelligence.sentiment_decay import SentimentDecayEngine
        engine = SentimentDecayEngine()
        score = engine.get_score("TCS")
        assert score.direction == "neutral"
        assert score.score == 0.0

    def test_timeline(self):
        from contextcore.intelligence.sentiment_decay import SentimentDecayEngine
        engine = SentimentDecayEngine()
        engine.add_signal("TCS", "positive", "high")
        timeline = engine.get_timeline("TCS", hours=2, resolution_minutes=30)
        assert len(timeline) >= 4  # 2 hours / 30 min = at least 4 points
        # Most recent should be strongest
        assert timeline[-1]["score"] >= timeline[0]["score"]

    def test_get_all_scores(self):
        from contextcore.intelligence.sentiment_decay import SentimentDecayEngine
        engine = SentimentDecayEngine()
        engine.add_signal("TCS", "positive", "high")
        engine.add_signal("Infosys", "negative", "medium")
        scores = engine.get_all_scores()
        assert "TCS" in scores
        assert "Infosys" in scores
        assert scores["TCS"].direction == "positive"
        assert scores["Infosys"].direction == "negative"

    def test_strength_levels(self):
        from contextcore.intelligence.sentiment_decay import SentimentDecayEngine
        engine = SentimentDecayEngine()

        # Strong: multiple high-impact same-direction
        engine.add_signal("TCS", "positive", "breaking")
        engine.add_signal("TCS", "positive", "high")
        score = engine.get_score("TCS")
        assert score.strength == "strong"

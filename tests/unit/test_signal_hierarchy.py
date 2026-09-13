"""Tests for Signal Hierarchy — context linking and signal propagation."""
import pytest


def _make_signal(signal_type="sentiment_reversal", entity="Oil Price", severity="warning"):
    from contextcore.intelligence.signals import Signal
    return Signal(type=signal_type, entity_name=entity, severity=severity,
                  details={"from": "stable", "to": "spike"})


class TestContextLinks:
    """Hierarchy link creation and traversal."""

    def test_add_link_and_traverse_downstream(self):
        from contextcore.intelligence.signal_hierarchy import SignalHierarchy, ContextLink

        h = SignalHierarchy()
        h.add_link(ContextLink(upstream="Global Macro", downstream="India Economy"))
        h.add_link(ContextLink(upstream="India Economy", downstream="IT Services"))
        h.add_link(ContextLink(upstream="IT Services", downstream="TCS"))

        downstream = h.get_downstream("Global Macro")
        assert "India Economy" in downstream
        assert "IT Services" in downstream
        assert "TCS" in downstream
        # Order: closest first
        assert downstream.index("India Economy") < downstream.index("TCS")

    def test_traverse_upstream(self):
        from contextcore.intelligence.signal_hierarchy import SignalHierarchy, ContextLink

        h = SignalHierarchy()
        h.add_link(ContextLink(upstream="Global Macro", downstream="India Economy"))
        h.add_link(ContextLink(upstream="India Economy", downstream="TCS"))

        upstream = h.get_upstream("TCS")
        assert "India Economy" in upstream
        assert "Global Macro" in upstream
        assert upstream.index("India Economy") < upstream.index("Global Macro")

    def test_full_chain(self):
        from contextcore.intelligence.signal_hierarchy import SignalHierarchy, ContextLink

        h = SignalHierarchy()
        h.add_link(ContextLink(upstream="Global Macro", downstream="India Economy"))
        h.add_link(ContextLink(upstream="India Economy", downstream="TCS"))
        h.add_link(ContextLink(upstream="TCS", downstream="TCS Products"))

        chain = h.get_full_chain("India Economy")
        assert "Global Macro" in chain["upstream"]
        assert "TCS" in chain["downstream"]

    def test_multiple_downstream_branches(self):
        from contextcore.intelligence.signal_hierarchy import SignalHierarchy, ContextLink

        h = SignalHierarchy()
        h.add_link(ContextLink(upstream="IT Services", downstream="TCS"))
        h.add_link(ContextLink(upstream="IT Services", downstream="Infosys"))
        h.add_link(ContextLink(upstream="IT Services", downstream="Wipro"))

        downstream = h.get_downstream("IT Services")
        assert len(downstream) == 3
        assert "TCS" in downstream
        assert "Infosys" in downstream
        assert "Wipro" in downstream


class TestSignalPropagation:
    """Signals propagate downstream with weight decay."""

    def test_signal_propagates_downstream(self):
        from contextcore.intelligence.signal_hierarchy import SignalHierarchy, ContextLink

        h = SignalHierarchy()
        h.add_link(ContextLink(upstream="Global Macro", downstream="India Economy", weight=0.8))
        h.add_link(ContextLink(upstream="India Economy", downstream="TCS", weight=0.7))

        signal = _make_signal()
        propagated = h.propagate_signal(signal, "Global Macro")

        assert len(propagated) == 2
        # India Economy gets it first
        india = [p for p in propagated if p.target_context == "India Economy"]
        assert len(india) == 1
        assert india[0].weight == 0.8
        assert india[0].hop_count == 1

        # TCS gets it second with decayed weight
        tcs = [p for p in propagated if p.target_context == "TCS"]
        assert len(tcs) == 1
        assert abs(tcs[0].weight - 0.56) < 0.01  # 0.8 * 0.7
        assert tcs[0].hop_count == 2

    def test_weight_decay_stops_propagation(self):
        from contextcore.intelligence.signal_hierarchy import SignalHierarchy, ContextLink

        h = SignalHierarchy()
        h.add_link(ContextLink(upstream="A", downstream="B", weight=0.3))
        h.add_link(ContextLink(upstream="B", downstream="C", weight=0.3))
        h.add_link(ContextLink(upstream="C", downstream="D", weight=0.3))

        signal = _make_signal()
        propagated = h.propagate_signal(signal, "A", min_weight=0.1)

        # A→B (0.3), B→C (0.09 < 0.1 threshold) — should stop at B
        targets = [p.target_context for p in propagated]
        assert "B" in targets
        assert "C" not in targets  # weight too low

    def test_signal_type_filtering(self):
        from contextcore.intelligence.signal_hierarchy import SignalHierarchy, ContextLink

        h = SignalHierarchy()
        h.add_link(ContextLink(
            upstream="Global Macro", downstream="TCS",
            propagate_signals=["threshold_breach"],  # only threshold, not reversal
        ))

        # sentiment_reversal should NOT propagate
        signal = _make_signal(signal_type="sentiment_reversal")
        propagated = h.propagate_signal(signal, "Global Macro")
        assert len(propagated) == 0

        # threshold_breach SHOULD propagate
        signal = _make_signal(signal_type="threshold_breach")
        propagated = h.propagate_signal(signal, "Global Macro")
        assert len(propagated) == 1

    def test_no_circular_propagation(self):
        from contextcore.intelligence.signal_hierarchy import SignalHierarchy, ContextLink

        h = SignalHierarchy()
        h.add_link(ContextLink(upstream="A", downstream="B"))
        h.add_link(ContextLink(upstream="B", downstream="C"))
        h.add_link(ContextLink(upstream="C", downstream="A"))  # cycle!

        signal = _make_signal()
        propagated = h.propagate_signal(signal, "A")

        # Should visit B and C but NOT loop back to A
        targets = [p.target_context for p in propagated]
        assert "B" in targets
        assert "C" in targets
        assert len(propagated) == 2  # no infinite loop


class TestLateralPropagation:
    """Signals flow sideways between peers."""

    def test_lateral_link(self):
        from contextcore.intelligence.signal_hierarchy import SignalHierarchy, ContextLink

        h = SignalHierarchy()
        h.add_lateral_link(ContextLink(upstream="TCS", downstream="Infosys", relationship="competes_with"))
        h.add_lateral_link(ContextLink(upstream="TCS", downstream="Wipro", relationship="competes_with"))

        peers = h.get_lateral("TCS")
        assert "Infosys" in peers
        assert "Wipro" in peers

        # Bidirectional
        peers_of_infosys = h.get_lateral("Infosys")
        assert "TCS" in peers_of_infosys

    def test_lateral_signal_propagation(self):
        from contextcore.intelligence.signal_hierarchy import SignalHierarchy, ContextLink

        h = SignalHierarchy()
        h.add_lateral_link(ContextLink(
            upstream="TCS", downstream="Infosys",
            relationship="competes_with", weight=0.8,
        ))

        signal = _make_signal(entity="TCS", signal_type="sentiment_reversal")
        propagated = h.propagate_signal(signal, "TCS")

        infosys_props = [p for p in propagated if p.target_context == "Infosys"]
        assert len(infosys_props) == 1
        assert infosys_props[0].details["direction"] == "lateral"
        # Lateral weight = 0.8 * 0.7 (lateral multiplier)
        assert abs(infosys_props[0].weight - 0.56) < 0.01

    def test_mixed_propagation_all_directions(self):
        """Signal starts at India Economy, flows up, down, and sideways."""
        from contextcore.intelligence.signal_hierarchy import SignalHierarchy, ContextLink

        h = SignalHierarchy()
        # Hierarchy: Global → India → IT Services → TCS
        h.add_link(ContextLink(upstream="Global Macro", downstream="India Economy", weight=0.8))
        h.add_link(ContextLink(upstream="India Economy", downstream="IT Services", weight=0.8))
        h.add_link(ContextLink(upstream="IT Services", downstream="TCS", weight=0.8))
        # Lateral: India ↔ China (peer countries)
        h.add_lateral_link(ContextLink(upstream="India Economy", downstream="China Economy",
                                        relationship="trade_partner", weight=0.6))

        # Signal starts at India Economy
        signal = _make_signal(entity="RBI Rate Hike", signal_type="sentiment_reversal")
        propagated = h.propagate_signal(signal, "India Economy")

        targets = [p.target_context for p in propagated]
        directions = {p.target_context: p.details["direction"] for p in propagated}

        # Should flow downstream
        assert "IT Services" in targets
        assert directions["IT Services"] == "downstream"
        # Should flow further downstream
        assert "TCS" in targets
        assert directions["TCS"] == "downstream"
        # Should flow upstream
        assert "Global Macro" in targets
        assert directions["Global Macro"] == "upstream"
        # Should flow laterally
        assert "China Economy" in targets
        assert directions["China Economy"] == "lateral"

    def test_supply_chain_lateral(self):
        from contextcore.intelligence.signal_hierarchy import SignalHierarchy, ContextLink

        h = SignalHierarchy()
        h.add_lateral_link(ContextLink(
            upstream="TSMC", downstream="Apple",
            relationship="supplies_to", weight=0.9,
        ))
        h.add_lateral_link(ContextLink(
            upstream="TSMC", downstream="NVIDIA",
            relationship="supplies_to", weight=0.9,
        ))

        signal = _make_signal(entity="TSMC", signal_type="sentiment_reversal")
        propagated = h.propagate_signal(signal, "TSMC")

        targets = [p.target_context for p in propagated]
        assert "Apple" in targets
        assert "NVIDIA" in targets

    def test_get_all_connected(self):
        from contextcore.intelligence.signal_hierarchy import SignalHierarchy, ContextLink

        h = SignalHierarchy()
        h.add_link(ContextLink(upstream="Global Macro", downstream="India Economy"))
        h.add_link(ContextLink(upstream="India Economy", downstream="TCS"))
        h.add_lateral_link(ContextLink(upstream="TCS", downstream="Infosys", relationship="competes_with"))

        connected = h.get_all_connected("TCS")
        assert "India Economy" in connected["upstream"]
        assert "Infosys" in connected["lateral"]
        assert connected["downstream"] == []  # TCS has no downstream


class TestUpstreamContext:
    """Build context briefing from upstream signals."""

    def test_upstream_briefing(self):
        from contextcore.intelligence.signal_hierarchy import SignalHierarchy, ContextLink

        h = SignalHierarchy()
        h.set_level("Global Macro", "world")
        h.set_level("India Economy", "country")
        h.set_level("TCS", "company")
        h.add_link(ContextLink(upstream="Global Macro", downstream="India Economy"))
        h.add_link(ContextLink(upstream="India Economy", downstream="TCS"))

        # Propagate a signal so there's history
        signal = _make_signal(entity="Oil Price")
        h.propagate_signal(signal, "Global Macro")

        briefing = h.build_upstream_context("TCS")
        assert len(briefing) == 2
        assert briefing[0]["context"] == "India Economy"
        assert briefing[0]["level"] == "country"
        assert briefing[1]["context"] == "Global Macro"
        assert briefing[1]["level"] == "world"


class TestDefaultHierarchy:
    """Built-in default hierarchy."""

    def test_default_has_world_to_country_links(self):
        from contextcore.intelligence.signal_hierarchy import build_default_hierarchy

        h = build_default_hierarchy()

        downstream = h.get_downstream("Global Macro")
        assert "India Economy" in downstream
        assert "US Economy" in downstream
        assert "China Economy" in downstream

    def test_default_serialization(self):
        from contextcore.intelligence.signal_hierarchy import build_default_hierarchy

        h = build_default_hierarchy()
        d = h.to_dict()
        assert len(d["links"]) > 0
        assert "Global Macro" in d["levels"]

        from contextcore.intelligence.signal_hierarchy import SignalHierarchy
        restored = SignalHierarchy.from_dict(d)
        assert "India Economy" in restored.get_downstream("Global Macro")

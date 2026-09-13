"""Context Intelligence Engine — active context curation."""
from .config import AutonomyLevel, IntelligenceConfig
from .event_bus import EventBus, LocalEventBus, get_event_bus

__all__ = [
    "AutonomyLevel", "IntelligenceConfig",
    "EventBus", "LocalEventBus", "get_event_bus",
    "start_intelligence", "get_intelligence_stats",
]

_modules = {}


def start_intelligence(config: IntelligenceConfig = None, graph_registry=None):
    """Start all intelligence modules and wire event subscriptions."""
    global _modules
    cfg = config or IntelligenceConfig.from_env()
    bus = get_event_bus()

    from .source_watcher import SourceWatcher
    from .feedback_loop import FeedbackLoop
    from .conflict_detector import ConflictDetector
    from .context_radar import ContextRadar

    watcher = SourceWatcher(cfg, bus, graph_registry)
    feedback = FeedbackLoop(cfg, bus)
    conflicts = ConflictDetector(cfg, bus)
    radar = ContextRadar(cfg, bus)

    # Wire event subscriptions (matches spec event routing table)
    if cfg.conflict_detector != "off":
        bus.subscribe("source_changed", conflicts.on_source_changed)
    if cfg.context_radar != "off":
        bus.subscribe("source_changed", radar.on_source_changed)
        bus.subscribe("feedback_received", radar.on_feedback_received)
        bus.subscribe("conflict_found", radar.on_conflict_found)

    _modules = {
        "source_watcher": watcher,
        "feedback_loop": feedback,
        "conflict_detector": conflicts,
        "context_radar": radar,
    }
    return _modules


def get_intelligence_stats() -> dict:
    """Get aggregated stats from all modules."""
    stats = {}
    for name, mod in _modules.items():
        if hasattr(mod, "get_stats"):
            stats[name] = mod.get_stats()
    return stats

"""Shared signal detection for the universal ingestion pipeline.

Regex-based detection of decision, action, question, problem, solution,
and clinical signal types.  Each hit is returned as a Signal dataclass
with type, matched text, and confidence score.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Dict, List, Optional, Pattern, Set

# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class Signal:
    """A single detected signal in a piece of text."""
    type: str
    text: str
    confidence: float


# ---------------------------------------------------------------------------
# Pattern registry  (compiled once at import time)
# ---------------------------------------------------------------------------

_SIGNAL_PATTERNS: Dict[str, Pattern] = {
    "decision": re.compile(
        r"(?i)\b(?:decided\s+to|let'?s\s+go\s+with|we'?ll\s+use|going\s+with"
        r"|chose|chosen|the\s+plan\s+is\s+to|we\s+agreed|final\s+decision"
        r"|settled\s+on)\b"
    ),
    "action": re.compile(
        r"(?i)\b(?:need\s+to|TODO|will\s+do|should|must|have\s+to|going\s+to"
        r"|plan\s+to|action\s+item|next\s+step|deadline)\b"
    ),
    "question": re.compile(r"\?\s*$", re.MULTILINE),
    "problem": re.compile(
        r"(?i)\b(?:error|failed|issue|bug|broken|doesn'?t\s+work|can'?t"
        r"|exception|crash(?:ed)?|timeout|404|500)\b"
    ),
    "solution": re.compile(
        r"(?i)\b(?:fix(?:ed)?|solved|workaround|the\s+solution|try\s+this"
        r"|you\s+can\s+use|resolved|the\s+answer|here'?s\s+how|to\s+fix\s+this)\b"
    ),
    "clinical_finding": re.compile(
        r"(?i)\b(?:diagnosed\s+with|lab\s+results|HbA1c|blood\s+pressure|BMI"
        r"|positive\s+for|negative\s+for|elevated|reduced)\b"
    ),
    "contraindication": re.compile(
        r"(?i)\b(?:contraindicated|do\s+not\s+use|avoid|not\s+recommended|adverse)\b"
    ),
    "guideline": re.compile(
        r"(?i)\b(?:guidelines?\s+recommend|standard\s+of\s+care"
        r"|per\s+ADA|per\s+WHO|per\s+CDC)\b"
    ),
}

# Confidence per signal type (higher = more decisive)
_CONFIDENCE: Dict[str, float] = {
    "decision": 0.90,
    "action": 0.80,
    "question": 0.75,
    "problem": 0.85,
    "solution": 0.85,
    "clinical_finding": 0.90,
    "contraindication": 0.95,
    "guideline": 0.90,
}

# ---------------------------------------------------------------------------
# Acknowledgment pattern (for significance scoring)
# ---------------------------------------------------------------------------

_ACK_PATTERN = re.compile(
    r"^(?:ok|okay|thanks|thank\s+you|got\s+it|yes|no|sure|right|yep|nope"
    r"|agreed|ack|roger|k|yup|nah|cool|fine|alright|understood)[.!]?$",
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def detect_signals(text: str, types: Optional[List[str]] = None) -> List[Signal]:
    """Detect signals in *text* using regex patterns.

    Parameters
    ----------
    text:
        The text to scan.
    types:
        Optional list of signal type names to restrict detection to.
        When ``None`` all known types are checked.

    Returns
    -------
    List[Signal]
        Detected signals ordered by occurrence in text.
    """
    if not text:
        return []

    allowed: Optional[Set[str]] = set(types) if types else None
    signals: List[Signal] = []

    for sig_type, pattern in _SIGNAL_PATTERNS.items():
        if allowed is not None and sig_type not in allowed:
            continue
        for m in pattern.finditer(text):
            signals.append(Signal(
                type=sig_type,
                text=m.group(0),
                confidence=_CONFIDENCE.get(sig_type, 0.7),
            ))

    # Sort by position in the original text
    signals.sort(key=lambda s: text.index(s.text))
    return signals


def significance_score(text: str) -> float:
    """Score the significance of *text* on a 0.0 – 1.0 scale.

    Heuristics
    ----------
    * Pure acknowledgment tokens  → 0.1
    * Very short text (< 20 chars) → 0.2
    * Base                         → 0.5
    * Contains a question mark     → +0.2
    * Contains a decision pattern  → +0.2
    * Contains an action pattern   → +0.1
    * Capped at 1.0
    """
    if not text or not text.strip():
        return 0.0

    stripped = text.strip()

    # Pure acknowledgment
    if _ACK_PATTERN.match(stripped):
        return 0.1

    # Very short
    if len(stripped) < 20:
        return 0.2

    score = 0.5

    if "?" in stripped:
        score += 0.2
    if _SIGNAL_PATTERNS["decision"].search(stripped):
        score += 0.2
    if _SIGNAL_PATTERNS["action"].search(stripped):
        score += 0.1

    return min(score, 1.0)

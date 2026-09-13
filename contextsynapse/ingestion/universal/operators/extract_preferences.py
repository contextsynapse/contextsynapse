"""Extract user preferences and interests from conversation turns."""
from __future__ import annotations

import re
import logging
from collections import Counter
from typing import Any, Dict, List

from .base import StageOperator
from ..ingest_content import Chunk

logger = logging.getLogger(__name__)

# Patterns that indicate user interest/preference
_INTEREST_PATTERNS = [
    r"(?:I(?:'m| am) (?:interested in|curious about|working on|learning|studying|building))\s+(.+?)(?:\.|,|$)",
    r"(?:tell me (?:about|more about))\s+(.+?)(?:\.|,|\?|$)",
    r"(?:how (?:do|does|can|should) (?:I|we|you))\s+(.+?)(?:\.|,|\?|$)",
    r"(?:what (?:is|are|about))\s+(.+?)(?:\.|,|\?|$)",
]

_PREFERENCE_PATTERNS = [
    r"(?:I (?:prefer|like|want|need|use|always))\s+(.+?)(?:\.|,|$)",
    r"(?:(?:let's|we should) (?:use|go with|try))\s+(.+?)(?:\.|,|$)",
]


class ExtractPreferencesOperator(StageOperator):
    """Detect user interests and preferences from conversation turns."""

    name = "extract_preferences"

    def process(self, chunks: List[Chunk], graph_ctx) -> List[Chunk]:
        # Only process user turns
        user_chunks = [c for c in chunks if c.metadata.get("role") == "user"]
        if not user_chunks:
            return chunks

        # Extract interests from user questions/statements
        interest_counts = Counter()
        preferences = []

        for chunk in user_chunks:
            text = chunk.content.strip()
            if not text or len(text) < 10:
                continue

            # Extract interests
            for pattern in _INTEREST_PATTERNS:
                for match in re.finditer(pattern, text, re.IGNORECASE):
                    topic = match.group(1).strip()[:80]
                    if len(topic) > 3:
                        interest_counts[topic.lower()] += 1

            # Extract preferences
            for pattern in _PREFERENCE_PATTERNS:
                for match in re.finditer(pattern, text, re.IGNORECASE):
                    pref = match.group(1).strip()[:100]
                    if len(pref) > 3:
                        preferences.append(pref)

        # Also detect repeated topics from user turns (keyword frequency)
        all_user_text = " ".join(c.content for c in user_chunks).lower()
        words = re.findall(r'\b[a-z]{4,}\b', all_user_text)
        word_freq = Counter(words)
        # Filter common stop words
        stop = {"that", "this", "with", "from", "have", "been", "will", "would", "could",
                "should", "about", "their", "there", "these", "which", "your", "what",
                "when", "where", "some", "other", "more", "also", "than", "into", "them",
                "very", "just", "like", "only", "over", "such", "after", "most", "make",
                "does", "each", "much", "need", "help", "want", "here", "know", "well",
                "back", "good", "give", "many", "then", "take", "come", "made", "find",
                "sure", "tell", "please", "thank", "thanks", "okay", "using", "used"}
        for w in stop:
            word_freq.pop(w, None)

        # Top keywords as additional interests
        for word, count in word_freq.most_common(5):
            if count >= 2:
                interest_counts[word] += count

        # Create Interest nodes (top interests by frequency)
        created_interests = 0
        for topic, count in interest_counts.most_common(10):
            if count < 1:
                continue
            node_id = graph_ctx.add_node("Interest", {
                "name": topic.title(),
                "description": f"User interest detected from conversation ({count} mentions)",
                "frequency": count,
            })
            # Link to session
            if graph_ctx.session_id:
                graph_ctx.add_edge(graph_ctx.session_id, node_id, "INTERESTED_IN")
            created_interests += 1

        # Create Preference nodes (deduplicated)
        seen_prefs = set()
        created_prefs = 0
        for pref in preferences:
            key = pref.lower()[:50]
            if key in seen_prefs:
                continue
            seen_prefs.add(key)
            node_id = graph_ctx.add_node("Preference", {
                "statement": pref,
                "category": "user_preference",
                "confidence": 0.7,
            })
            if graph_ctx.session_id:
                graph_ctx.add_edge(graph_ctx.session_id, node_id, "PREFERS")
            created_prefs += 1

        graph_ctx.log(f"Extracted {created_interests} interests, {created_prefs} preferences")
        graph_ctx.stage_complete(items=len(user_chunks), nodes_created=created_interests + created_prefs)

        return chunks

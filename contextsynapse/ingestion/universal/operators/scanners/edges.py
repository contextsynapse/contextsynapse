"""Edge inference — connect SDLC nodes using keyword overlap + path matching."""
from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any, Dict, List

logger = logging.getLogger(__name__)


def _text_of(node: Dict) -> str:
    """Extract all text from a node's properties for keyword matching."""
    parts = []
    for k, v in node.get("properties", {}).items():
        if isinstance(v, str) and not k.startswith("_"):
            parts.append(v.lower())
    return " ".join(parts)


def _keyword_overlap(text_a: str, text_b: str, min_word_len: int = 3) -> int:
    """Count overlapping meaningful words between two texts."""
    words_a = set(w for w in re.split(r'\W+', text_a.lower()) if len(w) > min_word_len)
    words_b = set(w for w in re.split(r'\W+', text_b.lower()) if len(w) > min_word_len)
    return len(words_a & words_b)


def infer_edges(nodes: List[Dict]) -> List[Dict]:
    """Infer SDLC edges between nodes using path matching + keyword overlap.

    Edge types: TESTS, EXPOSES, GOVERNS, IMPLEMENTS, SATISFIES, AFFECTS, DEPENDS_ON.
    """
    edges = []
    seen = set()

    # Guard: skip any element that isn't a proper node dict
    nodes = [n for n in nodes if isinstance(n, dict) and "label" in n and "id" in n]

    code_nodes = [n for n in nodes if n["label"] == "CodeModule"]
    test_nodes = [n for n in nodes if n["label"] == "TestCase"]
    api_nodes = [n for n in nodes if n["label"] == "APIContract"]
    arch_nodes = [n for n in nodes if n["label"] == "ArchDecision"]
    req_nodes = [n for n in nodes if n["label"] in ("Requirement", "UserStory", "Goal")]
    issue_nodes = [n for n in nodes if n["label"] == "KnownIssue"]

    def _add(label, source_id, target_id):
        key = (source_id, target_id, label)
        if key not in seen:
            seen.add(key)
            edges.append({"label": label, "source": source_id, "target": target_id})

    # TESTS: TestCase → CodeModule
    for tc in test_nodes:
        tc_text = _text_of(tc)
        tc_path = tc.get("properties", {}).get("how", "").lower()
        best_cm, best_score = None, 0
        for cm in code_nodes:
            cm_stem = Path(cm["properties"].get("path", "")).stem
            score = 5 if (cm_stem and cm_stem in tc_path) else 0
            score += _keyword_overlap(tc_text, _text_of(cm))
            if score > best_score:
                best_score, best_cm = score, cm
        if best_cm and best_score >= 2:
            _add("TESTS", tc["id"], best_cm["id"])

    # EXPOSES: CodeModule → APIContract
    for api in api_nodes:
        api_source = api["properties"].get("source", "").lower()
        api_text = _text_of(api)
        best_cm, best_score = None, 0
        for cm in code_nodes:
            cm_path = cm["properties"].get("path", "").lower()
            score = 10 if (cm_path and cm_path == api_source) else 0
            score += _keyword_overlap(api_text, _text_of(cm))
            if score > best_score:
                best_score, best_cm = score, cm
        if best_cm and best_score >= 1:
            _add("EXPOSES", best_cm["id"], api["id"])

    # GOVERNS: ArchDecision → CodeModule
    for arch in arch_nodes:
        arch_text = _text_of(arch)
        best_cm, best_score = None, 0
        for cm in code_nodes:
            score = _keyword_overlap(arch_text, _text_of(cm))
            if score > best_score:
                best_score, best_cm = score, cm
        if best_cm and best_score >= 1:
            _add("GOVERNS", arch["id"], best_cm["id"])
        elif code_nodes:
            _add("GOVERNS", arch["id"], code_nodes[0]["id"])

    # IMPLEMENTS: CodeModule → Requirement
    for req in req_nodes:
        req_text = _text_of(req)
        best_cm, best_score = None, 0
        for cm in code_nodes:
            score = _keyword_overlap(req_text, _text_of(cm))
            if score > best_score:
                best_score, best_cm = score, cm
        if best_cm and best_score >= 2:
            _add("IMPLEMENTS", best_cm["id"], req["id"])

    # SATISFIES: TestCase → Requirement
    for req in req_nodes:
        req_text = _text_of(req)
        best_tc, best_score = None, 0
        for tc in test_nodes:
            score = _keyword_overlap(req_text, _text_of(tc))
            if score > best_score:
                best_score, best_tc = score, tc
        if best_tc and best_score >= 2:
            _add("SATISFIES", best_tc["id"], req["id"])

    # AFFECTS: KnownIssue → CodeModule
    for issue in issue_nodes:
        issue_text = _text_of(issue)
        best_cm, best_score = None, 0
        for cm in code_nodes:
            score = _keyword_overlap(issue_text, _text_of(cm))
            if score > best_score:
                best_score, best_cm = score, cm
        if best_cm and best_score >= 2:
            _add("AFFECTS", issue["id"], best_cm["id"])

    # DEPENDS_ON: CodeModule → CodeModule
    for cm_a in code_nodes:
        a_text = _text_of(cm_a)
        for cm_b in code_nodes:
            if cm_a["id"] == cm_b["id"]:
                continue
            b_stem = Path(cm_b["properties"].get("path", "")).stem
            if b_stem and len(b_stem) > 2 and b_stem in a_text:
                _add("DEPENDS_ON", cm_a["id"], cm_b["id"])

    # ── CROSS-TYPE CONNECTIONS (Quick Scan — no code nodes) ─────────
    # Connect requirements to issues by keyword overlap
    for req in req_nodes:
        req_text = _text_of(req)
        for issue in issue_nodes:
            if _keyword_overlap(req_text, _text_of(issue)) >= 2:
                _add("SATISFIES", issue["id"], req["id"])

    # Connect PRs (ChangeRecords from PRs) to issues/requirements
    pr_nodes = [n for n in nodes if n["label"] == "ChangeRecord" and n["id"].startswith("pr:")]
    for pr in pr_nodes:
        pr_text = _text_of(pr)
        # PR → most relevant requirement
        best_req, best_score = None, 0
        for req in req_nodes:
            score = _keyword_overlap(pr_text, _text_of(req))
            if score > best_score:
                best_score, best_req = score, req
        if best_req and best_score >= 1:
            _add("IMPLEMENTS", pr["id"], best_req["id"])

        # PR → most relevant issue
        best_issue, best_score = None, 0
        for issue in issue_nodes:
            score = _keyword_overlap(pr_text, _text_of(issue))
            if score > best_score:
                best_score, best_issue = score, issue
        if best_issue and best_score >= 1:
            _add("MODIFIES", pr["id"], best_issue["id"])

    logger.info("infer_edges: %d edges (%d TESTS, %d IMPLEMENTS, %d GOVERNS, %d EXPOSES, %d DEPENDS_ON)",
                len(edges),
                sum(1 for e in edges if e["label"] == "TESTS"),
                sum(1 for e in edges if e["label"] == "IMPLEMENTS"),
                sum(1 for e in edges if e["label"] == "GOVERNS"),
                sum(1 for e in edges if e["label"] == "EXPOSES"),
                sum(1 for e in edges if e["label"] == "DEPENDS_ON"))
    return edges

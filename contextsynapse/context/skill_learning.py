"""
Agent Skill Learning — Agents get smarter over time.

Tracks patterns from past agent runs and uses them to improve future runs.
- Which tools does this agent use most? In what order?
- What tool sequences lead to completed tasks?
- What patterns work for specific project types?

Usage:
    from contextsynapse.context.skill_learning import get_skill_learner
    learner = get_skill_learner()
    learner.record_run(agent_id, tools_used, tasks_completed, project_type)
    tips = learner.get_tips(agent_id, project_type)
"""

from __future__ import annotations

import json
import logging
import sqlite3
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class SkillLearner:
    """Learns agent behavior patterns from past runs."""

    def __init__(self, db_path: str = "contextcore_data/skill_learning.db"):
        self._db_path = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(db_path)
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS agent_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                agent_id TEXT NOT NULL,
                agent_name TEXT DEFAULT '',
                project_type TEXT DEFAULT '',
                tools_used TEXT DEFAULT '[]',
                tool_sequence TEXT DEFAULT '[]',
                tasks_completed INTEGER DEFAULT 0,
                tasks_failed INTEGER DEFAULT 0,
                total_turns INTEGER DEFAULT 0,
                success INTEGER DEFAULT 0,
                duration_ms INTEGER DEFAULT 0,
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_skill_agent ON agent_runs(agent_id);
            CREATE INDEX IF NOT EXISTS idx_skill_type ON agent_runs(project_type);

            CREATE TABLE IF NOT EXISTS learned_patterns (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                pattern_type TEXT NOT NULL,
                pattern_key TEXT NOT NULL,
                pattern_value TEXT NOT NULL,
                confidence REAL DEFAULT 0.5,
                usage_count INTEGER DEFAULT 1,
                updated_at TEXT NOT NULL,
                UNIQUE(pattern_type, pattern_key)
            );
        """)
        conn.commit()
        conn.close()

    def record_run(
        self,
        agent_id: str,
        agent_name: str = "",
        project_type: str = "",
        tools_used: Optional[List[str]] = None,
        tool_sequence: Optional[List[str]] = None,
        tasks_completed: int = 0,
        tasks_failed: int = 0,
        total_turns: int = 0,
        success: bool = True,
        duration_ms: int = 0,
    ):
        """Record a completed agent run for pattern learning."""
        now = datetime.now(timezone.utc).isoformat()
        conn = sqlite3.connect(self._db_path)
        conn.execute(
            "INSERT INTO agent_runs (agent_id, agent_name, project_type, tools_used, tool_sequence, "
            "tasks_completed, tasks_failed, total_turns, success, duration_ms, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (agent_id, agent_name, project_type,
             json.dumps(tools_used or []), json.dumps(tool_sequence or []),
             tasks_completed, tasks_failed, total_turns, int(success), duration_ms, now),
        )
        conn.commit()
        conn.close()

        # Update patterns
        self._learn_patterns(agent_id, project_type, tools_used or [], tool_sequence or [], success)

    def _learn_patterns(self, agent_id: str, project_type: str, tools: List[str], sequence: List[str], success: bool):
        """Extract patterns from a run and update confidence scores."""
        conn = sqlite3.connect(self._db_path)
        now = datetime.now(timezone.utc).isoformat()

        # Pattern 1: tool frequency for project type
        if project_type and tools:
            tool_counts = Counter(tools)
            for tool, count in tool_counts.most_common(10):
                key = f"{project_type}:{tool}"
                confidence_delta = 0.05 if success else -0.02
                conn.execute(
                    "INSERT INTO learned_patterns (pattern_type, pattern_key, pattern_value, confidence, usage_count, updated_at) "
                    "VALUES ('tool_preference', ?, ?, 0.5, 1, ?) "
                    "ON CONFLICT(pattern_type, pattern_key) DO UPDATE SET "
                    "confidence = MIN(1.0, MAX(0.0, confidence + ?)), "
                    "usage_count = usage_count + 1, updated_at = ?",
                    (key, json.dumps({"tool": tool, "count": count}), now, confidence_delta, now),
                )

        # Pattern 2: effective tool sequences (bigrams)
        if sequence and len(sequence) >= 2:
            for i in range(len(sequence) - 1):
                bigram = f"{sequence[i]}→{sequence[i+1]}"
                key = f"{project_type or 'any'}:{bigram}"
                confidence_delta = 0.03 if success else -0.01
                conn.execute(
                    "INSERT INTO learned_patterns (pattern_type, pattern_key, pattern_value, confidence, usage_count, updated_at) "
                    "VALUES ('tool_sequence', ?, ?, 0.5, 1, ?) "
                    "ON CONFLICT(pattern_type, pattern_key) DO UPDATE SET "
                    "confidence = MIN(1.0, MAX(0.0, confidence + ?)), "
                    "usage_count = usage_count + 1, updated_at = ?",
                    (key, json.dumps({"bigram": bigram}), now, confidence_delta, now),
                )

        # Pattern 3: agent specialization (what does this agent do best?)
        if success and tools:
            primary_tool = Counter(tools).most_common(1)[0][0]
            key = f"agent:{agent_id}:specialty"
            conn.execute(
                "INSERT INTO learned_patterns (pattern_type, pattern_key, pattern_value, confidence, usage_count, updated_at) "
                "VALUES ('agent_specialty', ?, ?, 0.6, 1, ?) "
                "ON CONFLICT(pattern_type, pattern_key) DO UPDATE SET "
                "pattern_value = ?, confidence = MIN(1.0, confidence + 0.02), "
                "usage_count = usage_count + 1, updated_at = ?",
                (key, json.dumps({"tool": primary_tool}), now, json.dumps({"tool": primary_tool}), now),
            )

        conn.commit()
        conn.close()

    def get_tips(self, agent_id: str = "", project_type: str = "") -> List[str]:
        """Get learned tips for an agent/project type. Injected into agent system prompt."""
        tips = []
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row

        # Top tools for this project type
        if project_type:
            rows = conn.execute(
                "SELECT pattern_key, pattern_value, confidence FROM learned_patterns "
                "WHERE pattern_type = 'tool_preference' AND pattern_key LIKE ? "
                "ORDER BY confidence DESC LIMIT 5",
                (f"{project_type}:%",),
            ).fetchall()
            if rows:
                tool_tips = [json.loads(r["pattern_value"])["tool"] for r in rows if r["confidence"] > 0.5]
                if tool_tips:
                    tips.append(f"For {project_type} projects, most effective tools: {', '.join(tool_tips)}")

        # Best tool sequences
        if project_type:
            rows = conn.execute(
                "SELECT pattern_value, confidence FROM learned_patterns "
                "WHERE pattern_type = 'tool_sequence' AND pattern_key LIKE ? AND confidence > 0.6 "
                "ORDER BY confidence DESC LIMIT 3",
                (f"{project_type}:%",),
            ).fetchall()
            if rows:
                sequences = [json.loads(r["pattern_value"])["bigram"] for r in rows]
                tips.append(f"Effective workflows: {', '.join(sequences)}")

        # Agent specialty
        if agent_id:
            row = conn.execute(
                "SELECT pattern_value, confidence FROM learned_patterns "
                "WHERE pattern_type = 'agent_specialty' AND pattern_key = ?",
                (f"agent:{agent_id}:specialty",),
            ).fetchone()
            if row and row["confidence"] > 0.5:
                specialty = json.loads(row["pattern_value"])["tool"]
                tips.append(f"Your strength: {specialty}")

        # Past run stats
        if agent_id:
            rows = conn.execute(
                "SELECT COUNT(*) as runs, AVG(tasks_completed) as avg_tasks, AVG(success) as success_rate "
                "FROM agent_runs WHERE agent_id = ?",
                (agent_id,),
            ).fetchone()
            if rows and rows["runs"] > 0:
                tips.append(f"Past runs: {rows['runs']}, avg tasks: {rows['avg_tasks']:.1f}, success: {rows['success_rate']*100:.0f}%")

        conn.close()
        return tips

    def get_agent_stats(self, agent_id: str) -> Dict[str, Any]:
        """Get detailed stats for an agent."""
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM agent_runs WHERE agent_id = ? ORDER BY created_at DESC LIMIT 20",
            (agent_id,),
        ).fetchall()
        conn.close()

        if not rows:
            return {"runs": 0}

        all_tools = []
        for r in rows:
            all_tools.extend(json.loads(r["tools_used"]))

        return {
            "runs": len(rows),
            "total_tasks_completed": sum(r["tasks_completed"] for r in rows),
            "success_rate": sum(r["success"] for r in rows) / len(rows),
            "avg_turns": sum(r["total_turns"] for r in rows) / len(rows),
            "top_tools": [t for t, _ in Counter(all_tools).most_common(5)],
            "project_types": list(set(r["project_type"] for r in rows if r["project_type"])),
        }


_learner = None

def get_skill_learner() -> SkillLearner:
    global _learner
    if _learner is None:
        _learner = SkillLearner()
    return _learner

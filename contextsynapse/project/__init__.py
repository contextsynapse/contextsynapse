"""
Project Knowledge Graph
========================
Structures a codebase as a knowledge graph with tasks, documents,
decisions, and code files — enabling multi-agent collaboration
where Claude and Codex work as a team.
"""

from .graph import ProjectGraph

__all__ = ["ProjectGraph"]

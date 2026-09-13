"""
Agent Task Dispatcher — Auto-decompose goals and assign to agents.

Given a goal and a set of registered agents, the dispatcher:
1. Decomposes the goal into tasks using LLM
2. Matches tasks to agents based on profiles/capabilities
3. Creates Task nodes assigned to specific agents
4. Agents discover tasks via orient() or my_tasks()

Usage:
    from contextsynapse.agents.dispatcher import dispatch_goal

    result = dispatch_goal(
        goal="Analyze our codebase for security vulnerabilities",
        session_id="abc123",
        graph_registry=reg,
        agent_registry=agent_reg,
    )
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# ── Agent Profiles ─────────────────────────────────────────────────────

DEFAULT_PROFILES = {
    "claude": {
        "strengths": ["code review", "architecture", "debugging", "detailed analysis", "safety"],
        "preferred_tasks": ["review code", "analyze architecture", "find bugs", "security audit"],
        "speed": "thorough",
    },
    "chatgpt": {
        "strengths": ["research", "writing", "brainstorming", "breadth", "summarization"],
        "preferred_tasks": ["research topic", "write summary", "explore options", "draft document"],
        "speed": "fast",
    },
    "gemini": {
        "strengths": ["multimodal", "data analysis", "reasoning", "math"],
        "preferred_tasks": ["analyze data", "process images", "calculate", "compare"],
        "speed": "fast",
    },
}


def get_agent_profile(agent, registry=None) -> Dict[str, Any]:
    """Get the specialization profile for an agent.

    Checks agent.metadata for a custom profile, falls back to defaults
    based on agent name. Optionally enriches from registry capabilities.
    """
    metadata = agent.metadata if hasattr(agent, 'metadata') else {}
    if isinstance(metadata, str):
        try:
            metadata = json.loads(metadata)
        except Exception:
            metadata = {}

    # Custom profile in metadata
    if metadata.get("profile"):
        profile = metadata["profile"]
    else:
        # Match by name
        name_lower = (agent.name if hasattr(agent, 'name') else "").lower()
        profile = None
        for key, default_profile in DEFAULT_PROFILES.items():
            if key in name_lower:
                profile = dict(default_profile)
                break

        if not profile:
            profile = {
                "strengths": ["general"],
                "preferred_tasks": ["any"],
                "speed": "normal",
            }

    # Enrich from registry capabilities if available
    if registry:
        try:
            agent_name = (agent.name if hasattr(agent, 'name') else "").lower()
            agents = registry.list_agents()
            for reg_agent in agents:
                reg_name = reg_agent.name if hasattr(reg_agent, 'name') else (reg_agent.get("name", "") if isinstance(reg_agent, dict) else "")
                if reg_name.lower() == agent_name:
                    caps = reg_agent.capabilities if hasattr(reg_agent, 'capabilities') else (reg_agent.get("capabilities", []) if isinstance(reg_agent, dict) else [])
                    if isinstance(caps, str):
                        caps = json.loads(caps)
                    profile["strengths"] = list(set(profile.get("strengths", []) + caps))
                    break
        except Exception:
            pass

    return profile


# ── Goal Decomposition ────────────────────────────────────────────────

def decompose_goal(goal: str, agent_profiles: List[Dict], llm_fn=None) -> List[Dict]:
    """Decompose a goal into tasks matched to agent capabilities.

    Args:
        goal: Natural language goal
        agent_profiles: List of {"name": str, "strengths": [...]}
        llm_fn: Optional LLM callable(prompt) -> str

    Returns:
        List of {"title": str, "description": str, "assigned_to": str, "priority": str}
    """
    agent_descriptions = "\n".join(
        f"- {p['name']}: good at {', '.join(p.get('strengths', ['general']))}"
        for p in agent_profiles
    )

    prompt = f"""Decompose this goal into 3-5 specific tasks and assign each to the best agent.

GOAL: {goal}

AVAILABLE AGENTS:
{agent_descriptions}

Rules:
- Each task should be assigned to the agent whose strengths best match
- Distribute work evenly — don't give everything to one agent
- Tasks should be specific and actionable
- Include a mix of research, analysis, and writing tasks

Return ONLY a valid JSON array:
[
  {{"title": "task title", "description": "what to do", "assigned_to": "agent name", "priority": "high|medium|low"}},
  ...
]"""

    if llm_fn:
        try:
            import re
            response = llm_fn(prompt)
            match = re.search(r'\[[\s\S]*\]', response)
            if match:
                return json.loads(match.group())
        except Exception as e:
            logger.warning("LLM decomposition failed: %s", e)

    # Fallback: create one task per agent
    tasks = []
    for i, profile in enumerate(agent_profiles):
        tasks.append({
            "title": f"Research and analyze: {goal[:50]}",
            "description": f"Focus on: {', '.join(profile.get('strengths', ['general'])[:2])}",
            "assigned_to": profile["name"],
            "priority": "high" if i == 0 else "medium",
        })
    return tasks


# ── Dispatch Goal ─────────────────────────────────────────────────────

def dispatch_goal(
    goal: str,
    session_id: str,
    graph_registry=None,
    agent_registry=None,
    session_manager=None,
    llm_fn=None,
) -> Dict[str, Any]:
    """Decompose a goal and create tasks assigned to registered agents.

    Args:
        goal: What needs to be accomplished
        session_id: Session to create tasks in
        graph_registry: GraphRegistry for graph access
        agent_registry: AgentRegistry to find agents
        session_manager: SessionManager for session lookup
        llm_fn: Optional LLM callable

    Returns:
        {"tasks": [...], "agents": [...], "goal": str}
    """
    # Get session — tasks go to the runtime graph, not the atomic context
    session = None
    graph_ns = None
    if session_manager:
        session = session_manager.get_session(session_id)
        if session:
            # Use runtime_namespace for agent work (tasks, findings, actions)
            graph_ns = getattr(session, 'runtime_namespace', '') or f"{session.graph_namespace}_rt"

    if not graph_ns:
        return {"error": "Session not found", "tasks": []}

    # Get registered agents
    agents = []
    if agent_registry:
        # Get agents assigned to this session
        if session and session.config.get("assigned_agents"):
            for aid in session.config["assigned_agents"]:
                agent = agent_registry.get(aid)
                if agent and agent.status == "active":
                    agents.append(agent)

        # If no assigned agents, get all active agents
        if not agents:
            try:
                all_agents = agent_registry.list_agents() if hasattr(agent_registry, 'list_agents') else []
                agents = [a for a in all_agents if a.status == "active"][:5]
            except Exception:
                pass

    if not agents:
        return {"error": "No agents available", "tasks": []}

    # Build profiles
    agent_profiles = []
    for agent in agents:
        profile = get_agent_profile(agent)
        profile["name"] = agent.name
        profile["agent_id"] = agent.agent_id
        agent_profiles.append(profile)

    logger.info("[DISPATCH] Goal: %s | Agents: %s", goal[:60],
                [p["name"] for p in agent_profiles])

    # Decompose goal into tasks
    tasks = decompose_goal(goal, agent_profiles, llm_fn=llm_fn)

    # Create task nodes in the graph
    created = []
    if graph_registry:
        db = graph_registry.get_graph(graph_ns, load_if_missing=True)
        if db:
            from ..core.hybrid_graph_storage import GraphNode, GraphEdge

            for task in tasks:
                task_id = f"task_{uuid.uuid4().hex[:12]}"
                props = {
                    "title": task["title"],
                    "description": task.get("description", ""),
                    "assigned_to": task.get("assigned_to", ""),
                    "priority": task.get("priority", "medium"),
                    "status": "open",
                    "created_by": "dispatcher",
                    "created_at": datetime.now(timezone.utc).isoformat(),
                    "goal": goal[:200],
                    "session_id": session_id,
                }
                db.add_node(GraphNode(id=task_id, label="Task", properties=props))

                created.append({
                    "task_id": task_id,
                    "title": task["title"],
                    "assigned_to": task.get("assigned_to", ""),
                    "priority": task.get("priority", "medium"),
                })

                logger.info("[DISPATCH] Created task: %s → %s", task["title"][:40], task.get("assigned_to", "?"))

    # Publish all created tasks to stream for push-based delivery
    try:
        from ..project.task_stream import get_task_publisher
        pub = get_task_publisher()
        stream_tasks = [
            {"task_id": t.get("task_id", t.get("id", "")),
             "priority": {"critical": 3, "high": 2, "medium": 1, "low": 0}.get(t.get("priority", "medium"), 1),
             "metadata": {"title": t.get("title", ""), "assigned_to": t.get("assigned_to", ""), "goal": goal}}
            for t in created
        ]
        if stream_tasks:
            pub.publish_batch(namespace=graph_ns, tasks=stream_tasks)
    except Exception:
        pass  # stream publishing must never break goal dispatch

    # Notify agents (via propagation)
    try:
        from ..context.propagation import get_propagator
        for task in created:
            get_propagator().propagate(
                source_agent="dispatcher",
                event_type="task_assigned",
                content=f"New task: {task['title']} (priority: {task['priority']})",
                namespace=graph_ns,
                priority="high",
                target_agent=task["assigned_to"],
            )
    except Exception:
        pass

    return {
        "goal": goal,
        "tasks": created,
        "agents": [{"name": p["name"], "strengths": p.get("strengths", [])} for p in agent_profiles],
        "session_id": session_id,
    }

"""
Team Coordination Tools
========================
Shared tool implementations used by BOTH the MCP server (Claude)
and the OpenAI adapter (Codex). Both agents get the same capabilities.

These are higher-level than raw graph tools — they encode team workflow:
task claiming, completion, handoff, status, and context building.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

from .graph import ProjectGraph

logger = logging.getLogger(__name__)

# ── Module state ────────────────────────────────────────────────────
_project: Optional[ProjectGraph] = None
_agent_id: str = ""
_agent_name: str = ""
_thread_id: Optional[str] = None


def _thread_log(content: str, action: str = ""):
    """Log agent action to session thread (if configured)."""
    if not _thread_id:
        return
    try:
        from ..context.conversation import ConversationStore
        store = ConversationStore()
        store.append_message(
            conversation_id=_thread_id,
            role="assistant",
            content=content,
            agent_id=_agent_id,
            metadata={"type": "agent_action", "action": action, "agent_name": _agent_name},
        )
    except Exception as e:
        logger.debug("Thread log failed: %s", e)


def init_team(
    project_name: str,
    agent_id: str,
    agent_name: str,
    project: Optional[ProjectGraph] = None,
    thread_id: Optional[str] = None,
) -> ProjectGraph:
    """Initialize team tools with a project and agent identity."""
    global _project, _agent_id, _agent_name, _thread_id
    _agent_id = agent_id
    _agent_name = agent_name
    _thread_id = thread_id
    _project = project or ProjectGraph(project_name)
    return _project


def get_project() -> Optional[ProjectGraph]:
    return _project


# =====================================================================
# Tool implementations (called by both MCP and OpenAI dispatchers)
# =====================================================================

def tool_project_status() -> str:
    """Get the current project status — tasks, agents, decisions."""
    if not _project:
        return "Error: No project loaded. Use init_project first."

    status = _project.get_project_status()
    lines = [
        f"Project: {status['project']}",
        f"Tasks: {status['tasks']['total']} total",
    ]
    for s, c in status["tasks"]["by_status"].items():
        lines.append(f"  {s}: {c}")

    lines.append(f"Decisions: {status['decisions']}")
    lines.append(f"Documents: {status['documents']}")
    lines.append(f"Code files: {status['code_files']}")

    if status["agents_working"]:
        lines.append("\nAgents working:")
        for aid, titles in status["agents_working"].items():
            try:
                agent = _project.registry.get(aid)
                name = agent.name if agent else aid[:8]
            except Exception:
                name = aid[:8]
            lines.append(f"  {name}: {', '.join(titles)}")

    return "\n".join(lines)


def tool_list_tasks(status_filter: str = "", agent_filter: str = "") -> str:
    """List tasks, optionally filtered by status or assigned agent."""
    if not _project:
        return "Error: No project loaded."

    if status_filter in ("open", "in_progress", "blocked"):
        tasks = _project.get_open_tasks()
        tasks = [t for t in tasks if t["status"] == status_filter]
    elif status_filter == "completed":
        all_tasks = _project.conn.get_nodes(label="Task")
        tasks = []
        for t in all_tasks:
            p = t.properties if hasattr(t, "properties") else t
            if p.get("status") == "completed":
                tasks.append({
                    "id": t.id if hasattr(t, "id") else "?",
                    "title": p.get("title", "?"),
                    "status": "completed",
                    "priority": p.get("priority", "medium"),
                    "assigned_to": p.get("completed_by", p.get("assigned_to", "?")),
                    "tags": p.get("tags", ""),
                })
    else:
        tasks = _project.get_open_tasks()

    if agent_filter:
        tasks = [t for t in tasks if t["assigned_to"] == agent_filter]

    if not tasks:
        return "No tasks found matching filters."

    lines = []
    for t in tasks:
        assigned = t["assigned_to"]
        if assigned and assigned != "unassigned":
            try:
                agent = _project.registry.get(assigned)
                assigned = agent.name if agent else assigned[:8]
            except Exception:
                assigned = assigned[:8]
        lines.append(
            f"[{t['priority'].upper():8s}] {t['title']}\n"
            f"           status={t['status']}  assigned={assigned}  id={t['id']}"
        )

    return "\n".join(lines)


def tool_my_tasks() -> str:
    """Get tasks assigned to the current agent."""
    if not _project or not _agent_id:
        return "Error: No project or agent configured."

    tasks = _project.get_open_tasks(agent_id=_agent_id, agent_name=_agent_name)
    if not tasks:
        return f"No tasks assigned to {_agent_name}. Use claim_task to pick up work."

    lines = [f"Tasks assigned to {_agent_name}:"]
    for t in tasks:
        lines.append(f"  [{t['priority'].upper()}] {t['title']} (status: {t['status']}, id: {t['id']})")

    return "\n".join(lines)


def tool_claim_task(task_id: str) -> str:
    """Claim an open task for the current agent."""
    if not _project or not _agent_id:
        return "Error: No project or agent configured."

    result = _project.claim_task(task_id, _agent_id)
    _thread_log(f"{_agent_name}: {result}", action="claimed_task")
    return result


def tool_complete_task(task_id: str, summary: str = "", files_changed: str = "") -> str:
    """Mark a task as completed."""
    if not _project or not _agent_id:
        return "Error: No project or agent configured."

    files = [f.strip() for f in files_changed.split(",") if f.strip()] if files_changed else None
    result = _project.complete_task(task_id, _agent_id, summary=summary, files_changed=files)
    log_msg = f"{_agent_name}: {result}"
    if summary:
        log_msg += f" — {summary[:100]}"
    _thread_log(log_msg, action="completed_task")
    return result


def tool_handoff_task(task_id: str, to_agent: str, notes: str = "") -> str:
    """Hand off a task to another agent."""
    if not _project or not _agent_id:
        return "Error: No project or agent configured."

    result = _project.handoff_task(task_id, from_agent=_agent_id, to_agent=to_agent, notes=notes)
    _thread_log(f"{_agent_name}: Handed off task to {to_agent[:8]}. {notes[:100]}", action="handoff")
    return result


def tool_add_task(
    title: str,
    description: str = "",
    priority: str = "medium",
    tags: str = "",
    depends_on: str = "",
    assigned_to: str = "",
) -> str:
    """Create a new task in the project."""
    if not _project:
        return "Error: No project loaded."

    tag_list = [t.strip() for t in tags.split(",") if t.strip()] if tags else None
    dep_list = [d.strip() for d in depends_on.split(",") if d.strip()] if depends_on else None

    task_id = _project.add_task(
        title=title,
        description=description,
        priority=priority,
        tags=tag_list,
        depends_on=dep_list,
        assigned_to=assigned_to or None,
        created_by=_agent_name or None,
    )
    assign_msg = f", assigned to: {assigned_to}" if assigned_to else ""
    result = f"Created task: {title} (id: {task_id[:8]}, priority: {priority}{assign_msg})"
    _thread_log(f"{_agent_name}: {result}", action="created_task")
    return result


def tool_add_decision(title: str, rationale: str, impacts: str = "") -> str:
    """Record a decision made during the project."""
    if not _project or not _agent_id:
        return "Error: No project or agent configured."

    impact_list = [i.strip() for i in impacts.split(",") if i.strip()] if impacts else None
    decision_id = _project.add_decision(
        title=title,
        rationale=rationale,
        agent_id=_agent_id,
        impacts=impact_list,
    )
    result = f"Decision recorded: {title} (id: {decision_id[:8]}, by: {_agent_name})"
    _thread_log(f"{_agent_name}: Decision — {title}: {rationale[:100]}", action="decision")
    return result


def tool_get_context() -> str:
    """Build LLM-ready context for the current agent — tasks, decisions, status."""
    if not _project or not _agent_id:
        return "Error: No project or agent configured."

    return _project.build_agent_context(_agent_id)


# =====================================================================
# Tool schemas (OpenAI function-calling format)
# =====================================================================

TEAM_TOOL_SCHEMAS: List[Dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "project_status",
            "description": "Get overall project status — task counts, active agents, decisions made.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_tasks",
            "description": "List project tasks. Filter by status or assigned agent.",
            "parameters": {
                "type": "object",
                "properties": {
                    "status_filter": {
                        "type": "string",
                        "description": "Filter by status: 'open', 'in_progress', 'blocked', 'completed', or '' for all open",
                        "default": "",
                    },
                    "agent_filter": {
                        "type": "string",
                        "description": "Filter by assigned agent_id",
                        "default": "",
                    },
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "my_tasks",
            "description": "Get tasks assigned to you (the current agent).",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "claim_task",
            "description": "Claim an open task to work on. The task must be 'open' status.",
            "parameters": {
                "type": "object",
                "properties": {
                    "task_id": {"type": "string", "description": "UUID of the task to claim"},
                },
                "required": ["task_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "complete_task",
            "description": "Mark a task as completed. Include a summary of what you did.",
            "parameters": {
                "type": "object",
                "properties": {
                    "task_id": {"type": "string", "description": "UUID of the task to complete"},
                    "summary": {"type": "string", "description": "What you did to complete this task"},
                    "files_changed": {"type": "string", "description": "Comma-separated list of files changed", "default": ""},
                },
                "required": ["task_id", "summary"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "handoff_task",
            "description": "Hand off a task to another agent. Use when you need the other agent to continue your work.",
            "parameters": {
                "type": "object",
                "properties": {
                    "task_id": {"type": "string", "description": "UUID of the task to hand off"},
                    "to_agent": {"type": "string", "description": "agent_id of the agent to hand off to"},
                    "notes": {"type": "string", "description": "Context/instructions for the receiving agent", "default": ""},
                },
                "required": ["task_id", "to_agent"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "add_task",
            "description": "Create a new task in the project. Optionally assign it to an agent by name.",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "Task title"},
                    "description": {"type": "string", "description": "Detailed description", "default": ""},
                    "priority": {"type": "string", "enum": ["critical", "high", "medium", "low"], "default": "medium"},
                    "tags": {"type": "string", "description": "Comma-separated tags", "default": ""},
                    "depends_on": {"type": "string", "description": "Comma-separated task UUIDs this depends on", "default": ""},
                    "assigned_to": {"type": "string", "description": "Agent name to assign this task to (e.g. 'claude', 'codex')", "default": ""},
                },
                "required": ["title"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "add_decision",
            "description": "Record a technical/design decision you made. Other agents will see this.",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "Decision title"},
                    "rationale": {"type": "string", "description": "Why this decision was made"},
                    "impacts": {"type": "string", "description": "Comma-separated UUIDs of tasks/code affected", "default": ""},
                },
                "required": ["title", "rationale"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_agent_context",
            "description": "Build your personalized context — your tasks, project status, recent decisions, other agents' actions.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "extract_entities",
            "description": "Extract entities and relationships from text using LLM. Auto-creates graph nodes and edges.",
            "parameters": {
                "type": "object",
                "properties": {
                    "text": {"type": "string", "description": "Text to extract entities from"},
                    "provider": {"type": "string", "description": "LLM provider: 'openai', 'anthropic', 'groq', 'ollama' (auto-detect if empty)", "default": ""},
                    "model": {"type": "string", "description": "Model name (e.g. 'gpt-oss-120b', 'gpt-4o-mini')", "default": ""},
                },
                "required": ["text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "extract_facts",
            "description": "Extract verifiable facts from text — claims, metrics, dates, decisions. Each fact becomes a graph node.",
            "parameters": {
                "type": "object",
                "properties": {
                    "text": {"type": "string", "description": "Text to extract facts from"},
                    "provider": {"type": "string", "description": "LLM provider (auto-detect if empty)", "default": ""},
                    "model": {"type": "string", "description": "Model name", "default": ""},
                },
                "required": ["text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "extract_with_schema",
            "description": "Extract entities using a YAML schema that defines allowed types. More precise than generic extraction.",
            "parameters": {
                "type": "object",
                "properties": {
                    "text": {"type": "string", "description": "Text to extract from"},
                    "schema_path": {"type": "string", "description": "Path to YAML schema file"},
                    "provider": {"type": "string", "description": "LLM provider", "default": ""},
                    "model": {"type": "string", "description": "Model name", "default": ""},
                },
                "required": ["text", "schema_path"],
            },
        },
    },
]


# ── Dispatch map ────────────────────────────────────────────────────

def _tool_extract_entities(text: str, provider: str = "", model: str = "") -> str:
    """Extract entities from text and create graph nodes/edges."""
    if not _project:
        return "Error: No project loaded."
    try:
        from ..extraction.llm_entity_extractor import EntityExtractor
        extractor = EntityExtractor(
            db=_project.conn.contextcore,
            provider=provider or None,
            model=model or None,
        )
        result = extractor.extract(text, source="team_tool", agent_id=_agent_id or _agent_name)
        parts = [f"Extracted {len(result.node_ids)} entities, {len(result.edge_ids)} relationships"]
        for e in result.entities:
            parts.append(f"  [{e.get('type', '?')}] {e.get('name', '?')}")
        for r in result.relationships:
            parts.append(f"  {r.get('source', '?')} -[{r.get('type', '?')}]-> {r.get('target', '?')}")
        return "\n".join(parts)
    except Exception as e:
        return f"Error: {e}"


TEAM_DISPATCH = {
    "project_status": lambda **kw: tool_project_status(),
    "list_tasks": tool_list_tasks,
    "my_tasks": lambda **kw: tool_my_tasks(),
    "claim_task": tool_claim_task,
    "complete_task": tool_complete_task,
    "handoff_task": tool_handoff_task,
    "add_task": tool_add_task,
    "add_decision": tool_add_decision,
    "get_agent_context": lambda **kw: tool_get_context(),
    "extract_entities": _tool_extract_entities,
    "extract_facts": lambda text="", provider="", model="", **kw: _tool_extract_facts(text, provider, model),
    "extract_with_schema": lambda text="", schema_path="", provider="", model="", **kw: _tool_extract_with_schema(text, schema_path, provider, model),
}


def _tool_extract_facts(text: str, provider: str = "", model: str = "") -> str:
    """Extract verifiable facts from text."""
    if not _project:
        return "Error: No project loaded."
    try:
        from ..extraction.fact_extractor import FactExtractor
        extractor = FactExtractor(
            db=_project.conn.contextcore,
            provider=provider or None,
            model=model or None,
        )
        result = extractor.extract_facts(text, agent_id=_agent_id or _agent_name)
        parts = [f"Extracted {len(result.facts)} facts"]
        for f in result.facts:
            parts.append(f"  [{f.fact_type}] {f.statement} (confidence: {f.confidence:.2f})")
        return "\n".join(parts)
    except Exception as e:
        return f"Error: {e}"


def _tool_extract_with_schema(text: str, schema_path: str, provider: str = "", model: str = "") -> str:
    """Extract entities using a YAML schema."""
    if not _project:
        return "Error: No project loaded."
    try:
        from ..extraction.llm_entity_extractor import EntityExtractor
        extractor = EntityExtractor(
            db=_project.conn.contextcore,
            provider=provider or None,
            model=model or None,
            schema=schema_path,
        )
        result = extractor.extract(text, source="schema_tool", agent_id=_agent_id or _agent_name)
        parts = [f"Schema extraction: {len(result.node_ids)} nodes, {len(result.edge_ids)} edges"]
        for e in result.entities:
            parts.append(f"  [{e.get('type', '?')}] {e.get('name', '?')}")
        facts = result.raw_response.get("facts", [])
        if facts:
            for f in facts:
                parts.append(f"  [{f.get('type', '?')}] {f.get('statement', '?')}")
        return "\n".join(parts)
    except Exception as e:
        return f"Error: {e}"


def dispatch_team_tool(name: str, arguments: str) -> str:
    """Execute a team coordination tool call."""
    handler = TEAM_DISPATCH.get(name)
    if handler is None:
        return f"Error: Unknown team tool '{name}'"

    try:
        args = json.loads(arguments) if arguments else {}
    except json.JSONDecodeError:
        return f"Error: Invalid JSON: {arguments}"

    try:
        return handler(**args)
    except Exception as e:
        logger.error("Team tool %s failed: %s", name, e)
        return f"Error: {e}"

"""
Task Tools — task management for team coordination.

11 tools: project_status, list_tasks, my_tasks, claim_task, complete_task,
          handoff_task, add_task, add_decision, get_agent_context, init_project,
          get_unblocked_tasks
"""

from __future__ import annotations

import logging

from .registry import ToolContext, ToolParam, tool

logger = logging.getLogger(__name__)


def _require_project(ctx: ToolContext) -> str:
    """Return error string if no project loaded, else empty string."""
    if not ctx.project:
        return "Error: No project loaded. Use init_project first."
    return ""


@tool(
    "init_project", "task",
    "Initialize a project knowledge graph for team collaboration. "
    "Call this before using team tools (claim_task, etc.).",
    params=[ToolParam("project_name", "string", "Project name")],
)
def _init_project(ctx: ToolContext, project_name: str) -> str:
    try:
        from contextsynapse.project.graph import ProjectGraph
        ctx.project = ProjectGraph(project_name, connection=ctx.conn)
        return f"Project '{project_name}' initialized."
    except Exception as e:
        return f"Error initializing project: {e}"


@tool("project_status", "task",
      "Get overall project status — task counts, active agents, decisions made.",
      requires_project=True)
def _project_status(ctx: ToolContext) -> str:
    err = _require_project(ctx)
    if err:
        return err
    import json
    status = ctx.project.get_project_status()
    lines = [
        f"Project: {status['project']}",
        f"Tasks: {status['tasks']['total']} total",
    ]
    for s, c in status["tasks"]["by_status"].items():
        lines.append(f"  {s}: {c}")
    lines.append(f"Decisions: {status['decisions']}")
    lines.append(f"Documents: {status['documents']}")
    lines.append(f"Code files: {status['code_files']}")
    return "\n".join(lines)


@tool("list_tasks", "task",
      "List project tasks. Filter by status or assigned agent.",
      params=[
          ToolParam("status_filter", "string", "'open', 'in_progress', 'completed', or '' for all", required=False, default=""),
          ToolParam("agent_filter", "string", "Filter by agent name or id", required=False, default=""),
      ],
      requires_project=True)
def _list_tasks(ctx: ToolContext, status_filter: str = "", agent_filter: str = "") -> str:
    err = _require_project(ctx)
    if err:
        return err

    if status_filter in ("open", "in_progress", "blocked"):
        tasks = ctx.project.get_open_tasks()
        tasks = [t for t in tasks if t["status"] == status_filter]
    elif status_filter == "completed":
        all_tasks = ctx.project._query_nodes("Task")
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
        tasks = ctx.project.get_open_tasks()

    if agent_filter:
        tasks = [t for t in tasks if t["assigned_to"] == agent_filter]

    if not tasks:
        return "No tasks found matching filters."

    lines = []
    for t in tasks:
        lines.append(
            f"[{t['priority'].upper():8s}] {t['title']}\n"
            f"           status={t['status']}  assigned={t['assigned_to']}  id={t['id']}"
        )
    return "\n".join(lines)


@tool("my_tasks", "task",
      "Get tasks assigned to you (the current agent).",
      requires_project=True)
def _my_tasks(ctx: ToolContext) -> str:
    err = _require_project(ctx)
    if err:
        return err
    tasks = ctx.project.get_open_tasks(agent_id=ctx.agent_id, agent_name=ctx.agent_name)
    if not tasks:
        return f"No tasks assigned to {ctx.agent_name}. Use claim_task to pick up work."
    lines = [f"Tasks assigned to {ctx.agent_name}:"]
    for t in tasks:
        lines.append(f"  [{t['priority'].upper()}] {t['title']} (status: {t['status']}, id: {t['id']})")
    return "\n".join(lines)


@tool("claim_task", "task",
      "Claim an open task to work on.",
      params=[ToolParam("task_id", "string", "UUID of the task to claim")],
      requires_project=True)
def _claim_task(ctx: ToolContext, task_id: str) -> str:
    err = _require_project(ctx)
    if err:
        return err
    result = ctx.project.claim_task(task_id, ctx.agent_id)
    ctx.thread_log(f"{ctx.agent_name}: {result}", action="claimed_task")
    return result


@tool("complete_task", "task",
      "Mark a task as completed. Include a summary of what you did.",
      params=[
          ToolParam("task_id", "string", "UUID of the task"),
          ToolParam("summary", "string", "What you did to complete this task"),
          ToolParam("files_changed", "string", "Comma-separated files changed", required=False, default=""),
      ],
      requires_project=True)
def _complete_task(ctx: ToolContext, task_id: str, summary: str, files_changed: str = "") -> str:
    err = _require_project(ctx)
    if err:
        return err
    files = [f.strip() for f in files_changed.split(",") if f.strip()] if files_changed else None
    result = ctx.project.complete_task(task_id, ctx.agent_id, summary=summary, files_changed=files)
    ctx.thread_log(f"{ctx.agent_name}: {result}", action="completed_task")

    # Cross-agent propagation: notify others that a task was completed
    try:
        from contextsynapse.context.propagation import get_propagator
        get_propagator().propagate(
            source_agent=ctx.agent_name or ctx.agent_id,
            event_type="task_completed",
            content=f"Completed: {summary[:100]}",
            namespace=ctx.conn._namespace if ctx.conn else "",
            priority="normal",
        )
    except Exception:
        pass

    return result


@tool("handoff_task", "task",
      "Hand off a task to another agent.",
      params=[
          ToolParam("task_id", "string", "UUID of the task"),
          ToolParam("to_agent", "string", "Agent name or ID to hand off to"),
          ToolParam("notes", "string", "Context for the receiving agent", required=False, default=""),
      ],
      requires_project=True)
def _handoff_task(ctx: ToolContext, task_id: str, to_agent: str, notes: str = "") -> str:
    err = _require_project(ctx)
    if err:
        return err
    result = ctx.project.handoff_task(task_id, from_agent=ctx.agent_id, to_agent=to_agent, notes=notes)
    ctx.thread_log(f"{ctx.agent_name}: {result}", action="handoff_task")
    return result


@tool("add_task", "task",
      "Create a new task in the project. Optionally assign to an agent.",
      params=[
          ToolParam("title", "string", "Task title"),
          ToolParam("description", "string", "Detailed description", required=False, default=""),
          ToolParam("priority", "string", "critical, high, medium, low", required=False, default="medium",
                    enum=["critical", "high", "medium", "low"]),
          ToolParam("tags", "string", "Comma-separated tags", required=False, default=""),
          ToolParam("depends_on", "string", "Comma-separated task UUIDs this depends on", required=False, default=""),
          ToolParam("assigned_to", "string", "Agent name to assign to", required=False, default=""),
          ToolParam("parent_task", "string", "Parent task UUID (for subtasks)", required=False, default=""),
      ],
      requires_project=True)
def _add_task(ctx: ToolContext, title: str, description: str = "", priority: str = "medium",
              tags: str = "", depends_on: str = "", assigned_to: str = "", parent_task: str = "") -> str:
    err = _require_project(ctx)
    if err:
        return err
    tag_list = [t.strip() for t in tags.split(",") if t.strip()] if tags else None
    dep_list = [d.strip() for d in depends_on.split(",") if d.strip()] if depends_on else None
    task_id = ctx.project.add_task(
        title=title, description=description, priority=priority,
        tags=tag_list, depends_on=dep_list,
        assigned_to=assigned_to or None,
        parent_task=parent_task or None,
        created_by=ctx.agent_name or None,
    )
    assign_msg = f", assigned to: {assigned_to}" if assigned_to else ""
    result = f"Created task: {title} (id: {task_id}, priority: {priority}{assign_msg})"
    ctx.thread_log(f"{ctx.agent_name}: {result}", action="created_task")
    return result


@tool("add_decision", "task",
      "Record a technical decision so other agents know about it.",
      params=[
          ToolParam("title", "string", "Decision title"),
          ToolParam("rationale", "string", "Why this decision was made"),
          ToolParam("impacts", "string", "Comma-separated UUIDs of affected tasks/code", required=False, default=""),
      ],
      requires_project=True)
def _add_decision(ctx: ToolContext, title: str, rationale: str, impacts: str = "") -> str:
    err = _require_project(ctx)
    if err:
        return err
    impact_list = [i.strip() for i in impacts.split(",") if i.strip()] if impacts else None
    decision_id = ctx.project.add_decision(
        title=title, rationale=rationale,
        agent_id=ctx.agent_id, impacts=impact_list,
    )
    result = f"Decision recorded: {title} (id: {decision_id}, by: {ctx.agent_name})"
    ctx.thread_log(f"{ctx.agent_name}: Decision — {title}: {rationale[:100]}", action="decision")

    # Cross-agent propagation: notify other agents about the decision
    try:
        from contextsynapse.context.propagation import get_propagator
        get_propagator().propagate(
            source_agent=ctx.agent_name or ctx.agent_id,
            event_type="decision",
            content=f"{title}: {rationale[:100]}",
            namespace=ctx.conn._namespace if ctx.conn else "",
            priority="critical",
        )
    except Exception:
        pass

    return result


@tool("get_agent_context", "task",
      "Build your personalized context — your tasks, project status, "
      "recent decisions, and what other agents have been doing.",
      requires_project=True)
def _get_agent_context(ctx: ToolContext) -> str:
    err = _require_project(ctx)
    if err:
        return err
    return ctx.project.build_agent_context(
        agent_id=ctx.agent_id, agent_name=ctx.agent_name,
    )


@tool("get_unblocked_tasks", "task",
      "Get tasks that are ready to work on — all their dependencies are completed. "
      "Use this to find what to work on next in dependency order.",
      params=[
          ToolParam("assigned_to", "string", "Filter by agent name (optional)", required=False),
      ],
      requires_project=True)
def _get_unblocked_tasks(ctx: ToolContext, assigned_to: str = "") -> str:
    err = _require_project(ctx)
    if err:
        return err

    # Get all tasks (open + completed) from the graph
    all_task_nodes = ctx.project._query_nodes("Task")
    all_tasks = []
    for t in all_task_nodes:
        p = t.properties if hasattr(t, "properties") else t
        all_tasks.append({
            "id": t.id if hasattr(t, "id") else p.get("id", "?"),
            "title": p.get("title", "?"),
            "status": p.get("status", "open"),
            "priority": p.get("priority", "medium"),
            "assigned_to": p.get("assigned_to", "unassigned"),
        })

    # Filter by assigned_to if specified
    if assigned_to:
        tasks = [t for t in all_tasks if t["assigned_to"] == assigned_to]
    else:
        tasks = all_tasks

    if not tasks:
        return "No tasks found."

    # Build dependency map: task_id -> list of dependency task_ids
    dep_edges = ctx.project.conn.get_edges(label="DEPENDS_ON")
    deps = {}
    for e in dep_edges:
        src = e.source if hasattr(e, "source") else e.get("source", "")
        tgt = e.target if hasattr(e, "target") else e.get("target", "")
        if src:
            deps.setdefault(src, []).append(tgt)

    # Collect completed task IDs
    completed_ids = set()
    for t in all_tasks:
        if t.get("status") in ("completed", "done"):
            completed_ids.add(t.get("id", ""))

    # Filter to unblocked: open tasks whose deps are all completed (or have no deps)
    unblocked = []
    for t in tasks:
        tid = t.get("id", "")
        status = t.get("status", "open")
        if status not in ("open", "blocked"):
            continue
        task_deps = deps.get(tid, [])
        if all(d in completed_ids for d in task_deps):
            unblocked.append(t)

    if not unblocked:
        return "No unblocked tasks — all remaining tasks have incomplete dependencies."

    lines = []
    for t in unblocked:
        dep_info = ""
        task_deps = deps.get(t.get("id", ""), [])
        if task_deps:
            dep_info = f" (deps: {len(task_deps)} completed)"
        lines.append(
            f"[{t.get('priority', 'medium').upper()}] {t.get('title', '?')} "
            f"(id: {t.get('id', '?')}, assigned: {t.get('assigned_to', '?')}){dep_info}"
        )
    return f"{len(unblocked)} unblocked tasks:\n" + "\n".join(lines)

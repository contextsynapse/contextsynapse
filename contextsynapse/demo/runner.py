"""Demo agent runners — blind vs context-aware."""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from typing import Any, Dict, List

import anthropic

from contextsynapse.project.project_context import ProjectContext
from contextsynapse.project.sdlc_schema import SDLC_EDGE_TYPES, ALL_NODE_TYPES, is_sdlc_node_type, validate_node
from contextsynapse.core.graph_structures import GraphEdge, GraphNode

# ── System prompts ────────────────────────────────────────────────────────────

_BLIND_SYSTEM = """\
You are a senior software architect. When given a development task, produce a
detailed technical design including:
- The specific endpoints or interfaces to implement
- Data models and schemas
- Key decisions made and why
- Potential edge cases to handle

Be specific and reference concrete technologies."""

_CONTEXT_SYSTEM = """\
You are a senior software architect with access to a project's SDLC context graph.

ALWAYS start by:
1. Calling agent_brief() to read existing context (use phase="intent" first, then phase="build")
2. Calling search_sdlc() to find nodes relevant to your task
3. Calling coverage_score() to see which layers are thin

When producing your design:
- Reference existing Requirement, ArchDecision, and CodeModule nodes by their node_id
- Respect and honour existing ArchDecisions — do not contradict them
- Fill coverage gaps: write at least one new artifact (CodeModule or ArchDecision) back to the graph
  using add_sdlc_node, then link it with add_sdlc_edge where appropriate

Leave the graph richer than you found it."""

# ── Tool definitions (Anthropic tool_use format) ─────────────────────────────

_CONTEXT_TOOLS = [
    {
        "name": "agent_brief",
        "description": (
            "Get scoped SDLC context for the project. Returns existing nodes "
            "filtered by phase and/or topic keyword."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "project_name": {"type": "string", "description": "Project namespace name"},
                "phase": {
                    "type": "string",
                    "enum": ["intent", "design", "build", "verify", "evolution"],
                    "description": "SDLC layer to restrict to (optional)",
                },
                "topic": {
                    "type": "string",
                    "description": "Keyword filter for node property values (optional)",
                },
            },
            "required": ["project_name"],
        },
    },
    {
        "name": "search_sdlc",
        "description": (
            "Search SDLC nodes by keyword relevance. Returns {node_id, label, layer, score, snippet} "
            "ordered by relevance."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "project_name": {"type": "string"},
                "query": {"type": "string", "description": "Space-separated keywords"},
                "layer": {
                    "type": "string",
                    "enum": ["intent", "design", "build", "verify", "evolution"],
                    "description": "Optional layer filter",
                },
                "limit": {"type": "integer", "default": 10},
            },
            "required": ["project_name", "query"],
        },
    },
    {
        "name": "coverage_score",
        "description": (
            "Return per-layer SDLC coverage scores (0.0–1.0). "
            "Shows which layers are thin and need more artifacts."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "project_name": {"type": "string"},
            },
            "required": ["project_name"],
        },
    },
    {
        "name": "add_sdlc_node",
        "description": (
            "Write a typed SDLC artifact node to the project. "
            f"Valid node_type values: {', '.join(ALL_NODE_TYPES)}. "
            "properties must include required fields for the node_type."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "project_name": {"type": "string"},
                "node_type": {"type": "string"},
                "node_id": {
                    "type": "string",
                    "description": "Unique ID, e.g. 'code:login-endpoint'",
                },
                "properties": {
                    "type": "object",
                    "description": "Node properties as a JSON object",
                },
            },
            "required": ["project_name", "node_type", "node_id", "properties"],
        },
    },
    {
        "name": "add_sdlc_edge",
        "description": (
            "Declare a typed relationship between two SDLC nodes. "
            f"Valid edge_type values: {', '.join(sorted(SDLC_EDGE_TYPES))}."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "project_name": {"type": "string"},
                "edge_type": {"type": "string"},
                "source_id": {"type": "string"},
                "target_id": {"type": "string"},
            },
            "required": ["project_name", "edge_type", "source_id", "target_id"],
        },
    },
    {
        "name": "spec_code_diff",
        "description": (
            "Compare spec (requirements/decisions) vs code (modules/tests). "
            "Returns: unimplemented requirements, untested requirements, "
            "untracked code modules, unenforced architecture decisions, and coverage %."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "project_name": {"type": "string"},
            },
            "required": ["project_name"],
        },
    },
]

# Workspace tools — allow agents to write actual code, run tests, commit
_WORKSPACE_TOOLS = [
    {
        "name": "ws_write_file",
        "description": "Write a file to the project workspace. Creates directories if needed.",
        "input_schema": {
            "type": "object",
            "properties": {
                "filepath": {"type": "string", "description": "Relative path, e.g. 'src/auth.py'"},
                "content": {"type": "string", "description": "Full file content to write"},
            },
            "required": ["filepath", "content"],
        },
    },
    {
        "name": "ws_read_file",
        "description": "Read a file from the project workspace.",
        "input_schema": {
            "type": "object",
            "properties": {
                "filepath": {"type": "string", "description": "Relative path to read"},
            },
            "required": ["filepath"],
        },
    },
    {
        "name": "ws_list_files",
        "description": "List files in the project workspace directory.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Directory path (default: '.')", "default": "."},
            },
        },
    },
    {
        "name": "ws_run_command",
        "description": "Run a shell command in the project workspace (e.g. 'pytest tests/').",
        "input_schema": {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "Shell command to execute"},
            },
            "required": ["command"],
        },
    },
    {
        "name": "ws_git_status",
        "description": "Show git status of the workspace.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "ws_commit",
        "description": "Commit staged changes with a message.",
        "input_schema": {
            "type": "object",
            "properties": {
                "message": {"type": "string", "description": "Commit message"},
            },
            "required": ["message"],
        },
    },
]

# Combined: SDLC context tools + workspace tools
_ALL_TOOLS = _CONTEXT_TOOLS + _WORKSPACE_TOOLS

# ── AgentResult ───────────────────────────────────────────────────────────────

@dataclass
class AgentResult:
    """Result from running one agent pass."""
    response_text: str
    tool_calls: List[Dict[str, Any]]
    model: str


# ── Tool dispatcher ───────────────────────────────────────────────────────────

def _dispatch_tool(
    tool_name: str,
    tool_input: Dict[str, Any],
    base_path: str,
) -> str:
    """Execute a tool call against the real ProjectContext and return a JSON string."""
    project_name = tool_input.get("project_name", "")

    if tool_name == "agent_brief":
        pc = ProjectContext.load(project_name, base_path=base_path)
        result = pc.agent_brief(
            topic=tool_input.get("topic", ""),
            phase=tool_input.get("phase", ""),
        )
        return json.dumps(result)

    if tool_name == "search_sdlc":
        pc = ProjectContext.load(project_name, base_path=base_path)
        result = pc.search(
            query=tool_input.get("query", ""),
            layer=tool_input.get("layer", ""),
            limit=tool_input.get("limit", 10),
        )
        return json.dumps(result)

    if tool_name == "coverage_score":
        pc = ProjectContext.load(project_name, base_path=base_path)
        return json.dumps(pc.coverage_score())

    if tool_name == "spec_code_diff":
        pc = ProjectContext.load(project_name, base_path=base_path)
        return json.dumps(pc.spec_code_diff())

    if tool_name == "add_sdlc_node":
        node_type = tool_input.get("node_type", "")
        node_id = tool_input.get("node_id", "")
        properties = tool_input.get("properties", {})

        if not is_sdlc_node_type(node_type):
            return json.dumps({"ok": False, "error": f"Unknown node_type: {node_type}"})

        missing = validate_node(node_type, properties)
        if missing:
            return json.dumps({"ok": False, "error": f"Missing required fields: {missing}"})

        pc = ProjectContext.load(project_name, base_path=base_path)
        existing = pc.db.get_node(node_id)
        if existing is not None:
            return json.dumps({
                "ok": False,
                "error": "conflict",
                "existing_label": existing.label,
                "hint": "Use a different node_id.",
            })

        pc.db.add_node(GraphNode(id=node_id, label=node_type, properties=properties))
        return json.dumps({"ok": True, "node_id": node_id, "node_type": node_type})

    if tool_name == "add_sdlc_edge":
        edge_type = tool_input.get("edge_type", "")
        source_id = tool_input.get("source_id", "")
        target_id = tool_input.get("target_id", "")

        if edge_type not in SDLC_EDGE_TYPES:
            return json.dumps({"ok": False, "error": f"Unknown edge_type: {edge_type}"})

        exp_src_label, exp_tgt_label = SDLC_EDGE_TYPES[edge_type]
        pc = ProjectContext.load(project_name, base_path=base_path)

        src = pc.db.get_node(source_id)
        if src is None:
            return json.dumps({"ok": False, "error": f"Source node not found: {source_id}"})
        if src.label != exp_src_label:
            return json.dumps({
                "ok": False,
                "error": f"{edge_type} requires source label '{exp_src_label}', got '{src.label}'",
            })

        tgt = pc.db.get_node(target_id)
        if tgt is None:
            return json.dumps({"ok": False, "error": f"Target node not found: {target_id}"})
        if tgt.label != exp_tgt_label:
            return json.dumps({
                "ok": False,
                "error": f"{edge_type} requires target label '{exp_tgt_label}', got '{tgt.label}'",
            })

        pc.db.add_edge(GraphEdge(
            id=str(uuid.uuid4()),
            source=source_id,
            target=target_id,
            label=edge_type,
        ))
        return json.dumps({"ok": True, "edge_type": edge_type, "source_id": source_id, "target_id": target_id})

    # ── Workspace tools ────────────────────────────────────────────────────
    if tool_name.startswith("ws_"):
        return _dispatch_workspace_tool(tool_name, tool_input, base_path)

    return json.dumps({"ok": False, "error": f"Unknown tool: {tool_name}"})


def _dispatch_workspace_tool(
    tool_name: str,
    tool_input: Dict[str, Any],
    base_path: str,
) -> str:
    """Execute a workspace tool against the local filesystem."""
    import os
    from pathlib import Path

    workspace_root = Path(base_path)

    if tool_name == "ws_write_file":
        filepath = tool_input.get("filepath", "")
        content = tool_input.get("content", "")
        if not filepath:
            return json.dumps({"ok": False, "error": "filepath required"})
        target = workspace_root / filepath
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return json.dumps({"ok": True, "filepath": filepath, "bytes": len(content)})

    if tool_name == "ws_read_file":
        filepath = tool_input.get("filepath", "")
        target = workspace_root / filepath
        if not target.is_file():
            return json.dumps({"ok": False, "error": f"File not found: {filepath}"})
        text = target.read_text(encoding="utf-8", errors="replace")[:10000]
        return json.dumps({"ok": True, "filepath": filepath, "content": text})

    if tool_name == "ws_list_files":
        path = tool_input.get("path", ".")
        target = workspace_root / path
        if not target.is_dir():
            return json.dumps({"ok": False, "error": f"Not a directory: {path}"})
        files = sorted(str(f.relative_to(workspace_root)) for f in target.rglob("*") if f.is_file())[:100]
        return json.dumps({"ok": True, "files": files, "count": len(files)})

    if tool_name == "ws_run_command":
        command = tool_input.get("command", "")
        if not command:
            return json.dumps({"ok": False, "error": "command required"})
        import subprocess
        try:
            result = subprocess.run(
                command, shell=True, capture_output=True, text=True,
                cwd=str(workspace_root), timeout=60,
            )
            return json.dumps({
                "ok": result.returncode == 0,
                "returncode": result.returncode,
                "stdout": result.stdout[:5000],
                "stderr": result.stderr[:2000],
            })
        except subprocess.TimeoutExpired:
            return json.dumps({"ok": False, "error": "Command timed out (60s)"})

    if tool_name == "ws_git_status":
        import subprocess
        try:
            result = subprocess.run(
                ["git", "status", "--short"], capture_output=True, text=True,
                cwd=str(workspace_root), timeout=10,
            )
            return json.dumps({"ok": True, "status": result.stdout[:3000]})
        except Exception as exc:
            return json.dumps({"ok": False, "error": str(exc)})

    if tool_name == "ws_commit":
        message = tool_input.get("message", "")
        if not message:
            return json.dumps({"ok": False, "error": "message required"})
        import subprocess
        try:
            subprocess.run(["git", "add", "-A"], cwd=str(workspace_root), capture_output=True, timeout=10)
            result = subprocess.run(
                ["git", "commit", "-m", message], capture_output=True, text=True,
                cwd=str(workspace_root), timeout=30,
            )
            return json.dumps({"ok": result.returncode == 0, "output": result.stdout[:2000]})
        except Exception as exc:
            return json.dumps({"ok": False, "error": str(exc)})

    return json.dumps({"ok": False, "error": f"Unknown workspace tool: {tool_name}"})


# ── Agent runners ─────────────────────────────────────────────────────────────

def run_blind_agent(task: str, model: str) -> AgentResult:
    """Run Claude with no context graph access — just the raw task description.

    Args:
        task: the development task description
        model: Anthropic model ID

    Returns:
        AgentResult with response_text and empty tool_calls list.
    """
    client = anthropic.Anthropic()
    response = client.messages.create(
        model=model,
        max_tokens=1024,
        system=_BLIND_SYSTEM,
        messages=[{"role": "user", "content": task}],
    )
    text = next(
        (block.text for block in response.content if hasattr(block, "text")),
        "",
    )
    return AgentResult(response_text=text, tool_calls=[], model=model)


def run_context_agent(
    project_name: str,
    task: str,
    model: str,
    base_path: str,
) -> AgentResult:
    """Run Claude with full SDLC context graph tool access.

    The agent is given tools to read context (agent_brief, search_sdlc,
    coverage_score) and write artifacts back (add_sdlc_node, add_sdlc_edge).
    The tool-use loop runs until Claude returns end_turn.

    Args:
        project_name: the project namespace to operate on
        task: the development task description
        model: Anthropic model ID
        base_path: base_path for ProjectContext.load()

    Returns:
        AgentResult with response_text and all tool_calls recorded.
    """
    client = anthropic.Anthropic()
    messages: List[Dict[str, Any]] = [{"role": "user", "content": task}]
    tool_calls_log: List[Dict[str, Any]] = []

    while True:
        response = client.messages.create(
            model=model,
            max_tokens=2048,
            system=_CONTEXT_SYSTEM,
            tools=_CONTEXT_TOOLS,
            messages=messages,
        )

        if response.stop_reason == "end_turn":
            text = next(
                (block.text for block in response.content if hasattr(block, "text")),
                "",
            )
            return AgentResult(response_text=text, tool_calls=tool_calls_log, model=model)

        if response.stop_reason == "tool_use":
            messages.append({"role": "assistant", "content": response.content})
            tool_results = []

            for block in response.content:
                if block.type != "tool_use":
                    continue
                result_str = _dispatch_tool(block.name, block.input, base_path)
                tool_calls_log.append({
                    "tool": block.name,
                    "input": block.input,
                    "result": result_str,
                })
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": result_str,
                })

            messages.append({"role": "user", "content": tool_results})
            continue

        # Unexpected stop reason — break to avoid infinite loop
        break

    return AgentResult(response_text="", tool_calls=tool_calls_log, model=model)

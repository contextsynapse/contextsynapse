"""Multi-agent session demo — progressive context accumulation.

Phase 1: Atomic Context  — seed SDLC graph
Phase 2: Session Context — create session with SessionStore
Phase 3: Agents Join     — grant write access to architect + developer
Phase 4: Agents Execute  — run each agent sequentially, both write back to graph
"""
from __future__ import annotations

import os
import time
import uuid
from dataclasses import dataclass
from typing import Any, Dict, List

from contextsynapse.context.session import SessionStore
from contextsynapse.demo.runner import _dispatch_tool, _ALL_TOOLS
from contextsynapse.demo.universal_runner import run_agent, AgentResult
from contextsynapse.demo.scenario import seed_project, AUTH_SCENARIO
from contextsynapse.project.project_context import ProjectContext


@dataclass
class SessionDemoResult:
    """Result from the full 4-phase session demo."""
    session_id: str
    before_coverage: Dict[str, Any]
    mid_coverage: Dict[str, Any]
    after_coverage: Dict[str, Any]
    architect_result: AgentResult
    dev_result: AgentResult
    elapsed_ms: int


_MAX_TURNS = 20


_ARCHITECT_SYSTEM = """\
You are a software architect in a multi-agent session. You have two kinds of tools:

CONTEXT TOOLS (read/write the SDLC knowledge graph):
  agent_brief()    — read existing requirements, decisions, code modules
  search_sdlc()    — search for specific topics
  coverage_score() — see which SDLC layers need work
  add_sdlc_node()  — write new ArchDecision, Constraint, etc.
  add_sdlc_edge()  — link nodes together

WORKSPACE TOOLS (read/write actual files):
  ws_read_file()   — read source code
  ws_list_files()  — browse the project structure
  ws_write_file()  — write design docs, config files
  ws_run_command()  — run commands

Your workflow:
1. Read context: agent_brief(phase="intent"), agent_brief(phase="design")
2. Read code: ws_list_files(), ws_read_file() for key modules
3. Identify gaps in the DESIGN layer via coverage_score()
4. Write architecture decisions and constraints as SDLC nodes
5. Optionally write design docs to workspace with ws_write_file()

Focus on the DESIGN layer. Leave build and verify to the developer agent."""

_DEVELOPER_SYSTEM = """\
You are a senior developer in a multi-agent session. You have two kinds of tools:

CONTEXT TOOLS (read/write the SDLC knowledge graph):
  agent_brief()    — read existing requirements, decisions, code modules
  search_sdlc()    — search for specific topics
  coverage_score() — see which SDLC layers need work
  add_sdlc_node()  — write new CodeModule, TestCase, etc.
  add_sdlc_edge()  — link nodes together

WORKSPACE TOOLS (read/write actual files):
  ws_read_file()   — read source code
  ws_list_files()  — browse the project structure
  ws_write_file()  — write new source files and tests
  ws_run_command()  — run tests (e.g. 'pytest tests/')
  ws_commit()      — commit your changes

Your workflow:
1. Read context: agent_brief(phase="design") to see architect's decisions
2. Read context: agent_brief(phase="build") to see existing modules
3. Check coverage_score() for gaps in BUILD and VERIFY layers
4. Write actual code with ws_write_file() — implement missing features
5. Run tests with ws_run_command("pytest tests/")
6. Track what you built: add_sdlc_node() for each new module/test
7. Link with add_sdlc_edge() (IMPLEMENTS, TESTS)
8. Commit with ws_commit()

Focus on BUILD and VERIFY layers. Respect all existing ArchDecisions."""


def _run_agent(
    agent_id: str,
    system: str,
    task: str,
    model: str,
    base_path: str,
    project_name: str,
    provider: str = "anthropic",
) -> AgentResult:
    """Run a single agent with tool-use loop via ANY LLM provider.

    Supports: anthropic, openai, groq, deepseek, together, mistral,
    cerebras, fireworks, perplexity, ollama.
    """
    def dispatch(tool_name: str, tool_input: Dict[str, Any]) -> str:
        # Inject the real project name into tool input
        inp = dict(tool_input)
        if "project_name" in inp:
            inp["project_name"] = project_name
        return _dispatch_tool(tool_name, inp, base_path)

    return run_agent(
        provider=provider,
        model=model,
        system=system,
        task=task,
        tools=_ALL_TOOLS,
        dispatch=dispatch,
    )


def run_session_demo(
    model: str = "claude-haiku-4-5-20251001",
    base_path: str = "contextcore_data",
    architect_provider: str = "anthropic",
    architect_model: str = "",
    dev_provider: str = "anthropic",
    dev_model: str = "",
) -> SessionDemoResult:
    """Execute the full 4-phase session demo with ANY LLM providers.

    Mix providers: e.g. Claude as architect, GPT as developer.

    Args:
        model: default model (used when provider-specific model not set)
        base_path: directory for project index and session DB
        architect_provider: LLM provider for architect agent
        architect_model: model for architect (defaults to `model`)
        dev_provider: LLM provider for developer agent
        dev_model: model for developer (defaults to `model`)
    """
    t0 = time.perf_counter()
    uid = uuid.uuid4().hex[:8]
    project_name = f"auth-demo-{uid}"

    # ── Phase 1: Atomic Context ───────────────────────────────────────────
    pc = seed_project(project_name, base_path=base_path)
    before_coverage = pc.coverage_score()

    # ── Phase 2: Session Context ──────────────────────────────────────────
    db_path = os.path.join(base_path, "sessions.db")
    store = SessionStore(db_path=db_path)
    session = store.create_session(
        f"sprint-{uid}",
        owner_agent_id="orchestrator",
    )

    # ── Phase 3: Agents Join ──────────────────────────────────────────────
    store.grant_access(session.session_id, "architect-agent", "write")
    store.grant_access(session.session_id, "dev-agent", "write")

    # ── Phase 4: Agents Execute ───────────────────────────────────────────
    task = AUTH_SCENARIO["task"]

    architect_result = _run_agent(
        agent_id="architect-agent",
        system=_ARCHITECT_SYSTEM,
        task=task,
        model=architect_model or model,
        base_path=base_path,
        project_name=project_name,
        provider=architect_provider,
    )
    mid_coverage = pc.coverage_score()

    dev_result = _run_agent(
        agent_id="dev-agent",
        system=_DEVELOPER_SYSTEM,
        task=task,
        model=dev_model or model,
        base_path=base_path,
        project_name=project_name,
        provider=dev_provider,
    )
    after_coverage = pc.coverage_score()

    elapsed_ms = int((time.perf_counter() - t0) * 1000)

    return SessionDemoResult(
        session_id=session.session_id,
        before_coverage=before_coverage,
        mid_coverage=mid_coverage,
        after_coverage=after_coverage,
        architect_result=architect_result,
        dev_result=dev_result,
        elapsed_ms=elapsed_ms,
    )


def run_session_on_project(
    pc: ProjectContext,
    task: str,
    model: str = "claude-haiku-4-5-20251001",
    base_path: str = "contextcore_data",
    architect_provider: str = "anthropic",
    architect_model: str = "",
    dev_provider: str = "anthropic",
    dev_model: str = "",
) -> SessionDemoResult:
    """Run the 4-phase session demo on an EXISTING ProjectContext.

    Phase 1 (Atomic Context) is already done — the caller provides ``pc``
    with nodes already ingested. This function handles Phases 2-4.

    Mix providers: Claude as architect, GPT as developer, Groq as QA, etc.

    Args:
        pc: ProjectContext with SDLC nodes already ingested
        task: the task description for agents to work on
        model: default model ID
        base_path: directory for session DB
        architect_provider: LLM provider for architect (anthropic, openai, groq, etc.)
        architect_model: model for architect (defaults to `model`)
        dev_provider: LLM provider for developer
        dev_model: model for developer (defaults to `model`)
    """
    t0 = time.perf_counter()
    uid = uuid.uuid4().hex[:8]

    # ── Phase 1: Atomic Context (already done by caller) ─────────────────
    before_coverage = pc.coverage_score()

    # ── Phase 2: Session Context ─────────────────────────────────────────
    db_path = os.path.join(base_path, "sessions.db")
    store = SessionStore(db_path=db_path)
    session = store.create_session(
        f"sprint-{uid}",
        owner_agent_id="orchestrator",
    )

    # ── Phase 3: Agents Join ─────────────────────────────────────────────
    store.grant_access(session.session_id, "architect-agent", "write")
    store.grant_access(session.session_id, "dev-agent", "write")

    # ── Phase 4: Agents Execute ──────────────────────────────────────────
    architect_result = _run_agent(
        agent_id="architect-agent",
        system=_ARCHITECT_SYSTEM,
        task=task,
        model=architect_model or model,
        base_path=base_path,
        project_name=pc.name,
        provider=architect_provider,
    )
    mid_coverage = pc.coverage_score()

    dev_result = _run_agent(
        agent_id="dev-agent",
        system=_DEVELOPER_SYSTEM,
        task=task,
        model=dev_model or model,
        base_path=base_path,
        project_name=pc.name,
        provider=dev_provider,
    )
    after_coverage = pc.coverage_score()

    elapsed_ms = int((time.perf_counter() - t0) * 1000)

    return SessionDemoResult(
        session_id=session.session_id,
        before_coverage=before_coverage,
        mid_coverage=mid_coverage,
        after_coverage=after_coverage,
        architect_result=architect_result,
        dev_result=dev_result,
        elapsed_ms=elapsed_ms,
    )

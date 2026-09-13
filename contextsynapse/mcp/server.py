"""
AIContextDB MCP Server
==================
Exposes AIContextDB's Universal Tool Layer as MCP tools that any
MCP-compatible agent (Claude Code, Copilot, etc.) can use.

Two agents using this server share the SAME graph — that's the point.

Run directly:
    python -m contextsynapse.mcp.server
    python -m contextsynapse.mcp.server --namespace myproject
    python -m contextsynapse.mcp.server --transport sse --port 8100

Or register with Claude Code:
    claude mcp add contextcore -- python -m contextsynapse.mcp.server
"""

import json
import logging
import os
import sys
import builtins

# ── CRITICAL: Protect stdout from stray print() calls ──────────────
# MCP uses stdout exclusively for JSON-RPC. Any print() from any module
# (logging, indexing, etc.) corrupts the protocol and crashes the server.
# Override builtins.print to redirect to stderr.
_original_print = builtins.print

def _safe_print(*args, **kwargs):
    """Redirect all print() to stderr to protect MCP's stdout."""
    kwargs['file'] = kwargs.get('file', sys.stderr)
    if kwargs['file'] is sys.stdout:
        kwargs['file'] = sys.stderr
    _original_print(*args, **kwargs)

builtins.print = _safe_print

# ── CRITICAL: Force ALL logging to stderr BEFORE any imports ────────
# Replace stdout with a stderr wrapper so even modules that add
# StreamHandler(sys.stdout) end up writing to stderr.
_real_stdout = sys.stdout
sys.stdout = sys.stderr  # Temporarily redirect stdout to stderr for imports

from mcp.server.fastmcp import FastMCP

# Restore real stdout for MCP transport
sys.stdout = _real_stdout

# Configure logging to stderr
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [contextcore-mcp] %(message)s",
    stream=sys.stderr,
    force=True,
)
# Force ALL existing and future handlers to stderr
for handler in logging.root.handlers[:]:
    if hasattr(handler, 'stream'):
        handler.stream = sys.stderr

# Monkey-patch StreamHandler to always use stderr
_OrigStreamHandler = logging.StreamHandler
class _StderrStreamHandler(_OrigStreamHandler):
    def __init__(self, stream=None):
        super().__init__(sys.stderr)
logging.StreamHandler = _StderrStreamHandler

logger = logging.getLogger(__name__)

# ── Lazy-loaded shared state ─────────────────────────────────────────
_conn = None  # AIContextDBConnection singleton
_agent_name = os.environ.get("CONTEXTSYNAPSE_AGENT_NAME") or os.environ.get("AICONTEXTDB_AGENT_NAME", "unknown")
_agent_id = None
_api_key = None
_registry = None
_resolved_session = None
_thread_id = None
_tool_context = None  # ToolContext for dispatch


def _get_registry():
    global _registry
    if _registry is None:
        from contextsynapse.context.store_factory import create_agent_registry
        _registry = create_agent_registry()
    return _registry


def _ensure_registered():
    global _agent_id, _api_key
    if _agent_id and _api_key:
        return _agent_id, _api_key
    try:
        from datetime import datetime, timezone
        registry = _get_registry()
        agent, api_key = registry.register(
            name=_agent_name, role="agent", platform="desktop",
            capabilities=["read", "write"],
            metadata={"adapter": "mcp", "registered_at": datetime.now(timezone.utc).isoformat()},
        )
        _agent_id = agent.agent_id
        _api_key = api_key
        logger.info(f"MCP agent registered: {_agent_name} (id: {_agent_id})")
    except Exception as e:
        logger.warning(f"Agent registration failed: {e}")
        _agent_id = _agent_name
        _api_key = ""
    return _agent_id, _api_key


def _get_conn():
    global _conn, _thread_id

    # Multi-session mode: check ContextVar first
    from .session_context import get_session_scope
    scope = get_session_scope()
    if scope is not None:
        if scope.conn is not None:
            return scope.conn
        # Build connection for this session scope
        conn = _build_conn_for_namespace(scope.graph_namespace)
        if conn:
            conn._fan_out_graphs = scope.fan_out_graphs
        scope.conn = conn
        return conn

    # Stdio single-session mode: use module globals (backward compat)
    if _conn is None:
        project_root = os.environ.get(
            "AICONTEXTDB_PROJECT_ROOT",
            os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")),
        )
        if project_root not in sys.path:
            sys.path.insert(0, project_root)

        if _resolved_session:
            namespace = _resolved_session.graph_namespace
            _thread_id = _resolved_session.thread_id
        else:
            namespace = os.environ.get("CONTEXTSYNAPSE_NAMESPACE") or os.environ.get("AICONTEXTDB_NAMESPACE", "default")

        # Use Redis-backed connection if Redis URL is available
        redis_url = os.environ.get("CONTEXTSYNAPSE_REDIS_URL") or os.environ.get("AICONTEXTDB_REDIS_URL")
        if redis_url:
            try:
                from contextsynapse.core.registry import GraphRegistry
                reg = GraphRegistry()
                db = reg.get_graph(namespace, load_if_missing=True)
                if db:
                    # Wrap the AIContextDB instance as an AIContextDBConnection
                    from contextsynapse.adapters._base import AIContextDBConnection
                    _conn = AIContextDBConnection(namespace=namespace)
                    # Override the connection's db with the Redis-backed one
                    _conn.db = db
                    _conn._namespace = namespace
                    # Set up executor for AIQL queries
                    try:
                        from contextsynapse.aiql import AIQLExecutor
                        _conn.executor = AIQLExecutor(contextcore=db)
                    except Exception:
                        pass
                    logger.info(f"Connected via Redis registry (namespace: {namespace}, "
                               f"nodes: {len(db.get_all_nodes())})")
                else:
                    logger.warning(f"Graph '{namespace}' not in Redis registry, falling back to file")
                    from contextsynapse.adapters._base import AIContextDBConnection
                    _conn = AIContextDBConnection(namespace=namespace)
            except Exception as e:
                logger.warning(f"Redis connection failed: {e}, falling back to file")
                from contextsynapse.adapters._base import AIContextDBConnection
                _conn = AIContextDBConnection(namespace=namespace)
        else:
            from contextsynapse.adapters._base import AIContextDBConnection
            daemon_url = os.environ.get("CONTEXTSYNAPSE_DAEMON_URL") or os.environ.get("AICONTEXTDB_DAEMON_URL")
            _conn = AIContextDBConnection(namespace=namespace, daemon_url=daemon_url)

        # Attach fan-out search capability for session-based connections
        if _conn and _resolved_session:
            _setup_fan_out(_conn, _resolved_session.session_id)
        elif _conn:
            # Namespace mode — check if this namespace is a session's runtime graph
            _setup_fan_out_by_namespace(_conn, namespace)

        if not _resolved_session:
            _ensure_registered()
        logger.info(f"AIContextDB connection ready (namespace: {namespace}, agent: {_agent_name})")
    return _conn


def _build_conn_for_namespace(namespace: str):
    """Build an AIContextDBConnection for a given namespace. Used by both single and multi-session modes."""
    redis_url = os.environ.get("CONTEXTSYNAPSE_REDIS_URL") or os.environ.get("AICONTEXTDB_REDIS_URL")
    if redis_url:
        try:
            from contextsynapse.core.registry import GraphRegistry
            reg = GraphRegistry()
            db = reg.get_graph(namespace, load_if_missing=True)
            if db:
                from contextsynapse.adapters._base import AIContextDBConnection
                conn = AIContextDBConnection(namespace=namespace)
                conn.db = db
                conn._namespace = namespace
                try:
                    from contextsynapse.aiql import AIQLExecutor
                    conn.executor = AIQLExecutor(contextcore=db)
                except Exception:
                    pass
                return conn
        except Exception:
            pass
    from contextsynapse.adapters._base import AIContextDBConnection
    return AIContextDBConnection(namespace=namespace)


def _setup_fan_out(conn, session_id: str):
    """Attach fan-out search to a connection so it searches across attached graphs."""
    try:
        from contextsynapse.context.session import ContextSessionManager
        from contextsynapse.core.registry import GraphRegistry

        sm = ContextSessionManager(graph_registry=GraphRegistry())
        attached = sm.get_attached_contexts(session_id)
        if not attached:
            return

        extra_dbs = []
        reg = GraphRegistry()
        for ctx_ref in attached:
            ctx_ns = ctx_ref.get("graph_namespace", "")
            if ctx_ns:
                ctx_db = reg.get_graph(ctx_ns, load_if_missing=True)
                if ctx_db:
                    extra_dbs.append((ctx_ns, ctx_db))
                    logger.info(f"Fan-out: attached graph '{ctx_ns}' ({ctx_ref.get('name', '?')})")

        if extra_dbs:
            conn._fan_out_graphs = extra_dbs
    except Exception as e:
        logger.debug(f"Fan-out setup failed: {e}")


def _setup_fan_out_by_namespace(conn, namespace: str):
    """For namespace mode: check if any session uses this graph and attach fan-out."""
    try:
        from contextsynapse.context.session import ContextSessionManager
        from contextsynapse.core.registry import GraphRegistry

        sm = ContextSessionManager(graph_registry=GraphRegistry())
        # Find session that uses this namespace as runtime graph
        for session in sm.list_sessions():
            if session.graph_namespace == namespace:
                _setup_fan_out(conn, session.session_id)
                return
    except Exception:
        pass


def _get_tool_context():
    """Build or return the ToolContext for dispatching tools."""
    global _tool_context
    from contextsynapse.tools import ToolContext

    # Multi-session mode: build context from session scope
    from .session_context import get_session_scope
    scope = get_session_scope()
    if scope is not None:
        if scope.tool_context is not None:
            return scope.tool_context
        conn = _get_conn()  # Will use scope.conn via context var
        # Build runtime connection for agent-produced work
        rt_ns = scope.runtime_namespace or f"{scope.graph_namespace}_rt"
        if scope.runtime_conn is None:
            scope.runtime_conn = _build_conn_for_namespace(rt_ns)
        ctx = ToolContext(
            conn=conn,
            runtime_conn=scope.runtime_conn,
            agent_id=scope.agent_id or "unknown",
            agent_name=scope.agent_name,
            thread_id=scope.thread_id or "",
        )
        ctx._access_level = scope.access_level
        # Attach project — tasks/findings go to the runtime graph, not the atomic context
        try:
            from contextsynapse.project.graph import ProjectGraph
            ctx.project = ProjectGraph(rt_ns, connection=scope.runtime_conn)
        except Exception:
            pass
        scope.tool_context = ctx
        return ctx

    # Stdio single-session mode: use module globals
    conn = _get_conn()

    # Build runtime connection for agent-produced work
    base_ns = conn.namespace if hasattr(conn, 'namespace') else (
        getattr(conn, '_namespace', '') or 'default')
    rt_ns = f"{base_ns}_rt"
    if _resolved_session and getattr(_resolved_session, 'runtime_namespace', ''):
        rt_ns = _resolved_session.runtime_namespace
    rt_conn = _build_conn_for_namespace(rt_ns)

    _tool_context = ToolContext(
        conn=conn,
        runtime_conn=rt_conn,
        agent_id=_agent_id or "unknown",
        agent_name=_agent_name,
        thread_id=_thread_id or "",
    )

    # Attach project — tasks/findings go to the runtime graph, not the atomic context
    try:
        from contextsynapse.project.team_tools import get_project
        _tool_context.project = get_project()
    except Exception:
        try:
            from contextsynapse.project.graph import ProjectGraph
            _tool_context.project = ProjectGraph(rt_ns, connection=rt_conn)
        except Exception:
            pass

    return _tool_context


_DEFAULT_BASE_PATH = "contextcore_data"


# Valid edge labels for spec traceability (module-level so tests can import them)
_VALID_TRACE_LABELS: frozenset = frozenset([
    "IMPLEMENTED_BY", "VERIFIED_BY", "COVERS", "REFINES", "CONSTRAINED_BY",
    "PRESERVES", "STRANGLES", "REPLACES",
])


def _load_code_context(project_name: str):
    """Load a CodeContext instance for the given project name.

    Returns None if the project cannot be found or loaded.
    Used by the spec-driven MCP tools.
    """
    try:
        from contextsynapse.project.code_context import CodeContext
        return CodeContext.load(project_name)
    except Exception:
        return None


def _warmup_embedding() -> None:
    """Start embedding model warmup in a background daemon thread.

    Ollama loads the model on first embed_text() call (3-5s). By triggering
    get_session_vector_store() at server startup in a daemon thread, the model
    is already warm when the first agent calls orient().
    """
    import threading as _threading

    def _do_warmup():
        try:
            from contextsynapse.context.vector_integration import get_session_vector_store
            get_session_vector_store()
        except Exception:
            pass  # warmup is best-effort — never crash the server

    t = _threading.Thread(target=_do_warmup, daemon=True)
    t.start()


# =====================================================================
# MCP Server Definition
# =====================================================================

def _auto_capture_fn(tool_name, params, result, ctx):
    """Auto-capture tool call for conversation tracking."""
    try:
        from contextsynapse.agents.auto_capture import capture_tool_call
        capture_tool_call(
            agent_name=ctx.agent_name if hasattr(ctx, 'agent_name') else _agent_name,
            agent_id=ctx.agent_id if hasattr(ctx, 'agent_id') else (_agent_id or "unknown"),
            tool_name=tool_name,
            params=params,
            result=str(result)[:200],
            namespace=ctx.conn._namespace if hasattr(ctx.conn, '_namespace') else "",
        )
    except Exception:
        pass  # Never let capture errors break tool execution


def _make_mcp_wrapper(tool_def):
    """Create a wrapper function with EXPLICIT named parameters for FastMCP.

    FastMCP inspects function signatures to generate tool schemas.
    Using **kwargs produces a single 'kwargs' parameter in the schema,
    which breaks everything. Instead, we dynamically create a function
    with the exact parameter names the tool expects.
    """
    from contextsynapse.tools import ToolRegistry
    import inspect

    tool_name = tool_def.name
    params = tool_def.params

    if len(params) == 0:
        def wrapper() -> str:
            ctx = _get_tool_context()
            return ToolRegistry.dispatch(tool_name, ctx, {})
        wrapper.__name__ = tool_name
        wrapper.__doc__ = tool_def.description
        return wrapper

    # Build a wrapper with explicit parameter names using exec
    # This is the only way to create a function with dynamic parameter names
    # that FastMCP can inspect via signature introspection.
    param_parts = []
    for p in params:
        default = '""' if not p.required else None
        if hasattr(p, 'default') and p.default not in (None, type(None)):
            from contextsynapse.tools.registry import _MISSING
            if p.default is not _MISSING:
                default = repr(str(p.default))
        if default is not None:
            param_parts.append(f'{p.name}: str = {default}')
        else:
            param_parts.append(f'{p.name}: str')

    param_str = ", ".join(param_parts)
    param_names = [p.name for p in params]
    dict_build = ", ".join(f'"{p}": {p}' for p in param_names)

    # Write tools require write access
    _WRITE_TOOLS = {"add_knowledge", "add_decision", "add_task", "add_relationship",
                    "complete_task", "claim_task", "handoff_task", "log_action",
                    "remember", "update_memory", "dispatch_goal", "init_project",
                    "ws_write_file", "ws_commit", "ws_push"}
    is_write = tool_name in _WRITE_TOOLS

    func_code = f'''
def {tool_name}({param_str}) -> str:
    """{tool_def.description}"""
    ctx = _get_tool_context()
    # Write-access control
    if {is_write} and hasattr(ctx, "_access_level") and ctx._access_level == "read":
        return "Error: Read-only access. You do not have write permission for this session."
    params = {{{dict_build}}}
    # Remove empty optional params
    params = {{k: v for k, v in params.items() if v != ""}}
    result = ToolRegistry.dispatch("{tool_name}", ctx, params)
    # Auto-capture agent activity
    _auto_capture("{tool_name}", params, result, ctx)
    return result
'''

    local_ns = {
        '_get_tool_context': _get_tool_context,
        'ToolRegistry': ToolRegistry,
        '_auto_capture': _auto_capture_fn,
    }
    exec(func_code, local_ns)
    wrapper = local_ns[tool_name]
    return wrapper


def create_mcp_server() -> FastMCP:
    """Create and return the AIContextDB MCP server with all tools.

    Tools are loaded from the Universal Tool Registry (contextcore/tools/).
    No inline tool definitions — everything comes from one source.
    """
    # Import tools to trigger registration
    import contextsynapse.tools  # noqa: F401
    from contextsynapse.tools import ToolRegistry

    mcp = FastMCP(
        "contextsynapse",
        instructions=(
            "AIContextDB is a shared graph database for AI agent collaboration. "
            "You have tools to: add knowledge nodes, search nodes, create relationships, "
            "build LLM context, and manage tasks. "
            "START HERE: Call orient() first to see what data exists, your tasks, and team status. "
            "Use the high-level tools (add_knowledge, search_nodes, claim_task, complete_task) "
            "for most work. Use query_graph only for advanced queries. "
            "Everything you write is visible to other agents."
        ),
    )

    # Warm up the embedding model in the background so first orient() is fast
    _warmup_embedding()

    # Register ALL tools from the Universal Tool Registry
    for tool_def in ToolRegistry.all():
        wrapper = _make_mcp_wrapper(tool_def)
        mcp.add_tool(wrapper, name=tool_def.name, description=tool_def.description)

    logger.info(f"MCP server created with {ToolRegistry.count()} tools from Universal Registry")

    # ── MCP-only extras (not in universal registry) ─────────────────

    # Extraction tools (MCP-only for now — depend on LLM client)
    @mcp.tool()
    def extract_entities(text: str, provider: str = "", model: str = "") -> str:
        """Extract named entities from text using LLM."""
        conn = _get_conn()
        try:
            from contextsynapse.extraction import extract_engine
            result = extract_engine.extract_entities(
                text, conn=conn, provider=provider or None, model=model or None,
            )
            return json.dumps(result, indent=2, default=str)[:3000]
        except Exception as e:
            return f"Error: {e}"

    @mcp.tool()
    def extract_facts(text: str, provider: str = "", model: str = "") -> str:
        """Extract facts/claims from text using LLM."""
        conn = _get_conn()
        try:
            from contextsynapse.extraction import extract_engine
            result = extract_engine.extract_facts(
                text, conn=conn, provider=provider or None, model=model or None,
            )
            return json.dumps(result, indent=2, default=str)[:3000]
        except Exception as e:
            return f"Error: {e}"

    @mcp.tool()
    def extract_with_schema(text: str, schema_path: str, provider: str = "", model: str = "") -> str:
        """Extract entities using a YAML schema file."""
        conn = _get_conn()
        try:
            from contextsynapse.extraction import extract_engine
            result = extract_engine.extract_with_schema(
                text, schema_path=schema_path, conn=conn,
                provider=provider or None, model=model or None,
            )
            return json.dumps(result, indent=2, default=str)[:3000]
        except Exception as e:
            return f"Error: {e}"

    @mcp.tool()
    def save_schema(schema_path: str) -> str:
        """Upload a YAML schema to the registry."""
        conn = _get_conn()
        try:
            from contextsynapse.extraction import extract_engine
            result = extract_engine.save_schema(schema_path, conn=conn)
            return result
        except Exception as e:
            return f"Error: {e}"

    @mcp.tool()
    def list_schemas() -> str:
        """List all saved schemas in the registry."""
        conn = _get_conn()
        try:
            from contextsynapse.extraction import extract_engine
            result = extract_engine.list_schemas(conn=conn)
            return result
        except Exception as e:
            return f"Error: {e}"

    @mcp.tool()
    def extract_with_saved_schema(text: str, schema_name: str, provider: str = "", model: str = "") -> str:
        """Extract entities using a schema from the registry."""
        conn = _get_conn()
        try:
            from contextsynapse.extraction import extract_engine
            result = extract_engine.extract_with_saved_schema(
                text, schema_name=schema_name, conn=conn,
                provider=provider or None, model=model or None,
            )
            return json.dumps(result, indent=2, default=str)[:3000]
        except Exception as e:
            return f"Error: {e}"

    # Project tools (SDLC context namespaces)
    @mcp.tool()
    def create_project(name: str) -> str:
        """Create an SDLC context project namespace."""
        if not name:
            return "Error: name is required."
        try:
            from contextsynapse.project.project_context import ProjectContext
            pc = ProjectContext.create(name, base_path=_DEFAULT_BASE_PATH)
            return f"Project '{pc.name}' created. Use coverage_score to check context coverage."
        except Exception as e:
            return f"Error: {e}"

    @mcp.tool()
    def add_sdlc_node(
        project_name: str,
        node_type: str,
        node_id: str,
        properties: str = "{}",
    ) -> str:
        """Add a typed SDLC artifact node to a project namespace.

        Args:
            project_name: the project namespace name (must exist)
            node_type: one of the 15 SDLC types — Requirement, UserStory, Goal,
                ArchDecision, Pattern, Constraint, CodeModule, APIContract,
                DataModel, TestCase, TestResult, KnownIssue,
                Preservation, Migration, ChangeRecord
            node_id: unique node identifier within the project (e.g. "req:auth-login")
            properties: JSON object of node properties (must include required fields
                for the node_type — check validate_node for details)
        """
        if not project_name:
            return "Error: project_name is required."
        if not node_type:
            return "Error: node_type is required."
        if not node_id:
            return "Error: node_id is required."
        try:
            import json as _json
            from contextsynapse.project.project_context import ProjectContext
            from contextsynapse.project.sdlc_schema import is_sdlc_node_type, validate_node, ALL_NODE_TYPES
            from contextsynapse.core.graph_structures import GraphNode

            if not is_sdlc_node_type(node_type):
                return (
                    f"Error: '{node_type}' is not a valid SDLC node type. "
                    f"Valid types: {', '.join(ALL_NODE_TYPES)}"
                )

            try:
                props = _json.loads(properties) if properties else {}
            except _json.JSONDecodeError as exc:
                return f"Error: properties is not valid JSON — {exc}"

            missing = validate_node(node_type, props)
            if missing:
                return (
                    f"Error: node_type '{node_type}' is missing required fields: "
                    f"{', '.join(missing)}"
                )

            pc = ProjectContext.load(project_name, base_path=_DEFAULT_BASE_PATH)
            existing = pc.db.get_node(node_id)
            if existing is not None:
                return _json.dumps({
                    "ok": False,
                    "error": "conflict",
                    "node_id": node_id,
                    "existing_label": existing.label,
                    "hint": "Use a different node_id or update the existing node.",
                })
            pc.db.add_node(GraphNode(id=node_id, label=node_type, properties=props))
            return _json.dumps({"ok": True, "node_id": node_id, "node_type": node_type})
        except KeyError as e:
            return f"Error: {e}"
        except Exception as e:
            return f"Error: {e}"

    @mcp.tool()
    def coverage_score(project_name: str) -> str:
        """Return per-layer SDLC coverage scores for a project namespace."""
        if not project_name:
            return "Error: project_name is required."
        try:
            from contextsynapse.project.project_context import ProjectContext
            pc = ProjectContext.load(project_name, base_path=_DEFAULT_BASE_PATH)
            return json.dumps(pc.coverage_score())
        except Exception as e:
            return f"Error: {e}"

    @mcp.tool()
    def project_stale(project_name: str) -> str:
        """Return all stale SDLC nodes in a project namespace."""
        if not project_name:
            return "Error: project_name is required."
        try:
            from contextsynapse.project.project_context import ProjectContext
            pc = ProjectContext.load(project_name, base_path=_DEFAULT_BASE_PATH)
            return json.dumps(pc.stale_report())
        except Exception as e:
            return f"Error: {e}"

    @mcp.tool()
    def agent_brief(project_name: str, topic: str = "", phase: str = "") -> str:
        """Return a scoped SDLC context package for an agent about to work.

        Args:
            project_name: the project namespace name
            topic: keyword to filter nodes by (case-insensitive substring match)
            phase: SDLC layer to filter to (intent/design/build/verify/evolution)
        """
        if not project_name:
            return "Error: project_name is required."
        try:
            from contextsynapse.project.project_context import ProjectContext
            pc = ProjectContext.load(project_name, base_path=_DEFAULT_BASE_PATH)
            return json.dumps(pc.agent_brief(topic=topic, phase=phase))
        except Exception as e:
            return f"Error: {e}"

    @mcp.tool()
    def add_sdlc_edge(
        project_name: str,
        edge_type: str,
        source_id: str,
        target_id: str,
    ) -> str:
        """Declare a typed relationship between two SDLC nodes in a project namespace.

        Args:
            project_name: the project namespace name (must exist)
            edge_type: one of the 12 SDLC edge types —
                IMPLEMENTS, SATISFIES, TESTS, GOVERNS, CONSTRAINED_BY,
                DEPENDS_ON, EXPOSES, USES,
                PRESERVES, STRANGLES, REPLACES, MODIFIES
            source_id: node_id of the source node (must exist in the project)
            target_id: node_id of the target node (must exist in the project)

        The edge is validated against the schema: each edge_type has a required
        source label and target label. Both nodes must already exist.
        """
        if not project_name:
            return "Error: project_name is required."
        if not edge_type:
            return "Error: edge_type is required."
        if not source_id:
            return "Error: source_id is required."
        if not target_id:
            return "Error: target_id is required."
        try:
            import uuid as _uuid
            from contextsynapse.project.project_context import ProjectContext
            from contextsynapse.project.sdlc_schema import SDLC_EDGE_TYPES
            from contextsynapse.core.graph_structures import GraphEdge

            if edge_type not in SDLC_EDGE_TYPES:
                return (
                    f"Error: '{edge_type}' is not a valid SDLC edge type. "
                    f"Valid types: {', '.join(sorted(SDLC_EDGE_TYPES))}"
                )

            expected_src_label, expected_tgt_label = SDLC_EDGE_TYPES[edge_type]

            pc = ProjectContext.load(project_name, base_path=_DEFAULT_BASE_PATH)

            src_node = pc.db.get_node(source_id)
            if src_node is None:
                return f"Error: source node '{source_id}' not found in project '{project_name}'."
            if src_node.label != expected_src_label:
                return (
                    f"Error: edge type '{edge_type}' requires source label "
                    f"'{expected_src_label}', but '{source_id}' has label '{src_node.label}'."
                )

            tgt_node = pc.db.get_node(target_id)
            if tgt_node is None:
                return f"Error: target node '{target_id}' not found in project '{project_name}'."
            if tgt_node.label != expected_tgt_label:
                return (
                    f"Error: edge type '{edge_type}' requires target label "
                    f"'{expected_tgt_label}', but '{target_id}' has label '{tgt_node.label}'."
                )

            edge_id = str(_uuid.uuid4())
            pc.db.add_edge(GraphEdge(
                id=edge_id,
                source=source_id,
                target=target_id,
                label=edge_type,
            ))
            return json.dumps({
                "ok": True,
                "edge_id": edge_id,
                "edge_type": edge_type,
                "source_id": source_id,
                "target_id": target_id,
            })
        except KeyError as e:
            return f"Error: {e}"
        except Exception as e:
            return f"Error: {e}"

    @mcp.tool()
    def search_sdlc(
        project_name: str,
        query: str,
        layer: str = "",
        limit: int = 20,
    ) -> str:
        """Search SDLC nodes in a project namespace by keyword relevance.

        Args:
            project_name: the project namespace name (must exist)
            query: space-separated keywords to search for
            layer: optional SDLC layer to restrict results to —
                   intent, design, build, verify, or evolution
            limit: max results to return (default 20, max 100)

        Returns JSON list of {node_id, label, layer, score, snippet} ordered
        by relevance. 'layer' tells you which SDLC phase each result belongs to
        so agents can reason about coverage gaps.
        """
        if not project_name:
            return "Error: project_name is required."
        if not query:
            return "Error: query is required."
        try:
            from contextsynapse.project.project_context import ProjectContext
            pc = ProjectContext.load(project_name, base_path=_DEFAULT_BASE_PATH)
            results = pc.search(query=query, layer=layer, limit=min(limit, 100))
            return json.dumps(results)
        except (KeyError, ValueError) as e:
            return f"Error: {e}"
        except Exception as e:
            return f"Error: {e}"

    # Spec-driven traceability tools
    @mcp.tool()
    def ingest_spec(project_name: str, markdown_text: str) -> str:
        """Parse a markdown requirements spec and write Requirement/Feature/Constraint nodes.

        Returns JSON with the list of created node IDs and count.
        """
        cc = _load_code_context(project_name)
        if cc is None:
            return json.dumps({"error": f"Project '{project_name}' not found"})
        ids = cc.ingest_spec(markdown_text)
        return json.dumps({"created": ids, "count": len(ids)})

    @mcp.tool()
    def get_requirements(project_name: str) -> str:
        """Return all Requirement nodes for a project as JSON."""
        cc = _load_code_context(project_name)
        if cc is None:
            return json.dumps({"error": f"Project '{project_name}' not found"})
        reqs = cc.get_requirements()
        return json.dumps(reqs, default=str)

    @mcp.tool()
    def check_coverage(project_name: str) -> str:
        """Analyse requirement traceability coverage for a project.

        Returns JSON with keys: covered, partial, uncovered, coverage_pct, total.
        """
        cc = _load_code_context(project_name)
        if cc is None:
            return json.dumps({"error": f"Project '{project_name}' not found"})
        return json.dumps(cc.check_coverage())

    @mcp.tool()
    def link_artifact(
        project_name: str, req_id: str, artifact_id: str, edge_label: str
    ) -> str:
        """Create a traceability edge between a requirement and an artifact.

        edge_label must be one of: IMPLEMENTED_BY, VERIFIED_BY, COVERS, REFINES, CONSTRAINED_BY.
        """
        if edge_label not in _VALID_TRACE_LABELS:
            return json.dumps({"error": f"Invalid edge label '{edge_label}'. "
                               f"Must be one of: {sorted(_VALID_TRACE_LABELS)}"})
        cc = _load_code_context(project_name)
        if cc is None:
            return json.dumps({"error": f"Project '{project_name}' not found"})
        cc.link_artifact(req_id, artifact_id, edge_label)
        return json.dumps({"ok": True, "edge": f"{req_id} --{edge_label}--> {artifact_id}"})

    # Pipeline tool
    @mcp.tool()
    def run_pipeline(prompt: str, session_id: str = "", max_turns: int = 10) -> str:
        """Run a multi-agent pipeline on a session."""
        return "Pipeline execution is available via the dashboard. Use the REST API to trigger it."

    # Resource
    @mcp.resource("contextcore://summary")
    def resource_summary() -> str:
        """Current graph summary."""
        ctx = _get_tool_context()
        from contextsynapse.tools import ToolRegistry
        return ToolRegistry.dispatch("graph_summary", ctx, {})

    return mcp


# =====================================================================
# Entry point
# =====================================================================


def create_multi_session_app(graph_registry=None):
    """Create a Starlette app that serves MCP for multiple sessions.

    Each session is accessed via /sessions/{session_id}/sse (or /messages).
    Authentication happens per-connection via Bearer token.

    Mount on the main FastAPI app:
        app.mount("/mcp", create_multi_session_app(graph_registry))
    """
    from .auth_middleware import MCPAuthMiddleware

    server = create_mcp_server()

    # Get the SSE Starlette app from FastMCP
    try:
        sse_app = server.sse_app()
    except Exception:
        # Fallback: use streamable_http_app if available
        try:
            sse_app = server.streamable_http_app()
        except Exception:
            logger.error("[MCP] Failed to create SSE/HTTP app from FastMCP")
            from starlette.applications import Starlette
            return Starlette()

    # Wrap with auth middleware
    app = MCPAuthMiddleware(sse_app, graph_registry=graph_registry)

    logger.info("[MCP] Multi-session app created (auth + session routing)")
    return app


def main():
    """Run the AIContextDB MCP server."""
    import argparse

    parser = argparse.ArgumentParser(description="AIContextDB MCP Server")
    parser.add_argument("--session", default=None, help="Session ID to connect to")
    parser.add_argument("--api-key", default=None, help="Agent API key (agent_id:secret)")
    parser.add_argument("--namespace", default="default", help="Direct namespace (legacy)")
    parser.add_argument("--agent-name", default=None, help="Agent display name")
    parser.add_argument("--transport", default="stdio", choices=["stdio", "sse", "streamable-http"],
                        help="Transport: stdio (local), sse (remote), streamable-http (remote)")
    parser.add_argument("--port", default=8100, type=int, help="Port for SSE/HTTP transport")
    args = parser.parse_args()

    global _agent_id, _api_key, _agent_name, _resolved_session

    api_key = args.api_key or os.environ.get("CONTEXTSYNAPSE_API_KEY") or os.environ.get("AICONTEXTDB_API_KEY", "")
    session_id = args.session or os.environ.get("CONTEXTSYNAPSE_SESSION_ID") or os.environ.get("AICONTEXTDB_SESSION_ID", os.environ.get("AICONTEXTDB_SESSION", ""))

    if args.agent_name:
        _agent_name = args.agent_name

    # Session-based connection (preferred)
    if session_id and api_key:
        try:
            from contextsynapse.context.session_resolver import resolve_session
            resolved = resolve_session(session_id, api_key)
            _resolved_session = resolved
            _agent_id = resolved.agent_id
            _agent_name = resolved.agent_name
            _api_key = api_key.split(":", 1)[1] if ":" in api_key else ""
            logger.info(f"Session resolved: {session_id} -> graph={resolved.graph_namespace}, "
                       f"goal='{resolved.goal}', access={resolved.access_level}")
        except Exception as e:
            # Retry once after 2s — Redis may not be ready on cold start
            logger.warning(f"Session resolution attempt 1 failed: {e}, retrying...")
            import time
            time.sleep(2)
            try:
                resolved = resolve_session(session_id, api_key)
                _resolved_session = resolved
                _agent_id = resolved.agent_id
                _agent_name = resolved.agent_name
                _api_key = api_key.split(":", 1)[1] if ":" in api_key else ""
                logger.info(f"Session resolved (retry): {session_id} -> graph={resolved.graph_namespace}")
            except Exception as e2:
                logger.error(f"Session resolution failed after retry: {e2}")
                # Fall back to namespace mode instead of exiting
                logger.info("Falling back to namespace mode: default")
                os.environ["AICONTEXTDB_NAMESPACE"] = args.namespace
    elif api_key:
        if ":" in api_key:
            _agent_id, _api_key = api_key.split(":", 1)
        os.environ["AICONTEXTDB_NAMESPACE"] = args.namespace
        logger.info(f"Starting in namespace mode: {args.namespace}")
    else:
        os.environ["AICONTEXTDB_NAMESPACE"] = args.namespace
        logger.info(f"Starting in dev mode (no auth): {args.namespace}")

    server = create_mcp_server()

    if args.transport in ("sse", "streamable-http"):
        os.environ["FASTMCP_PORT"] = str(args.port)
        os.environ["FASTMCP_HOST"] = "0.0.0.0"
        logger.info(f"MCP server starting on port {args.port} ({args.transport})")
        logger.info(f"Remote agents can connect to: http://0.0.0.0:{args.port}/mcp")

    server.run(transport=args.transport)


if __name__ == "__main__":
    main()

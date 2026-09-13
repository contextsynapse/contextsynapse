"""
Context Manager CLI
===================
Local command-line tool for building, viewing, editing, and managing
CAS context sessions.

Usage::

    python -m contextsynapse.context.cli list
    python -m contextsynapse.context.cli view <session>
    python -m contextsynapse.context.cli add <session> "some text"
    python -m contextsynapse.context.cli add <session> --file path/to/file.txt
    python -m contextsynapse.context.cli edit <session> <item_index> "new content"
    python -m contextsynapse.context.cli delete <session> [<item_index>]
    python -m contextsynapse.context.cli export <session> [--format messages|prompt|markdown|dict] [--output file.json]
    python -m contextsynapse.context.cli search <session> "query text"
    python -m contextsynapse.context.cli agents
    python -m contextsynapse.context.cli stats <session>
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Optional

# ---------------------------------------------------------------------------
# Shared initialisation — these are created once per CLI invocation
# ---------------------------------------------------------------------------

_agent_registry = None
_session_manager = None
_vector_store = None
_graph_registry = None


def _init():
    """Lazy-init shared singletons (same DB as the server uses)."""
    global _agent_registry, _session_manager, _vector_store, _graph_registry

    if _session_manager is not None:
        return

    from .agents import AgentRegistry
    from .session import ContextSessionManager
    from .vector_integration import SessionVectorStore

    try:
        from ..core.registry import GraphRegistry
        _graph_registry = GraphRegistry()
    except Exception:
        _graph_registry = None

    _agent_registry = AgentRegistry()
    _session_manager = ContextSessionManager(graph_registry=_graph_registry)
    _vector_store = SessionVectorStore()


# ---------------------------------------------------------------------------
# Hub persistence helpers
# ---------------------------------------------------------------------------

_HUB_DIR = Path("contextcore_data/context_hubs")


def _hub_path(session_id: str) -> Path:
    return _HUB_DIR / f"{session_id}.json"


def _load_hub(session_id: str):
    from .hub import ContextHub
    path = _hub_path(session_id)
    if path.exists():
        return ContextHub.load(str(path))
    return ContextHub()


def _save_hub(session_id: str, hub):
    _HUB_DIR.mkdir(parents=True, exist_ok=True)
    hub.save(str(_hub_path(session_id)))


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def cmd_list(args):
    """List all active sessions."""
    _init()
    sessions = _session_manager.list_sessions()
    if not sessions:
        print("No active sessions.")
        return

    print(f"{'ID':<26} {'Name':<20} {'Owner':<14} {'Status':<10} {'Updated'}")
    print("-" * 90)
    for s in sessions:
        owner = (s.owner_agent_id or "")[:12]
        print(f"{s.session_id:<26} {s.name:<20} {owner:<14} {s.status:<10} {s.updated_at[:19]}")


def cmd_view(args):
    """View context items for a session."""
    _init()
    session = _resolve_session(args.session)
    if not session:
        return

    hub = _load_hub(session.session_id)
    items = hub.items()

    if not items:
        # Try to build from graph
        if _graph_registry:
            graph = _graph_registry.get_graph(session.graph_namespace, load_if_missing=True)
            if graph:
                try:
                    nodes = graph.get_all_nodes()
                    if nodes:
                        hub.add_nodes(nodes, label="Session Graph Nodes")
                except Exception:
                    pass
        items = hub.items()

    if not items:
        print(f"Session '{session.name}' has no context items.")
        return

    fmt = getattr(args, "format", "markdown") or "markdown"
    if fmt == "markdown":
        print(hub.to_markdown())
    elif fmt == "messages":
        print(json.dumps(hub.to_messages(), indent=2))
    elif fmt == "prompt":
        print(hub.to_prompt())
    else:
        print(json.dumps(hub.to_dict(), indent=2))


def cmd_add(args):
    """Add text or a file to a session's context."""
    _init()
    session = _resolve_session(args.session)
    if not session:
        return

    hub = _load_hub(session.session_id)

    if args.file:
        path = Path(args.file)
        if not path.exists():
            print(f"Error: File not found: {args.file}", file=sys.stderr)
            return
        content = path.read_text(encoding="utf-8", errors="replace")
        label = path.name
        source = str(path)
    elif args.text:
        content = " ".join(args.text)
        label = args.label
        source = "cli"
    else:
        print("Error: Provide text or --file", file=sys.stderr)
        return

    role = args.role or "user"
    hub.add_text(content, role=role, label=label, source=source)
    _save_hub(session.session_id, hub)

    # Also embed into vector store
    if _vector_store and _vector_store.available:
        import uuid
        node_id = str(uuid.uuid4())
        _vector_store.add_text(
            session_id=session.session_id,
            text=content,
            node_id=node_id,
            metadata={"source": source, "label": label or ""},
        )

    print(f"Added item #{len(hub)} to session '{session.name}' (role={role})")


def cmd_edit(args):
    """Edit an existing context item by index."""
    _init()
    session = _resolve_session(args.session)
    if not session:
        return

    hub = _load_hub(session.session_id)
    items = hub.items()
    idx = args.index

    if idx < 0 or idx >= len(items):
        print(f"Error: Index {idx} out of range (0-{len(items)-1})", file=sys.stderr)
        return

    new_content = " ".join(args.content)
    hub._items[idx].content = new_content
    _save_hub(session.session_id, hub)
    print(f"Updated item #{idx} in session '{session.name}'")


def cmd_delete(args):
    """Delete a context item or an entire session."""
    _init()
    session = _resolve_session(args.session)
    if not session:
        return

    if args.index is not None:
        # Delete single item
        hub = _load_hub(session.session_id)
        items = hub.items()
        idx = args.index
        if idx < 0 or idx >= len(items):
            print(f"Error: Index {idx} out of range (0-{len(items)-1})", file=sys.stderr)
            return
        removed = hub._items.pop(idx)
        _save_hub(session.session_id, hub)
        print(f"Deleted item #{idx} ({removed.label or removed.role.value}) from '{session.name}'")
    else:
        # Delete entire session
        confirm = input(f"Delete session '{session.name}' and all its data? [y/N] ")
        if confirm.lower() != "y":
            print("Cancelled.")
            return
        _session_manager.delete_session(session.session_id)
        hub_file = _hub_path(session.session_id)
        if hub_file.exists():
            hub_file.unlink()
        if _vector_store:
            _vector_store.delete_session(session.session_id)
        print(f"Deleted session '{session.name}'")


def cmd_export(args):
    """Export a session's context to a file."""
    _init()
    session = _resolve_session(args.session)
    if not session:
        return

    hub = _load_hub(session.session_id)

    # Also pull graph nodes if hub is empty
    if len(hub) == 0 and _graph_registry:
        graph = _graph_registry.get_graph(session.graph_namespace, load_if_missing=True)
        if graph:
            try:
                nodes = graph.get_all_nodes()
                if nodes:
                    hub.add_nodes(nodes, label="Session Graph Nodes")
            except Exception:
                pass

    fmt = args.format or "messages"
    data = hub.export(fmt)

    if args.output:
        path = Path(args.output)
        if isinstance(data, str):
            path.write_text(data, encoding="utf-8")
        else:
            path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"Exported to {args.output} (format={fmt})")
    else:
        if isinstance(data, str):
            print(data)
        else:
            print(json.dumps(data, indent=2))


def cmd_search(args):
    """Semantic search across a session's embedded context."""
    _init()
    session = _resolve_session(args.session)
    if not session:
        return

    if not _vector_store or not _vector_store.available:
        print("Error: Vector search unavailable (no vector backend or embeddings)", file=sys.stderr)
        return

    query = " ".join(args.query)
    k = args.k or 10
    results = _vector_store.search(session_id=session.session_id, query=query, k=k)

    if not results:
        print("No results found.")
        return

    print(f"Top {len(results)} results for: {query}\n")
    for i, r in enumerate(results, 1):
        score = r.get("score", 0)
        nid = r.get("node_id", "?")
        meta = r.get("metadata", {})
        text = meta.get("text", "")[:200]
        print(f"  {i}. [{score:.4f}] {nid[:12]}...")
        if text:
            print(f"     {text}")
        print()


def cmd_agents(args):
    """List registered agents."""
    _init()
    agents = _agent_registry.list_agents()
    if not agents:
        print("No registered agents.")
        return

    print(f"{'ID':<26} {'Name':<20} {'Role':<10} {'Capabilities'}")
    print("-" * 80)
    for a in agents:
        caps = ", ".join(a.capabilities)
        print(f"{a.agent_id:<26} {a.name:<20} {a.role:<10} {caps}")


def cmd_stats(args):
    """Show statistics for a session."""
    _init()
    session = _resolve_session(args.session)
    if not session:
        return

    hub = _load_hub(session.session_id)

    print(f"Session: {session.name}")
    print(f"  ID:        {session.session_id}")
    print(f"  Status:    {session.status}")
    print(f"  Owner:     {session.owner_agent_id or 'none'}")
    print(f"  Graph NS:  {session.graph_namespace}")
    print(f"  Created:   {session.created_at[:19]}")
    print(f"  Updated:   {session.updated_at[:19]}")
    print(f"  Hub items: {len(hub)}")
    print(f"  Est tokens: {hub.estimate_tokens()}")

    if _vector_store:
        vs = _vector_store.get_stats(session.session_id)
        print(f"  Vectors:   {vs.get('vector_count', 0)}")
        print(f"  V-Backend: {vs.get('backend', 'n/a')}")

    # Access list
    access = _session_manager.get_access_list(session.session_id)
    if access:
        print(f"  Access:    {len(access)} agents")
        for a in access:
            print(f"    - {a['agent_id'][:12]}... ({a['access_level']})")


def cmd_create(args):
    """Create a new context session."""
    _init()
    name = args.name
    try:
        session = _session_manager.create_session(name=name)
        print(f"Created session '{name}' (id: {session.session_id})")
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)


def cmd_sync(args):
    """Sync a session with a remote CAS instance."""
    from .sync import ContextSync

    if not args.remote:
        print("Error: --remote URL is required", file=sys.stderr)
        return
    if not args.key:
        print("Error: --key <agent_id:secret> is required", file=sys.stderr)
        return

    sync = ContextSync(remote_url=args.remote, api_key=args.key)

    if args.check:
        health = sync.check_connection()
        print(f"Remote: {health}")
        return

    direction = args.direction or "sync"
    session_name = args.session

    if direction == "push":
        result = sync.push(session_name)
    elif direction == "pull":
        result = sync.pull(session_name)
    else:
        result = sync.sync(session_name)

    print(f"Sync result ({result.direction}):")
    print(f"  Pushed:  {result.items_pushed}")
    print(f"  Pulled:  {result.items_pulled}")
    print(f"  Skipped: {result.items_skipped} (duplicates)")
    if result.errors:
        print(f"  Errors:  {len(result.errors)}")
        for e in result.errors[:5]:
            print(f"    - {e}")
    print(f"  Success: {result.success}")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _resolve_session(name_or_id: str):
    """Resolve a session by name or ID."""
    # Try by name first
    session = _session_manager.get_session_by_name(name_or_id)
    if session:
        return session
    # Try by ID
    session = _session_manager.get_session(name_or_id)
    if session:
        return session
    print(f"Error: Session '{name_or_id}' not found", file=sys.stderr)
    return None


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        prog="contextcore-context",
        description="AIContextDB Context Manager — build, view, edit, and manage CAS sessions locally",
    )
    sub = parser.add_subparsers(dest="command", help="Available commands")

    # list
    sub.add_parser("list", aliases=["ls"], help="List all sessions")

    # create
    p = sub.add_parser("create", help="Create a new session")
    p.add_argument("name", help="Session name")

    # view
    p = sub.add_parser("view", help="View a session's context")
    p.add_argument("session", help="Session name or ID")
    p.add_argument("--format", "-f", choices=["markdown", "messages", "prompt", "dict"], default="markdown")

    # add
    p = sub.add_parser("add", help="Add text or file to a session")
    p.add_argument("session", help="Session name or ID")
    p.add_argument("text", nargs="*", help="Text to add")
    p.add_argument("--file", help="Path to a file to add")
    p.add_argument("--role", default="user", help="Context role (user, background, retrieved, instruction, example)")
    p.add_argument("--label", help="Label/heading for this item")

    # edit
    p = sub.add_parser("edit", help="Edit a context item")
    p.add_argument("session", help="Session name or ID")
    p.add_argument("index", type=int, help="Item index (0-based)")
    p.add_argument("content", nargs="+", help="New content")

    # delete
    p = sub.add_parser("delete", aliases=["rm"], help="Delete an item or session")
    p.add_argument("session", help="Session name or ID")
    p.add_argument("index", type=int, nargs="?", default=None, help="Item index to delete (omit to delete entire session)")

    # export
    p = sub.add_parser("export", help="Export context to file or stdout")
    p.add_argument("session", help="Session name or ID")
    p.add_argument("--format", "-f", choices=["messages", "prompt", "markdown", "dict"], default="messages")
    p.add_argument("--output", "-o", help="Output file path")

    # search
    p = sub.add_parser("search", help="Semantic search across session context")
    p.add_argument("session", help="Session name or ID")
    p.add_argument("query", nargs="+", help="Search query")
    p.add_argument("-k", type=int, default=10, help="Number of results")

    # agents
    sub.add_parser("agents", help="List registered agents")

    # stats
    p = sub.add_parser("stats", aliases=["info"], help="Show session statistics")
    p.add_argument("session", help="Session name or ID")

    # sync
    p = sub.add_parser("sync", help="Sync session with remote CAS")
    p.add_argument("session", nargs="?", help="Session name")
    p.add_argument("--remote", "-r", help="Remote CAS URL (e.g., https://host/context)")
    p.add_argument("--key", "-k", help="API key (agent_id:secret)")
    p.add_argument("--direction", "-d", choices=["push", "pull", "sync"], default="sync")
    p.add_argument("--check", action="store_true", help="Just check remote connectivity")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return

    commands = {
        "list": cmd_list, "ls": cmd_list,
        "create": cmd_create,
        "view": cmd_view,
        "add": cmd_add,
        "edit": cmd_edit,
        "delete": cmd_delete, "rm": cmd_delete,
        "export": cmd_export,
        "search": cmd_search,
        "agents": cmd_agents,
        "stats": cmd_stats, "info": cmd_stats,
        "sync": cmd_sync,
    }

    fn = commands.get(args.command)
    if fn:
        fn(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
AIContextDB Interactive CLI
Connect to server and run AIQL queries interactively
Supports both direct file access (local) and HTTP access (remote)
"""
import sys
import requests
import json
import time
from typing import Optional
from pathlib import Path

class AIContextDBCLI:
    """Interactive CLI for AIContextDB."""
    
    def __init__(self, connection_string: str = None, server_url: str = None, tinker_mode: bool = False):
        """
        Initialize AIContextDB CLI with auto-detection.
        
        Args:
            connection_string: 
                - Local path: "contextcore_data/namespaces/tcs_analysis" → Direct file access
                - HTTP URL: "http://localhost:8000" → HTTP access
                - None: Try local default → Direct file access
            server_url: Legacy parameter (use connection_string instead)
            tinker_mode: Legacy parameter (for backward compatibility)
        """
        # Handle legacy parameters
        if server_url and not connection_string:
            connection_string = server_url
        
        # Auto-detect mode based on connection_string
        if connection_string:
            if connection_string.startswith("http://") or connection_string.startswith("https://"):
                # Remote: HTTP mode
                self.mode = "http"
                self.server_url = connection_string.rstrip("/")
                self.tinker_mode = tinker_mode
                if tinker_mode:
                    self.server_url = self.server_url.replace(':8000', ':8001')
                self.session_id = None
                self.active_namespace = None
                print(f"[MODE] HTTP: Connecting to {self.server_url}")
            else:
                # Local: Direct file access
                self.mode = "direct"
                self.db_path = Path(connection_string)
                self._init_direct_connection()
        else:
            # Default: Try local direct access
            self.mode = "direct"
            self._init_local_default()
    
    def _init_direct_connection(self):
        """Initialize direct file access."""
        from ...core.hybrid_graph_storage import AIContextDB
        from ...aiql.engine import AIQLExecutor
        
        # Extract namespace from path
        namespace = self.db_path.name
        
        # Create graph instance
        graph = AIContextDB(name=namespace, storage_backend='csr')
        
        # Try to load graph file (check multiple possible locations/formats)
        graph_file = None
        
        # Check for HDF5 format (preferred)
        h5_file = self.db_path / "graph.h5"
        json_file = self.db_path / "graph.json"
        
        if h5_file.exists():
            graph_file = h5_file
        elif json_file.exists():
            graph_file = json_file
        
        # Also check parent directory (namespace-centric structure)
        if not graph_file:
            parent_h5 = self.db_path.parent / namespace / "graph.h5"
            parent_json = self.db_path.parent / namespace / "graph.json"
            if parent_h5.exists():
                graph_file = parent_h5
            elif parent_json.exists():
                graph_file = parent_json
        
        # Load graph if file exists
        if graph_file and graph_file.exists():
            success = graph.load(str(graph_file))
            if success:
                print(f"[OK] Loaded graph from {graph_file}")
            else:
                print(f"[WARN] Graph file exists but failed to load, using empty graph")
        else:
            print(f"[INFO] No graph file found, using empty graph (will create on first write)")
        
        # Initialize executor
        self.graph = graph
        self.executor = AIQLExecutor(contextcore=graph)
        self.executor.active_namespace = namespace
        print(f"[OK] Direct file access initialized for namespace: {namespace}")
    
    def _init_local_default(self):
        """Initialize with local default path."""
        default_path = Path("contextcore_data/namespaces/default")
        if default_path.exists():
            self.db_path = default_path
            self._init_direct_connection()
        else:
            # Create default namespace
            default_path.mkdir(parents=True, exist_ok=True)
            self.db_path = default_path
            self._init_direct_connection()
    
    def execute_query(self, query: str) -> dict:
        """Execute a query and return result."""
        if self.mode == "http":
            return self._execute_http_query(query)
        else:
            return self._execute_direct_query(query)
    
    def _execute_http_query(self, query: str) -> dict:
        """Execute query via HTTP API."""
        try:
            # Use session endpoint if available
            endpoint = f"{self.server_url}/query/grql"
            if self.session_id:
                endpoint = f"{self.server_url}/session/{self.session_id}/query/grql"
            
            payload = {
                "query": query,
                "namespace": self.active_namespace
            }
            
            response = requests.post(endpoint, json=payload, timeout=30)
            response.raise_for_status()
            
            result = response.json()
            
            # Update session info if provided
            if 'session_id' in result:
                self.session_id = result['session_id']
            if 'active_namespace' in result:
                self.active_namespace = result['active_namespace']
            
            return result
        except requests.exceptions.RequestException as e:
            return {
                "success": False,
                "error": f"HTTP request failed: {str(e)}",
                "result": None
            }
    
    def _execute_direct_query(self, query: str) -> dict:
        """Execute query via direct file access."""
        try:
            result = self.executor.execute(query)
            
            # Auto-save for direct mode
            if self.executor.active_namespace:
                namespace = self.executor.active_namespace
                graph_file = self.db_path / "graph.h5"
                if not graph_file.exists():
                    graph_file = self.db_path / "graph.json"
                
                # Save graph after successful query
                if hasattr(self.executor, 'graph_registry') and self.executor.graph_registry:
                    self.executor.graph_registry.save_graph(namespace)
                elif self.graph:
                    self.graph.save(str(graph_file))
            
            return {
                "success": True,
                "result": result,
                "namespace": self.executor.active_namespace
            }
        except Exception as e:
            import traceback
            return {
                "success": False,
                "error": str(e),
                "traceback": traceback.format_exc(),
                "result": None
            }
    
    def format_result(self, result: dict) -> str:
        """Format query result for display."""
        if not result.get('success'):
            error = result.get('error', 'Unknown error')
            traceback = result.get('traceback', '')
            return f"[ERROR] {error}\n{traceback}" if traceback else f"[ERROR] {error}"
        
        data = result.get('result', {})
        if not data:
            return "[OK] Query executed (no result)"
        
        # Format based on result type
        if isinstance(data, dict):
            if 'nodes' in data:
                nodes = data['nodes']
                edges = data.get('edges', [])
                output = f"[OK] Query executed successfully\n"
                output += f"Nodes: {len(nodes)}\n"
                if nodes:
                    output += f"Sample node: {nodes[0] if len(nodes) > 0 else 'N/A'}\n"
                if edges:
                    output += f"Edges: {len(edges)}\n"
                return output
            elif 'message' in data:
                return f"[OK] {data['message']}"
            else:
                return f"[OK] {json.dumps(data, indent=2, default=str)}"
        elif isinstance(data, list):
            return f"[OK] Query returned {len(data)} items\n{json.dumps(data[:5], indent=2, default=str)}"
        else:
            return f"[OK] {str(data)}"
    
    def interactive_mode(self):
        """Run interactive CLI mode."""
        print("=" * 80)
        print("AIContextDB Interactive CLI")
        print("=" * 80)
        print(f"Mode: {self.mode.upper()}")
        if self.mode == "http":
            print(f"Server: {self.server_url}")
        else:
            print(f"Database: {self.db_path}")
        print("\nType 'help' for commands, 'exit' to quit")
        print("=" * 80 + "\n")
        
        while True:
            try:
                # Get input
                if self.active_namespace or (hasattr(self, 'executor') and self.executor.active_namespace):
                    namespace = self.active_namespace or self.executor.active_namespace
                    prompt = f"contextcore[{namespace}]> "
                else:
                    prompt = "contextcore> "
                
                query = input(prompt).strip()
                
                if not query:
                    continue
                
                # Handle commands
                if query.lower() == 'exit' or query.lower() == 'quit':
                    print("Goodbye!")
                    break
                elif query.lower() == 'help':
                    self.show_help()
                    continue
                elif query.lower().startswith('use namespace'):
                    # Handle namespace switching
                    parts = query.split()
                    if len(parts) >= 3:
                        namespace = parts[2]
                        self.active_namespace = namespace
                        if self.mode == "direct" and hasattr(self, 'executor'):
                            self.executor.active_namespace = namespace
                        print(f"[OK] Switched to namespace: {namespace}")
                    continue
                
                # Execute query
                result = self.execute_query(query)
                print(self.format_result(result))
                print()  # Empty line for readability
                
            except KeyboardInterrupt:
                print("\n\nGoodbye!")
                break
            except EOFError:
                print("\n\nGoodbye!")
                break
            except Exception as e:
                print(f"[ERROR] Unexpected error: {e}")
                import traceback
                traceback.print_exc()
    
    def show_help(self):
        """Show help information."""
        print("\n" + "=" * 80)
        print("AIContextDB CLI Help")
        print("=" * 80)
        print("\nCommands:")
        print("  help              - Show this help message")
        print("  exit / quit       - Exit the CLI")
        print("  USE NAMESPACE <name> - Switch to a namespace")
        print("\nQuery Examples:")
        print("  CREATE NODE (name: 'Alice', type: 'Person')")
        print("  SELECT * FROM Person")
        print("  MATCH (a:Person)-[r:KNOWS]->(b:Person) RETURN a, r, b")
        print("\nFor more information, see the AIQL documentation.")
        print("=" * 80 + "\n")

def _cmd_server(args):
    """Start the API server."""
    import uvicorn
    import os
    host = args.host or os.environ.get("CONTEXTSYNAPSE_API_HOST") or os.environ.get("AICONTEXTDB_API_HOST", "0.0.0.0")
    port = args.port or int(os.environ.get("CONTEXTSYNAPSE_API_PORT") or os.environ.get("AICONTEXTDB_API_PORT", "8000"))
    workers = args.workers or int(os.environ.get("CONTEXTSYNAPSE_API_WORKERS") or os.environ.get("AICONTEXTDB_API_WORKERS", "1"))
    print(f"Starting AIContextDB on {host}:{port}")
    uvicorn.run("contextsynapse.api.api:app", host=host, port=port, workers=workers,
                reload=args.reload, log_level="info")

def _cmd_status(args):
    """Show system status."""
    import os
    from contextsynapse.core.registry import GraphRegistry
    from pathlib import Path as _P
    registry = GraphRegistry()
    graphs = registry.list_graphs()
    data_dir = _P("contextcore_data")
    storage = "—"
    if data_dir.exists():
        total = sum(f.stat().st_size for f in data_dir.rglob("*") if f.is_file())
        storage = f"{total / 1_000_000:.1f} MB" if total > 1_000_000 else f"{total / 1000:.0f} KB"
    total_nodes = sum(g.get("num_nodes", 0) for g in graphs)
    print(f"AIContextDB Status\n{'='*40}")
    print(f"  Graphs:  {len(graphs)}")
    print(f"  Nodes:   {total_nodes}")
    print(f"  Storage: {storage}")
    for key, name in [("OPENAI_API_KEY", "OpenAI"), ("GROQ_API_KEY", "Groq"), ("ANTHROPIC_API_KEY", "Anthropic")]:
        print(f"  {name + ':':12s} {'configured' if os.environ.get(key) else 'not set'}")

def _cmd_graphs(args):
    """List all graphs."""
    from contextsynapse.core.registry import GraphRegistry
    registry = GraphRegistry()
    graphs = registry.list_graphs()
    if args.json_out:
        print(json.dumps(graphs, indent=2, default=str))
    else:
        print(f"{'Name':<40} {'Nodes':>8} {'Edges':>8}")
        print("-" * 60)
        for g in graphs:
            name = g.get("name", "?")
            print(f"{name:<40} {g.get('num_nodes', 0):>8} {g.get('num_edges', 0):>8}")

def _cmd_export(args):
    """Export a graph."""
    from contextsynapse.core.registry import GraphRegistry
    registry = GraphRegistry()
    graph = registry.get_graph(args.graph, load_if_missing=True)
    if not graph:
        print(f"Error: Graph '{args.graph}' not found"); sys.exit(1)
    nodes = [{"id": n.id, "label": n.label, "properties": dict(n.properties)} for n in graph.get_all_nodes()]
    edges = [{"source": e.source if hasattr(e, "source") else e.get("source",""),
              "target": e.target if hasattr(e, "target") else e.get("target",""),
              "label": e.label if hasattr(e, "label") else e.get("label","")} for e in graph.get_all_edges()]
    data = {"graph": args.graph, "nodes": nodes, "edges": edges}
    if args.output:
        with open(args.output, "w") as f: json.dump(data, f, indent=2, default=str)
        print(f"Exported {len(nodes)} nodes, {len(edges)} edges → {args.output}")
    else:
        print(json.dumps(data, indent=2, default=str))

def _cmd_mcp(args):
    """Start MCP server."""
    sys.argv = ["mcp"]
    if args.api_key: sys.argv.extend(["--api-key", args.api_key])
    if args.session: sys.argv.extend(["--session", args.session])
    from contextsynapse.mcp.server import main as mcp_main
    mcp_main()


def _cmd_create_vertical(args):
    """Create a new vertical plugin project."""
    from contextsynapse.cli.scaffold import scaffold_vertical
    try:
        project_dir = scaffold_vertical(
            name=args.name,
            output_dir=args.output,
            author=args.author,
            description=args.description,
        )
        print(f"\nCreated vertical '{args.name}' at {project_dir}\n")
        print("Next steps:")
        print(f"  1. cd {project_dir}")
        print(f"  2. Edit schemas.py — define your node/edge types")
        print(f"  3. Edit sensors/ — add your data feeds")
        print(f"  4. Edit api/routes.py — add your endpoints")
        print(f"  5. pip install -e . && contextcore serve")
        print(f"  6. Visit http://localhost:8000/plugins/ to verify")
    except FileExistsError as e:
        print(f"Error: {e}")
        sys.exit(1)


def main():
    """Main entry point."""
    import argparse

    parser = argparse.ArgumentParser(
        prog="contextsynapse",
        description="AIContextDB — Shared context platform for AI agents",
    )
    sub = parser.add_subparsers(dest="subcommand")

    # Subcommands
    p = sub.add_parser("server", help="Start API server")
    p.add_argument("--host", default=None); p.add_argument("--port", type=int, default=None)
    p.add_argument("--workers", type=int, default=None); p.add_argument("--reload", action="store_true")

    sub.add_parser("status", help="System status")

    p = sub.add_parser("graphs", help="List graphs")
    p.add_argument("--json", dest="json_out", action="store_true")

    p = sub.add_parser("export", help="Export graph as JSON")
    p.add_argument("graph"); p.add_argument("-o", "--output")

    p = sub.add_parser("mcp", help="Start MCP server")
    p.add_argument("--api-key", default=None); p.add_argument("--session", default=None)

    p = sub.add_parser("create-vertical", help="Create a new vertical plugin project")
    p.add_argument("name", help="Vertical name (e.g. legal, healthcare, logistics)")
    p.add_argument("--author", default="ContextCore Developer", help="Author name")
    p.add_argument("--description", default="", help="Plugin description")
    p.add_argument("--output", default=".", help="Output directory")

    # Legacy flags (backward compat)
    parser.add_argument("--local", type=str,
                       help="Local database path (direct file access)")
    parser.add_argument("--remote", type=str, default="http://localhost:8000",
                       help="Remote server URL")
    parser.add_argument("--server", type=str, help=argparse.SUPPRESS)
    parser.add_argument("--tinker", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--query", help="Execute a single query and exit")
    parser.add_argument("--file", help="Execute queries from file")

    args = parser.parse_args()

    # Handle subcommands
    if args.subcommand == "server": return _cmd_server(args)
    if args.subcommand == "status": return _cmd_status(args)
    if args.subcommand == "graphs": return _cmd_graphs(args)
    if args.subcommand == "export": return _cmd_export(args)
    if args.subcommand == "mcp": return _cmd_mcp(args)
    if args.subcommand == "create-vertical": return _cmd_create_vertical(args)

    # Determine connection string
    if args.local:
        connection_string = args.local
    elif args.server:
        connection_string = args.server  # Legacy support
    elif args.remote:
        connection_string = args.remote
    else:
        connection_string = None  # Auto-detect
    
    cli = AIContextDBCLI(connection_string=connection_string, tinker_mode=args.tinker)
    
    if args.query:
        # Single query mode
        result = cli.execute_query(args.query)
        print(json.dumps(result, indent=2, default=str))
        sys.exit(0 if result.get('success') else 1)
    elif args.file:
        # File mode
        with open(args.file, 'r') as f:
            queries = f.read().split(';')
            for query in queries:
                query = query.strip()
                if query:
                    print(f"\nExecuting: {query}")
                    result = cli.execute_query(query + ';')
                    print(cli.format_result(result))
    else:
        # Interactive mode
        cli.interactive_mode()

if __name__ == "__main__":
    main()


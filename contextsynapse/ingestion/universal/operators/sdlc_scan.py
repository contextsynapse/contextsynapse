"""SDLCScanOperator — orchestrates SDLC repo scanning via modular scanners.

Two modes:
  Quick Scan (no clone) — GitHub API only, ~5 seconds
  Full Scan  (clone)    — git clone + file scanning + API, ~30-120 seconds

Delegates to scanners/ package:
  scanners.repo          — README, source, tests, docs, API routes (Full only)
  scanners.github        — GitHub issues via .git/config (Full only)
  scanners.github_api    — GitHub API: README, issues, PRs, contributors, metadata (Quick)
  scanners.git           — git history, repo metadata (Full only)
  scanners.edges         — edge inference (7 types)
  scanners.llm_enrichment — LLM-powered code summaries (Full only, optional)
  scanners.jira          — Jira ticket import (both modes)
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any, Dict, List, TYPE_CHECKING

from .base import StageOperator

# Re-export everything from scanners for backward compatibility
from .scanners import (
    scan_readme, scan_source_modules, scan_tests, scan_docs, scan_api_routes,
    scan_github_issues_full, scan_git_history, scan_repo_metadata,
    scan_with_llm, scan_jira_issues, infer_edges, quick_scan,
    _safe_id, _issue_to_node, _extract_github_owner_repo,
)

if TYPE_CHECKING:
    from ..ingest_content import Chunk
    from ..stage_executor import GraphContext

logger = logging.getLogger(__name__)


class SDLCScanOperator(StageOperator):
    """Scan a repository into typed SDLC graph nodes.

    Modes:
        quick (default): GitHub API only — no clone, ~5 seconds
        full:            git clone + file scan + API — ~30-120 seconds

    Config (from pipeline_params):
        scan_mode: "quick" or "full" (default: "quick")
        skip_llm:  skip LLM enrichment in full mode (default: True)
    """

    name = "sdlc_scan"

    def __init__(self, config: Dict[str, Any] = None):
        self.config = config or {}

    def process(self, chunks: List["Chunk"], graph_ctx: "GraphContext") -> List["Chunk"]:
        cfg = self.config
        for chunk in chunks:
            if chunk.metadata.get("pipeline_params"):
                cfg = {**cfg, **chunk.metadata["pipeline_params"]}
                break

        # Get source URL or repo path
        source_url = ""
        repo_path_str = ""
        for chunk in chunks:
            source_url = chunk.metadata.get("source_url", "")
            repo_path_str = chunk.metadata.get("repo_path", "")
            if source_url or repo_path_str:
                break

        # Determine scan mode: auto-detect from input if not explicitly set
        scan_mode = cfg.get("scan_mode", "")
        if not scan_mode:
            if repo_path_str and os.path.isdir(repo_path_str):
                scan_mode = "full"  # local directory → full scan
            elif source_url and source_url.startswith("https://"):
                scan_mode = "quick"  # URL → quick scan (no clone by default)
            else:
                scan_mode = "full"  # fallback

        if scan_mode == "quick":
            return self._quick_scan(source_url or repo_path_str, graph_ctx, cfg)
        else:
            return self._full_scan(repo_path_str, source_url, chunks, graph_ctx, cfg)

    def _write_nodes_and_catch_orphans(
        self, all_nodes: List[Dict], edges: List[Dict],
        graph_ctx: "GraphContext", project_name: str,
    ) -> None:
        """Write nodes + edges, then connect orphans to a root Project node.

        Most nodes connect to each other via inferred edges.
        Only nodes with NO connections get linked to the root as a safety net.
        """
        # Write all nodes
        for node_def in all_nodes:
            graph_ctx.add_node(
                label=node_def["label"],
                properties=dict(node_def.get("properties", {})),
                node_id=node_def["id"],
            )

        # Write all edges
        for edge_def in edges:
            graph_ctx.add_edge(
                source_id=edge_def["source"],
                target_id=edge_def["target"],
                label=edge_def["label"],
            )

        # Find orphans — nodes with no edges at all
        connected = set()
        for e in edges:
            connected.add(e["source"])
            connected.add(e["target"])

        orphans = [n for n in all_nodes if n["id"] not in connected]

        if orphans:
            # Create root node and link orphans to it
            root_id = f"project:{_safe_id(project_name)}"
            graph_ctx.add_node(
                label="Project",
                properties={"name": project_name, "version": 1},
                node_id=root_id,
            )
            for orphan in orphans:
                graph_ctx.add_edge(
                    source_id=root_id,
                    target_id=orphan["id"],
                    label="HAS_NODE",
                )
            logger.info("sdlc_scan: %d orphans connected to root node %s", len(orphans), root_id)

    def _quick_scan(
        self, url: str, graph_ctx: "GraphContext", cfg: Dict,
    ) -> List["Chunk"]:
        """Quick Scan — GitHub API only, no clone."""
        if not url:
            logger.warning("sdlc_scan[quick]: no URL provided — skipping")
            return []

        logger.info("sdlc_scan[quick]: scanning %s via API (no clone)", url)

        all_nodes = quick_scan(url)
        all_nodes += scan_jira_issues()

        # Infer cross-connections
        edges = infer_edges(all_nodes)

        # Write nodes + edges, orphans fall back to root
        from .scanners.github_api import _parse_owner_repo
        project_name = _parse_owner_repo(url) or url.split("/")[-1] or "project"
        self._write_nodes_and_catch_orphans(all_nodes, edges, graph_ctx, project_name)

        logger.info("sdlc_scan[quick]: %d nodes, %d edges", len(all_nodes), len(edges))
        return []

    def _full_scan(
        self, repo_path_str: str, source_url: str,
        chunks: List["Chunk"], graph_ctx: "GraphContext", cfg: Dict,
    ) -> List["Chunk"]:
        """Full Scan — clone + file scanning + API."""
        if not repo_path_str:
            logger.warning("sdlc_scan[full]: no repo_path — skipping")
            return chunks

        repo_path = Path(repo_path_str)
        if not repo_path.is_dir():
            logger.warning("sdlc_scan[full]: %s is not a directory — skipping", repo_path)
            return chunks

        logger.info("sdlc_scan[full]: scanning %s (clone mode)", repo_path)

        # File-based scanners
        all_nodes = (
            scan_readme(repo_path)
            + scan_source_modules(repo_path)
            + scan_tests(repo_path)
            + scan_docs(repo_path)
            + scan_api_routes(repo_path)
        )

        # LLM enrichment (optional)
        skip_llm = cfg.get("skip_llm", False) or os.environ.get("SDLC_SKIP_LLM")
        if not skip_llm:
            code_nodes = [n for n in all_nodes if n["label"] == "CodeModule"]
            non_code = [n for n in all_nodes if n["label"] != "CodeModule"]
            all_nodes = non_code + scan_with_llm(repo_path, code_nodes)
        else:
            logger.info("sdlc_scan[full]: LLM enrichment skipped")

        # API-based scanners
        all_nodes += scan_github_issues_full(repo_path, source_url=source_url or repo_path_str)
        all_nodes += scan_jira_issues()
        all_nodes += scan_repo_metadata(repo_path)

        # Git history
        history_nodes, history_edges = scan_git_history(repo_path)
        all_nodes += history_nodes

        # Infer cross-connections + history edges
        edges = infer_edges(all_nodes)
        edges += history_edges

        # Write nodes + edges, orphans fall back to root
        project_name = repo_path.name or "project"
        self._write_nodes_and_catch_orphans(all_nodes, edges, graph_ctx, project_name)

        logger.info("sdlc_scan[full]: %d nodes, %d edges from %s", len(all_nodes), len(edges), repo_path)
        return chunks

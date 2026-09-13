"""Integration tests for graph save/load/delete lifecycle.

Tests the full registry flow: create → save → reload → delete → no ghost.
This is the layer that was previously untested, causing persistent sync bugs.
"""

import os
import shutil
import tempfile
import uuid

import pytest

from contextcore.core.registry import GraphRegistry
from contextcore.core.hybrid_graph_storage import GraphNode


@pytest.fixture
def storage_dir(tmp_path, monkeypatch):
    """Isolated storage directory for each test.

    Forces CSR (disk) backend so these file-persistence tests are not
    affected by a running Redis instance that changes the storage backend.
    """
    monkeypatch.setenv("AICONTEXTDB_GRAPH_BACKEND", "csr")
    d = tmp_path / "contextcore_data"
    d.mkdir()
    return str(d)


@pytest.fixture
def gname():
    """Unique graph name per test — prevents Redis key collisions across runs."""
    return f"test_lifecycle_{uuid.uuid4().hex[:8]}"


@pytest.fixture
def registry(storage_dir):
    return GraphRegistry(storage_dir=storage_dir)


class TestGraphLifecycle:
    """End-to-end create → save → reload → delete → no ghost."""

    def test_create_and_list(self, registry, gname):
        registry.create_graph(gname)
        names = [g["name"] for g in registry.list_graphs()]
        assert gname in names

    def test_save_and_reload(self, storage_dir, gname):
        # Create and save (write_through=True to bypass async buffer)
        reg1 = GraphRegistry(storage_dir=storage_dir)
        graph = reg1.create_graph(gname)
        graph.add_node(GraphNode(id="n1", label="Person", properties={"name": "Alice"}), write_through=True)
        reg1.save_graph(gname, create_checkpoint=False)

        # Simulate restart — new registry reads from disk
        reg2 = GraphRegistry(storage_dir=storage_dir)
        graph2 = reg2.get_graph(gname)
        assert graph2 is not None
        node = graph2.get_node("n1")
        assert node is not None
        assert node.properties["name"] == "Alice"

    def test_delete_removes_from_metadata(self, registry, gname):
        registry.create_graph(gname)
        registry.save_graph(gname, create_checkpoint=False)
        registry.delete_graph(gname)

        assert registry.get_graph(gname, load_if_missing=False) is None
        names = [g["name"] for g in registry.list_graphs()]
        assert gname not in names

    def test_no_ghost_after_delete_and_restart(self, storage_dir, gname):
        # Create, save, delete
        reg1 = GraphRegistry(storage_dir=storage_dir)
        reg1.create_graph(gname)
        reg1.save_graph(gname, create_checkpoint=False)
        reg1.delete_graph(gname)

        # Simulate restart
        reg2 = GraphRegistry(storage_dir=storage_dir)
        assert reg2.get_graph(gname, load_if_missing=True) is None
        names = [g["name"] for g in reg2.list_graphs()]
        assert gname not in names

    def test_metadata_pruned_when_files_deleted_externally(self, storage_dir, gname):
        reg1 = GraphRegistry(storage_dir=storage_dir)
        reg1.create_graph(gname)
        reg1.save_graph(gname, create_checkpoint=False)

        # Manually delete graph files (simulating external cleanup)
        ns_dir = os.path.join(storage_dir, "namespaces", gname)
        if os.path.exists(ns_dir):
            shutil.rmtree(ns_dir)

        # Restart — stale entry should be pruned
        reg2 = GraphRegistry(storage_dir=storage_dir)
        names = [g["name"] for g in reg2.list_graphs()]
        assert gname not in names

    def test_get_graph_for_request_uses_cached(self, registry, gname):
        graph = registry.create_graph(gname)
        graph.add_node(GraphNode(id="n1", label="Test", properties={}))

        # get_graph_for_request should return the SAME cached instance
        req_graph = registry.get_graph_for_request(gname)
        assert req_graph.get_node("n1") is not None

    def test_multiple_graphs_independent(self, registry, gname):
        na = gname + "_a"
        nb = gname + "_b"
        registry.create_graph(na)
        registry.create_graph(nb)
        ga = registry.get_graph(na)
        gb = registry.get_graph(nb)
        ga.add_node(GraphNode(id="na", label="A", properties={}))
        gb.add_node(GraphNode(id="nb", label="B", properties={}))

        assert ga.get_node("na") is not None
        assert ga.get_node("nb") is None
        assert gb.get_node("nb") is not None
        assert gb.get_node("na") is None

    def test_save_updates_metadata_counts(self, registry, gname):
        graph = registry.create_graph(gname)
        graph.add_node(GraphNode(id="n1", label="Person", properties={}), write_through=True)
        graph.add_node(GraphNode(id="n2", label="Person", properties={}), write_through=True)
        registry.save_graph(gname, create_checkpoint=False)

        info = [g for g in registry.list_graphs() if g["name"] == gname][0]
        assert info["num_nodes"] >= 2

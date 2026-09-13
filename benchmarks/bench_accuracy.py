"""Accuracy benchmarks — measures data integrity, dedup, and entity resolution quality."""
import os
import sys
import time
from typing import Dict, Any, List

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("AICONTEXTDB_ADMIN_KEY", "bench-key")

from contextsynapse.core.hybrid_graph_storage import AIContextDB
from contextsynapse.core.graph_structures import GraphNode, GraphEdge
from contextsynapse.context.hub import ContextHub


def bench_node_data_integrity(count: int = 10000) -> Dict[str, Any]:
    """Verify that stored data matches inserted data exactly."""
    db = AIContextDB(name="integrity_bench")

    test_data = {}
    for i in range(count):
        props = {
            "name": f"Test Node {i}",
            "value": i * 3.14159,
            "flag": i % 2 == 0,
            "category": f"cat_{i % 7}",
            "description": f"A test node with index {i} and various properties" * 3,
        }
        nid = f"int_{i}"
        test_data[nid] = {"label": "IntegrityTest", "properties": props}
        db.add_node(GraphNode(id=nid, label="IntegrityTest", properties=props))

    # Verify
    mismatches = 0
    missing = 0
    checked = 0
    mismatch_details = []

    for nid, expected in test_data.items():
        checked += 1
        node = db.get_node(nid)
        if node is None:
            missing += 1
            continue

        node_props = node.properties if hasattr(node, 'properties') else node.get('properties', {})
        for key, expected_val in expected["properties"].items():
            actual_val = node_props.get(key)
            if actual_val != expected_val:
                mismatches += 1
                if len(mismatch_details) < 5:
                    mismatch_details.append({
                        "node_id": nid,
                        "property": key,
                        "expected": expected_val,
                        "actual": actual_val,
                    })

    return {
        "operation": "data_integrity",
        "nodes_checked": checked,
        "nodes_missing": missing,
        "property_mismatches": mismatches,
        "integrity_score": 1.0 - (mismatches + missing * 10) / (checked * 5) if checked > 0 else 0,
        "sample_mismatches": mismatch_details,
    }


def bench_edge_integrity(count: int = 5000) -> Dict[str, Any]:
    """Verify edge data integrity — are edges retrievable and correct?"""
    db = AIContextDB(name="edge_integrity")

    # Create nodes
    for i in range(count):
        db.add_node(GraphNode(id=f"en_{i}", label="EdgeNode", properties={"idx": i}))

    # Create edges and track expected
    import random
    random.seed(42)
    expected_edges = []
    for i in range(count * 2):
        src = f"en_{random.randint(0, count - 1)}"
        tgt = f"en_{random.randint(0, count - 1)}"
        if src == tgt:
            continue
        label = random.choice(["A", "B", "C"])
        db.add_edge(GraphEdge(id=f"ae_{i}", source=src, target=tgt, label=label, properties={"idx": i}))
        expected_edges.append((src, tgt, label))

    # Verify via neighbor traversal
    edge_found = 0
    edge_missing = 0
    sample_size = min(500, count)
    for i in range(sample_size):
        nid = f"en_{i}"
        neighbors = db.get_neighbors(nid)
        neighbor_ids = set()
        for n in neighbors:
            if isinstance(n, tuple):
                neighbor_ids.add(n[0])
            elif hasattr(n, 'id'):
                neighbor_ids.add(n.id)

        # Check expected outgoing edges for this node
        expected_targets = {tgt for src, tgt, _ in expected_edges if src == nid}
        for tgt in expected_targets:
            if tgt in neighbor_ids:
                edge_found += 1
            else:
                edge_missing += 1

    return {
        "operation": "edge_integrity",
        "total_edges_created": len(expected_edges),
        "edges_verified": edge_found + edge_missing,
        "edges_found": edge_found,
        "edges_missing": edge_missing,
        "integrity_score": edge_found / (edge_found + edge_missing) if (edge_found + edge_missing) > 0 else 1.0,
    }


def bench_duplicate_handling() -> Dict[str, Any]:
    """Test how the system handles duplicate node insertions."""
    db = AIContextDB(name="dedup_bench")

    # Insert same node multiple times with different properties
    for i in range(100):
        db.add_node(GraphNode(
            id="dup_node",
            label="TestNode",
            properties={"version": i, "name": f"Version {i}"},
        ))

    node = db.get_node("dup_node")
    node_props = node.properties if hasattr(node, 'properties') else node.get('properties', {})

    all_nodes = db.get_all_nodes()
    dup_count = sum(1 for n in all_nodes
                    if (n.id if hasattr(n, 'id') else n.get('id')) == "dup_node")

    return {
        "operation": "duplicate_handling",
        "insertions": 100,
        "final_node_count_for_id": dup_count,
        "last_version": node_props.get("version"),
        "behavior": "last_write_wins" if dup_count == 1 else "allows_duplicates",
        "correct": dup_count == 1,  # Expected: upsert semantics
    }


def bench_context_hub_completeness() -> Dict[str, Any]:
    """Test that ContextHub correctly includes all relevant data."""
    db = AIContextDB(name="hub_bench")

    # Build a small but complete graph
    doc_count = 20
    for i in range(doc_count):
        db.add_node(GraphNode(
            id=f"cdoc_{i}",
            label="Document",
            properties={"title": f"Doc {i}", "content": f"Content of document {i}"},
        ))

    for i in range(10):
        db.add_node(GraphNode(
            id=f"cent_{i}",
            label="Entity",
            properties={"name": f"Entity {i}", "type": "Company"},
        ))
        db.add_edge(GraphEdge(
            id=f"ce_{i}", source=f"cdoc_{i}", target=f"cent_{i}",
            label="MENTIONS", properties={},
        ))

    # Build context
    hub = ContextHub(system_prompt="Test", max_tokens=16000)
    all_docs = db.get_all_nodes(label="Document")
    hub.add_nodes(all_docs)

    messages = hub.to_messages()
    prompt_text = hub.to_prompt()

    # Check completeness
    docs_in_context = sum(1 for i in range(doc_count) if f"Doc {i}" in prompt_text)
    token_estimate = hub.estimate_tokens()

    return {
        "operation": "context_completeness",
        "docs_inserted": doc_count,
        "docs_in_context": docs_in_context,
        "completeness_ratio": docs_in_context / doc_count,
        "message_count": len(messages),
        "estimated_tokens": token_estimate,
        "within_budget": hub.is_within_limit(),
    }


def run_all_benchmarks():
    """Run the full accuracy benchmark suite."""
    print("=" * 70)
    print("AIContextDB Accuracy Benchmark Suite")
    print("=" * 70)

    # Node data integrity
    print(f"\n{'─' * 70}")
    r = bench_node_data_integrity(10000)
    print(f"Node Data Integrity (10K nodes):")
    print(f"  Score:           {r['integrity_score']:.4f}")
    print(f"  Missing:         {r['nodes_missing']}")
    print(f"  Mismatches:      {r['property_mismatches']}")
    if r['sample_mismatches']:
        for m in r['sample_mismatches'][:3]:
            print(f"    {m['node_id']}.{m['property']}: expected={m['expected']}, got={m['actual']}")

    # Edge integrity
    print(f"\n{'─' * 70}")
    r = bench_edge_integrity(5000)
    print(f"Edge Data Integrity (5K nodes, {r['total_edges_created']} edges):")
    print(f"  Score:           {r['integrity_score']:.4f}")
    print(f"  Found:           {r['edges_found']}")
    print(f"  Missing:         {r['edges_missing']}")

    # Duplicate handling
    print(f"\n{'─' * 70}")
    r = bench_duplicate_handling()
    print(f"Duplicate Handling:")
    print(f"  Behavior:        {r['behavior']}")
    print(f"  Correct:         {r['correct']}")
    print(f"  Final count:     {r['final_node_count_for_id']} (expected: 1)")

    # Context completeness
    print(f"\n{'─' * 70}")
    r = bench_context_hub_completeness()
    print(f"Context Hub Completeness:")
    print(f"  Completeness:    {r['completeness_ratio']:.2%}")
    print(f"  Docs inserted:   {r['docs_inserted']}")
    print(f"  Docs in context: {r['docs_in_context']}")
    print(f"  Tokens:          {r['estimated_tokens']}")
    print(f"  Within budget:   {r['within_budget']}")

    print(f"\n{'=' * 70}")
    print("ACCURACY SUMMARY")
    print(f"{'=' * 70}")


if __name__ == "__main__":
    run_all_benchmarks()

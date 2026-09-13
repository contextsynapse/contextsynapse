"""
Accuracy Deep-Dive: Do all 3 architecture patterns return identical results?

Tests:
  1. RAG query — same passages in same order?
  2. Content integrity — text byte-for-byte identical?
  3. Relationship integrity — same neighbors?
  4. Price data integrity — same values?
  5. Fact integrity — statements match original?
"""
import hashlib
import os
import random
import shutil
import sys
import tempfile

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("AICONTEXTDB_ADMIN_KEY", "k")

import warnings
warnings.filterwarnings("ignore")

from benchmarks.poc_architecture import (
    PatternA_FatGraph,
    PatternB_ThinGraph_DocStore,
    PatternC_ThinGraph_Columnar,
    _generate_test_data,
    COMPANIES,
)


def main():
    print("=" * 70)
    print("ACCURACY DEEP-DIVE: Do all patterns return identical results?")
    print("=" * 70)

    data = _generate_test_data(500, passages_per_doc=10)
    print(
        f"Data: {len(data['passages']):,} passages, {len(data['entities'])} entities, "
        f"{len(data['facts']):,} facts, {len(data['prices']):,} prices"
    )

    dirs = [tempfile.mkdtemp() for _ in range(3)]
    patterns = [
        PatternA_FatGraph(dirs[0]),
        PatternB_ThinGraph_DocStore(dirs[1]),
        PatternC_ThinGraph_Columnar(dirs[2]),
    ]
    for p in patterns:
        p.ingest(data)

    # ── TEST 1: RAG query result identity ──
    print()
    print("-" * 70)
    print("TEST 1: RAG Query -- Same passages in same order?")
    print("-" * 70)

    rag_pass = 0
    rag_fail = 0
    np.random.seed(42)
    for qi in range(10):
        q_vec = np.random.randn(128).astype(np.float32)
        q_vec /= np.linalg.norm(q_vec)
        results = [p.query_rag(q_vec, top_k=5) for p in patterns]

        ids = [[r["id"] for r in res] for res in results]
        scores = [[round(r["score"], 6) for r in res] for res in results]
        texts = [[hashlib.md5(r["text"].encode()).hexdigest() for r in res] for res in results]
        ents = [[sorted(r["entities"]) for r in res] for res in results]

        id_ok = ids[0] == ids[1] == ids[2]
        score_ok = scores[0] == scores[1] == scores[2]
        text_ok = texts[0] == texts[1] == texts[2]
        ent_ok = ents[0] == ents[1] == ents[2]

        if all([id_ok, score_ok, text_ok, ent_ok]):
            rag_pass += 1
            print(f"  Query {qi+1}: PASS (IDs, scores, text, entities all match)")
        else:
            rag_fail += 1
            print(
                f"  Query {qi+1}: FAIL  IDs={'Y' if id_ok else 'N'} "
                f"Scores={'Y' if score_ok else 'N'} "
                f"Text={'Y' if text_ok else 'N'} "
                f"Entities={'Y' if ent_ok else 'N'}"
            )
            if not text_ok:
                for i in range(min(3, len(results[0]))):
                    la = len(results[0][i]["text"])
                    lb = len(results[1][i]["text"])
                    lc = len(results[2][i]["text"])
                    print(f"    {results[0][i]['id']}: A={la}ch B={lb}ch C={lc}ch")

    # ── TEST 2: Content integrity ──
    print()
    print("-" * 70)
    print("TEST 2: Content Integrity -- Random passage text byte-identical?")
    print("-" * 70)

    random.seed(42)
    sample_ids = random.sample([p["id"] for p in data["passages"]], 100)
    text_match = 0
    text_mismatch = 0

    from tinydb import Query
    Q = Query()

    for pid in sample_ids:
        # A: graph node
        node_a = patterns[0].graph.get_node(pid)
        text_a = node_a.properties.get("text", "") if node_a else ""

        # B: TinyDB
        docs_b = patterns[1].passage_db.search(Q.id == pid)
        text_b = docs_b[0]["text"] if docs_b else ""

        # C: DuckDB
        row_c = patterns[2].db.execute("SELECT text FROM passages WHERE id = ?", [pid]).fetchone()
        text_c = row_c[0] if row_c else ""

        if text_a == text_b == text_c and len(text_a) > 0:
            text_match += 1
        else:
            text_mismatch += 1
            if text_mismatch <= 3:
                print(f"  MISMATCH {pid}: A={len(text_a)}ch B={len(text_b)}ch C={len(text_c)}ch")

    print(f"  Checked: {len(sample_ids)} | Match: {text_match} | Mismatch: {text_mismatch}")
    print(f"  Score: {text_match / len(sample_ids):.4f}")

    # ── TEST 3: Relationship integrity ──
    print()
    print("-" * 70)
    print("TEST 3: Relationships -- Same neighbors for same entity?")
    print("-" * 70)

    rel_match = 0
    rel_mismatch = 0
    for company in COMPANIES[:30]:
        eid = f"ent_{company}"
        na = sorted(patterns[0].query_neighbors(eid))
        nb = sorted(patterns[1].query_neighbors(eid))
        nc = sorted(patterns[2].query_neighbors(eid))
        if na == nb == nc:
            rel_match += 1
        else:
            rel_mismatch += 1
            print(f"  MISMATCH {eid}: A={len(na)} B={len(nb)} C={len(nc)}")

    print(f"  Checked: 30 | Match: {rel_match} | Mismatch: {rel_mismatch}")
    print(f"  Score: {rel_match / 30:.4f}")

    # ── TEST 4: Price integrity ──
    print()
    print("-" * 70)
    print("TEST 4: Prices -- Same values for same ticker?")
    print("-" * 70)

    price_match = 0
    price_mismatch = 0
    for company in COMPANIES[:20]:
        pa = patterns[0].query_prices(company, 30)
        pb = patterns[1].query_prices(company, 30)
        pc = patterns[2].query_prices(company, 30)

        closes_a = [round(p["close"], 2) for p in pa]
        closes_b = [round(p["close"], 2) for p in pb]
        closes_c = [round(p["close"], 2) for p in pc]

        if closes_a == closes_b == closes_c and len(closes_a) == 30:
            price_match += 1
        else:
            price_mismatch += 1
            print(
                f"  MISMATCH {company}: A={len(closes_a)} B={len(closes_b)} C={len(closes_c)} rows "
                f"AB={closes_a == closes_b} AC={closes_a == closes_c}"
            )

    print(f"  Checked: 20 | Match: {price_match} | Mismatch: {price_mismatch}")
    print(f"  Score: {price_match / 20:.4f}")

    # ── TEST 5: Fact integrity ──
    print()
    print("-" * 70)
    print("TEST 5: Facts -- Statements match original data?")
    print("-" * 70)

    random.seed(99)
    sample_facts = random.sample(data["facts"], min(50, len(data["facts"])))
    fact_match = 0
    fact_mismatch = 0

    for fact in sample_facts:
        fid = fact["id"]
        original = fact["statement"]

        node_a = patterns[0].graph.get_node(fid)
        stmt_a = node_a.properties.get("statement", "") if node_a else ""

        docs_b = patterns[1].fact_db.search(Q.id == fid)
        stmt_b = docs_b[0]["statement"] if docs_b else ""

        row_c = patterns[2].db.execute("SELECT statement FROM facts WHERE id = ?", [fid]).fetchone()
        stmt_c = row_c[0] if row_c else ""

        if stmt_a == stmt_b == stmt_c == original:
            fact_match += 1
        else:
            fact_mismatch += 1
            if fact_mismatch <= 3:
                print(f"  MISMATCH {fid}:")
                print(f"    orig: {original[:60]}")
                print(f"    A:    {stmt_a[:60]}")
                print(f"    B:    {stmt_b[:60]}")
                print(f"    C:    {stmt_c[:60]}")

    print(f"  Checked: {len(sample_facts)} | Match: {fact_match} | Mismatch: {fact_mismatch}")
    print(f"  Score: {fact_match / len(sample_facts):.4f}")

    # ── Cleanup ──
    for d in dirs:
        shutil.rmtree(d, ignore_errors=True)

    # ── Summary ──
    print()
    print("=" * 70)
    print("ACCURACY SUMMARY")
    print("=" * 70)
    print(f"  {'Test':<30} {'A vs B vs C':>15} {'Score':>8}")
    print(f"  {'-'*30} {'-'*15} {'-'*8}")
    print(f"  {'RAG query identity':<30} {f'{rag_pass}/10':>15} {rag_pass/10:.4f}")
    print(f"  {'Content integrity':<30} {f'{text_match}/100':>15} {text_match/100:.4f}")
    print(f"  {'Relationship integrity':<30} {f'{rel_match}/30':>15} {rel_match/30:.4f}")
    print(f"  {'Price data integrity':<30} {f'{price_match}/20':>15} {price_match/20:.4f}")
    print(f"  {'Fact integrity':<30} {f'{fact_match}/{len(sample_facts)}':>15} {fact_match/len(sample_facts):.4f}")

    total = rag_pass + text_match + rel_match + price_match + fact_match
    total_max = 10 + 100 + 30 + 20 + len(sample_facts)
    print(f"  {'-'*30} {'-'*15} {'-'*8}")
    print(f"  {'OVERALL':<30} {f'{total}/{total_max}':>15} {total/total_max:.4f}")

    all_pass = rag_fail == 0 and text_mismatch == 0 and rel_mismatch == 0 and price_mismatch == 0 and fact_mismatch == 0
    print(f"\n  Verdict: {'ALL THREE PATTERNS ARE IDENTICAL' if all_pass else 'DIFFERENCES FOUND'}")


if __name__ == "__main__":
    main()

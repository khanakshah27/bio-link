"""
Measure BioLink's LIVE behaviour against the real services. Run this from a
machine with normal internet access (it will not work from a sandbox that
blocks the databases):

    cd backend && python ../scripts/live_check.py [--json live_results.json]

It reports, per database: how many lookups were live / cached / unavailable,
median and max latency, and a sample of what came back; then compares the old
one-at-a-time lookup order with the concurrent one. If GEMINI_API_KEY is set
it also runs one relation-extraction batch and one grounded question.

Nothing here is a benchmark of biological accuracy; it answers "do the seven
clients still parse the real responses, and how long do they take?".
"""
import argparse
import json
import os
import statistics
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))
os.environ.setdefault("DATABASE_URL", "sqlite://")

from app.services import db_integrations as dbi  # noqa: E402

GENES = ["TP53", "BRCA1", "BRCA2", "MDM2", "EGFR", "KRAS", "PTEN", "ATM", "CFTR", "APOE"]
DISEASES = ["breast cancer", "melanoma", "cystic fibrosis", "type 2 diabetes mellitus"]
PATHWAYS = ["apoptosis", "cell cycle"]
SNPS = ["rs1799950"]

ap = argparse.ArgumentParser()
ap.add_argument("--json")
args = ap.parse_args()

entities = [(g, "gene") for g in GENES] + [(d, "disease") for d in DISEASES] + \
           [(p, "pathway") for p in PATHWAYS] + [(s, "snp") for s in SNPS]

# per-client timing, one at a time
lat = defaultdict(list)
status = defaultdict(Counter)
samples = {}
for text, etype in entities:
    for source, fn in dbi.QUERIES_BY_TYPE[etype]:
        t0 = time.perf_counter()
        res = fn(text)
        lat[source].append(time.perf_counter() - t0)
        status[source][res["status"]] += 1
        if res["status"] == "ok":
            samples.setdefault(source, {"query": text, "data": res["data"]})

print(f"{'database':12s} {'live':>5s} {'cached':>7s} {'n/a':>5s} {'median s':>9s} {'max s':>7s}")
report = {}
for source in sorted(lat):
    c = status[source]
    report[source] = {"live": c["ok"], "cached": c["offline_fallback"], "unavailable": c["unavailable"],
                      "median_s": round(statistics.median(lat[source]), 2), "max_s": round(max(lat[source]), 2),
                      "sample": samples.get(source)}
    print(f"{source:12s} {c['ok']:5d} {c['offline_fallback']:7d} {c['unavailable']:5d} "
          f"{report[source]['median_s']:9.2f} {report[source]['max_s']:7.2f}")

# sequential vs concurrent for a realistic paper-sized entity list
t0 = time.perf_counter()
for text, etype in entities:
    for _source, fn in dbi.QUERIES_BY_TYPE[etype]:
        fn(text)
seq = time.perf_counter() - t0
t0 = time.perf_counter()
dbi.query_all_for_entities(entities)
conc = time.perf_counter() - t0
n_calls = sum(len(dbi.QUERIES_BY_TYPE[t]) for _, t in entities)
print(f"\n{len(entities)} entities, {n_calls} lookups: one-at-a-time {seq:.1f}s, concurrent {conc:.1f}s")
report["_timing"] = {"entities": len(entities), "lookups": n_calls, "sequential_s": round(seq, 1), "concurrent_s": round(conc, 1)}

if os.getenv("GEMINI_API_KEY"):
    from app.services import relationship_extraction as rx, qa, ner
    from app import schemas
    sentence = "TP53 inhibits MDM2 in human cells, and BRCA1 interacts with RAD51 during homologous recombination."
    ents, _ = ner.extract_entities(sentence)
    t0 = time.perf_counter()
    rels = rx.extract_relationships(ents)
    report["gemini_relations"] = {"seconds": round(time.perf_counter() - t0, 2),
                                  "relations": [(r.source_text, r.relation_type, r.target_text, r.confidence) for r in rels]}
    req = schemas.AskRequest(filename="check", question="Which genes are linked to DNA repair?",
                             entities=[schemas.EntityOut(id=str(i), text=e.text, entity_type=e.entity_type, confidence=e.confidence,
                                                         sentence=e.sentence) for i, e in enumerate(ents)])
    report["gemini_qa"] = qa.answer_question(req, req.question)
    print("\nGemini relations:", report["gemini_relations"])
    print("Gemini Q&A:", report["gemini_qa"])
else:
    print("\n(GEMINI_API_KEY not set: skipping the Gemini checks)")

if args.json:
    Path(args.json).write_text(json.dumps(report, indent=1, default=str))

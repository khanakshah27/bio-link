"""
Offline evaluation of BioLink's entity recognition and its regex relation
fallback against hand-annotated passages.

    python eval/run_eval.py                       # both datasets, current code
    python eval/run_eval.py --backend ../other    # score a different checkout
    python eval/run_eval.py --json results.json

No network is used and GEMINI_API_KEY is ignored, so this measures the
deterministic parts only (recognition tier + regex fallback). It says nothing
about live database behaviour or Gemini quality; see scripts/live_check.py.

Scoring: entities are compared as sets of (type, normalized id) per passage;
relations as unordered entity pairs, with and without the relation label.
"""
import argparse
import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ap = argparse.ArgumentParser()
ap.add_argument("--backend", default=str(HERE.parent / "backend"), help="directory containing the 'app' package")
ap.add_argument("--datasets", nargs="*", default=["dataset_dev.json", "dataset_heldout.json"])
ap.add_argument("--json", help="write results to this file")
args = ap.parse_args()

os.environ["DATABASE_URL"] = "sqlite://"
os.environ.pop("GEMINI_API_KEY", None)
sys.path.insert(0, args.backend)
from app.services import ner, normalize as norm, relationship_extraction as rx  # noqa: E402


def prf(tp, fp, fn):
    p = tp / (tp + fp) if tp + fp else 0.0
    r = tp / (tp + fn) if tp + fn else 0.0
    return {"tp": tp, "fp": fp, "fn": fn, "precision": round(p, 3), "recall": round(r, 3),
            "f1": round(2 * p * r / (p + r), 3) if p + r else 0.0}


def entity_key(text, etype):
    return (etype, norm.normalize(text, etype))


def pair_key(a, b):
    return tuple(sorted([norm.normalize(a, "gene"), norm.normalize(b, "gene")]))


def score(dataset):
    tot = {"all": [0, 0, 0], "gene": [0, 0, 0], "non_gene": [0, 0, 0]}
    rel_typed = [0, 0, 0]
    rel_pair = [0, 0, 0]
    errors = {"fp": [], "fn": []}
    backend = None
    for p in dataset["passages"]:
        ents, backend = ner.extract_entities(p["text"])
        pred = {entity_key(e.text, e.entity_type) for e in ents}
        gold = {entity_key(t, ty) for t, ty in p["entities"]}
        for bucket, sel in (("all", lambda k: True), ("gene", lambda k: k[0] == "gene"), ("non_gene", lambda k: k[0] != "gene")):
            tot[bucket][0] += len({k for k in pred & gold if sel(k)})
            tot[bucket][1] += len({k for k in pred - gold if sel(k)})
            tot[bucket][2] += len({k for k in gold - pred if sel(k)})
        errors["fp"] += [f"{p['id']}: {k[0]} {k[1]}" for k in sorted(pred - gold)]
        errors["fn"] += [f"{p['id']}: {k[0]} {k[1]}" for k in sorted(gold - pred)]

        rels = rx.extract_relationships(ents)
        pf = {(pair_key(r.source_text, r.target_text), r.relation_type) for r in rels}
        gf = {(pair_key(a, b), lab) for a, b, lab in p["relations"]}
        rel_typed[0] += len(pf & gf); rel_typed[1] += len(pf - gf); rel_typed[2] += len(gf - pf)
        pp, gp = {x[0] for x in pf}, {x[0] for x in gf}
        rel_pair[0] += len(pp & gp); rel_pair[1] += len(pp - gp); rel_pair[2] += len(gp - pp)
    n_sent = sum(len(ner.split_sentences(p["text"])) for p in dataset["passages"])
    return {
        "dataset": dataset["name"], "ner_backend": backend,
        "passages": len(dataset["passages"]), "sentences": n_sent,
        "gold_entities": sum(len({entity_key(t, ty) for t, ty in p["entities"]}) for p in dataset["passages"]),
        "gold_relations": sum(len(p["relations"]) for p in dataset["passages"]),
        "entities_all": prf(*tot["all"]), "entities_gene": prf(*tot["gene"]), "entities_non_gene": prf(*tot["non_gene"]),
        "relations_pair_and_label": prf(*rel_typed), "relations_pair_only": prf(*rel_pair),
        "errors": errors,
    }


results = []
for name in args.datasets:
    ds = json.loads((HERE / name).read_text())
    results.append(score(ds))

for r in results:
    print(f"\n== {r['dataset']}  ({r['passages']} passages, {r['sentences']} sentences, "
          f"{r['gold_entities']} gold entities, {r['gold_relations']} gold relations; NER tier: {r['ner_backend']})")
    for k in ("entities_all", "entities_gene", "entities_non_gene", "relations_pair_and_label", "relations_pair_only"):
        m = r[k]
        print(f"  {k:26s} P={m['precision']:.2f} R={m['recall']:.2f} F1={m['f1']:.2f}   (TP {m['tp']}, FP {m['fp']}, FN {m['fn']})")
    print("  false positives:", "; ".join(r["errors"]["fp"]) or "none")
    print("  misses:         ", "; ".join(r["errors"]["fn"]) or "none")
if args.json:
    Path(args.json).write_text(json.dumps(results, indent=1))

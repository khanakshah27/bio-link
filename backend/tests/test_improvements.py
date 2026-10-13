"""Offline tests: every network call is replaced by a stub, so these check
parsing, ordering, rate limiting and fallbacks, not live service behaviour."""
import time
import threading

import pytest

from app.services import db_integrations as dbi, normalize as norm, ner, qa
from app import schemas


# ---------------- normalization ----------------
@pytest.mark.parametrize("text,etype,expected", [
    ("IL-6", "gene", "IL6"), ("il-6", "gene", "IL6"), ("HER-2", "gene", "ERBB2"),
    ("p53", "gene", "TP53"), ("BRCA-1", "gene", "BRCA1"), ("TNF-alpha", "gene", "TNF"),
    ("Alzheimer’s disease", "disease", "ALZHEIMER'S DISEASE"),
    ("Alzheimers disease", "disease", "ALZHEIMER'S DISEASE"),
    ("mice", "organism", "MUS MUSCULUS"),
])
def test_normalize(text, etype, expected):
    assert norm.normalize(text, etype) == expected


# ---------------- recognition of name variants ----------------
def _genes(text):
    ents, _ = ner.extract_entities(text)
    return {norm.normalize(e.text, e.entity_type) for e in ents if e.entity_type == "gene"}


def test_variants_are_recognised():
    assert {"TP53", "IL6", "ERBB2"} <= _genes("p53 and IL-6 were higher in HER-2 positive tumours.")


def test_reagents_and_software_are_not_genes():
    assert _genes("Cells expressed GFP and statistics used SPSS with BSA blocking.") == set()


# ---------------- GO ----------------
def test_go_parsing_groups_by_aspect_and_dedupes():
    rows = [
        {"goId": "GO:1", "goName": "apoptotic process", "goAspect": "biological_process"},
        {"goId": "GO:1", "goName": "apoptotic process", "goAspect": "biological_process"},
        {"goId": "GO:2", "goName": "nucleus", "goAspect": "cellular_component"},
        {"goId": "GO:3", "goName": "DNA binding", "goAspect": "molecular_function"},
        {"goId": "GO:9", "goAspect": "weird"},
    ]
    out = dbi.parse_go_annotations(rows)
    assert out["biological_process"] == ["apoptotic process"]
    assert out["cellular_component"] == ["nucleus"] and out["molecular_function"] == ["DNA binding"]
    assert out["distinct_terms"] == 3


def test_go_uses_uniprot_accession_and_falls_back(monkeypatch):
    seen = {}

    def fake_get(url, params=None, timeout=None):
        seen[url] = params
        if "annotation/search" in url:
            return {"results": [{"goId": "GO:5", "goName": "x", "goAspect": "F"}]}
        return None
    monkeypatch.setattr(dbi, "_get", fake_get)
    res = dbi.query_go("TP53", accession="P04637")
    assert res["status"] == "ok" and res["data"]["molecular_function"] == ["x"]
    assert [v for u, v in seen.items() if "annotation" in u][0]["geneProductId"] == "UniProtKB:P04637"
    monkeypatch.setattr(dbi, "_get", lambda *a, **k: None)
    assert dbi.query_go("TP53")["status"] == "offline_fallback"      # cached entry
    assert dbi.query_go("MDM2")["status"] == "unavailable"


# ---------------- ClinVar ----------------
@pytest.mark.parametrize("field", ["germline_classification", "clinical_significance"])
def test_clinvar_breakdown_both_schemas(monkeypatch, field):
    def fake_get(url, params=None, timeout=None):
        if "esearch" in url:
            return {"esearchresult": {"count": "6120", "idlist": ["1", "2", "3"]}}
        return {"result": {"uids": ["1", "2", "3"],
                           "1": {"title": "a", field: {"description": "Pathogenic"}},
                           "2": {"title": "b", field: {"description": "Pathogenic"}},
                           "3": {"title": "c", field: {"description": "Uncertain significance"}}}}
    monkeypatch.setattr(dbi, "_get", fake_get)
    res = dbi.query_clinvar("breast cancer")
    d = res["data"]
    assert res["status"] == "ok" and d["variant_count_reported"] == "6120"
    assert d["significance_breakdown_in_sample"] == {"Pathogenic": 2, "Uncertain significance": 1}
    assert len(d["example_variants"]) == 3


def test_clinvar_count_survives_summary_failure(monkeypatch):
    def fake_get(url, params=None, timeout=None):
        return {"esearchresult": {"count": "5", "idlist": ["1"]}} if "esearch" in url else None
    monkeypatch.setattr(dbi, "_get", fake_get)
    assert dbi.query_clinvar("melanoma")["data"] == {"variant_count_reported": "5"}


# ---------------- concurrency ----------------
def _slow_ok(delay):
    def fn(_t):
        time.sleep(delay)
        return {"status": "ok", "data": {"accession": "P1"}}
    return fn


def test_entity_lookups_run_concurrently_and_keep_order(monkeypatch):
    plan = [("ncbi_gene", _slow_ok(0.2)), ("uniprot", _slow_ok(0.2)), ("pdb", _slow_ok(0.2)),
            ("string", _slow_ok(0.2)), ("go", _slow_ok(0.2))]
    monkeypatch.setitem(dbi.QUERIES_BY_TYPE, "gene", plan)
    monkeypatch.setattr(dbi, "query_go", lambda sym, accession=None: (time.sleep(0.2),
                        {"status": "ok", "data": {"accession": accession}})[1])
    t0 = time.perf_counter()
    recs = dbi.query_all_for_entity("TP53", "gene")
    elapsed = time.perf_counter() - t0
    assert [r["source"] for r in recs] == ["ncbi_gene", "uniprot", "pdb", "string", "go"]
    assert recs[-1]["payload"]["accession"] == "P1"        # GO reused the UniProt accession
    assert elapsed < 0.7                                    # sequential would be >= 1.0 s


def test_many_entities_preserve_order(monkeypatch):
    monkeypatch.setattr(dbi, "query_all_for_entity",
                        lambda text, typ: (time.sleep(0.05), [{"source": text, "status": "ok", "payload": None}])[1])
    out = dbi.query_all_for_entities([(f"E{i}", "gene") for i in range(10)])
    assert [r[0]["source"] for r in out] == [f"E{i}" for i in range(10)]


def test_ncbi_throttle_spaces_calls(monkeypatch):
    monkeypatch.setattr(dbi.settings, "NCBI_API_KEY", "")
    dbi._ncbi_next_slot = 0.0
    stamps = []
    threads = [threading.Thread(target=lambda: (dbi._ncbi_throttle(), stamps.append(time.monotonic()))) for _ in range(5)]
    [t.start() for t in threads]; [t.join() for t in threads]
    stamps.sort()
    gaps = [b - a for a, b in zip(stamps, stamps[1:])]
    assert min(gaps) >= 0.30                                # ~3 requests/second at most


# ---------------- Q&A conversation memory ----------------
def _ask(question, history):
    return schemas.AskRequest(filename="p.pdf", question=question, history=history)


def _capture_prompt(monkeypatch):
    captured = {}

    def fake(prompt, **kwargs):
        captured["p"] = prompt
        return "ok", None
    monkeypatch.setattr(qa.llm_client, "call_gemini_verbose", fake)
    return captured


def test_followup_history_is_in_prompt_and_trimmed(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "x")
    captured = _capture_prompt(monkeypatch)
    hist = [schemas.ChatTurn(role="user" if i % 2 == 0 else "assistant", content=f"turn{i} " + "x" * 900) for i in range(10)]
    out = qa.answer_question(_ask("and which of those?", hist), "and which of those?")
    p = captured["p"]
    assert out["grounded"] and "Conversation so far" in p
    assert "turn9" in p and "turn4" in p and "turn3" not in p          # last 6 turns only
    assert "x" * 700 not in p                                          # each turn clipped to 600 chars
    assert p.rstrip().endswith("Question: and which of those?")


def test_no_history_leaves_prompt_unchanged(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "x")
    captured = _capture_prompt(monkeypatch)
    qa.answer_question(_ask("q", []), "q")
    assert "Conversation so far" not in captured["p"]


# ---------------- word boundaries / guessed genes ----------------
def test_dictionary_terms_need_word_boundaries():
    ents, _ = ner.extract_entities("The hazard ratio was 1.4 and the data were humanized.")
    assert [e.text for e in ents if e.entity_type == "organism"] == []


def test_guessed_genes_off_by_default_in_model_tiers(monkeypatch):
    from app.config import get_settings
    text = "Patients with COPD and CKD were scored by GATK."
    monkeypatch.setattr(get_settings(), "INCLUDE_GUESSED_GENES", False)
    ents, backend = ner.extract_entities(text)
    if backend != "dictionary":          # model tiers: guesses dropped
        assert not [e for e in ents if e.source == "heuristic"]

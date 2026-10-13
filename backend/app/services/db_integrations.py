"""
Clients for the seven external biological databases BioLink integrates
with. Each function makes a real HTTP call to the corresponding public
REST API and returns a small structured dict.

Design notes:
- Every call is wrapped in try/except with a timeout, so one slow or
  down database never breaks the whole pipeline.
- If USE_OFFLINE_FALLBACK is on (default) and a call fails, we return a
  small cached example record for a few well-known genes/diseases (the
  ones used in the worked example: TP53, BRCA1, breast cancer) so the
  UI still has something meaningful to render in restricted-network
  environments or during live demos without reliable wifi. Everything
  returned this way is tagged status="offline_fallback" so it is never
  confused with a live result.
"""
from __future__ import annotations

import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor

import requests
from typing import Any

from ..config import get_settings

settings = get_settings()


# NCBI E-utilities allow ~3 requests/second without an API key (10 with one).
# Lookups now run concurrently, so every NCBI call passes through this shared
# throttle to stay inside that limit no matter how many worker threads exist.
_ncbi_lock = threading.Lock()
_ncbi_next_slot = 0.0


def _ncbi_throttle() -> None:
    global _ncbi_next_slot
    interval = 0.11 if settings.NCBI_API_KEY else 0.34
    with _ncbi_lock:
        now = time.monotonic()
        wait = _ncbi_next_slot - now
        _ncbi_next_slot = max(now, _ncbi_next_slot) + interval
    if wait > 0:
        time.sleep(wait)


def _get(url: str, params: dict | None = None, timeout: float | None = None) -> dict | None:
    try:
        if url.startswith(settings.NCBI_EUTILS_BASE):
            _ncbi_throttle()
        resp = requests.get(url, params=params, timeout=timeout or settings.REQUEST_TIMEOUT_SECONDS)
        resp.raise_for_status()
        return resp.json()
    except Exception:
        return None


def _post(url: str, json_body: dict, timeout: float | None = None) -> dict | None:
    try:
        resp = requests.post(url, json=json_body, timeout=timeout or settings.REQUEST_TIMEOUT_SECONDS)
        resp.raise_for_status()
        return resp.json()
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Small offline fallback cache, keyed by (source, UPPERCASE query term).
# ---------------------------------------------------------------------------
_FALLBACK_CACHE: dict[tuple[str, str], dict[str, Any]] = {
    ("ncbi_gene", "TP53"): {"gene_id": "7157", "symbol": "TP53", "chromosome": "17",
                             "description": "tumor protein p53", "aliases": ["P53", "LFS1"]},
    ("ncbi_gene", "BRCA1"): {"gene_id": "672", "symbol": "BRCA1", "chromosome": "17",
                              "description": "BRCA1 DNA repair associated", "aliases": ["BRCAI", "PSCP"]},
    ("uniprot", "TP53"): {"accession": "P04637", "protein_name": "Cellular tumor antigen p53",
                           "length": 393, "function": "Acts as a tumor suppressor; induces cell cycle "
                           "arrest, apoptosis, and DNA repair in response to genotoxic stress."},
    ("uniprot", "BRCA1"): {"accession": "P38398", "protein_name": "Breast cancer type 1 susceptibility protein",
                            "length": 1863, "function": "E3 ubiquitin-protein ligase that participates in "
                            "DNA repair via homologous recombination."},
    ("pdb", "TP53"): {"structures": ["1TUP", "2XWR", "1TSR"]},
    ("pdb", "BRCA1"): {"structures": ["1JNX", "1T29", "4OFB"]},
    ("string", "TP53"): {"partners": ["MDM2", "BRCA1", "ATM", "CHEK2", "CDKN1A"]},
    ("string", "BRCA1"): {"partners": ["RAD51", "TP53", "BARD1", "PALB2", "BRCA2"]},
    ("go", "TP53"): {"biological_process": ["DNA damage response", "apoptotic process",
                                             "regulation of cell cycle"],
                      "molecular_function": ["DNA-binding transcription factor activity"],
                      "cellular_component": ["nucleus", "cytoplasm"]},
    ("go", "BRCA1"): {"biological_process": ["double-strand break repair via homologous recombination",
                                              "DNA damage response"],
                       "molecular_function": ["DNA binding", "ubiquitin-protein transferase activity"],
                       "cellular_component": ["nucleus", "BRCA1-A complex"]},
    ("kegg", "BREAST CANCER"): {"pathways": [{"id": "hsa05224", "name": "Breast cancer"}]},
    ("clinvar", "BREAST CANCER"): {"variant_count_reported": "several thousand (see ClinVar for live count)",
                                    "example_significance": ["Pathogenic", "Likely pathogenic", "Uncertain significance"]},
}


def _fallback(source: str, term: str) -> dict[str, Any] | None:
    if not settings.USE_OFFLINE_FALLBACK:
        return None
    return _FALLBACK_CACHE.get((source, term.upper()))


# ---------------------------------------------------------------------------
# Public query functions — one per database.
# Each returns: {"status": "ok"|"offline_fallback"|"unavailable", "data": {...}}
# ---------------------------------------------------------------------------

def query_ncbi_gene(symbol: str) -> dict:
    search = _get(f"{settings.NCBI_EUTILS_BASE}/esearch.fcgi", params={
        "db": "gene", "term": f"{symbol}[sym] AND human[orgn]", "retmode": "json",
        **({"api_key": settings.NCBI_API_KEY} if settings.NCBI_API_KEY else {}),
    })
    if search and search.get("esearchresult", {}).get("idlist"):
        gene_id = search["esearchresult"]["idlist"][0]
        summary = _get(f"{settings.NCBI_EUTILS_BASE}/esummary.fcgi", params={
            "db": "gene", "id": gene_id, "retmode": "json",
        })
        if summary:
            doc = summary.get("result", {}).get(gene_id, {})
            return {"status": "ok", "data": {
                "gene_id": gene_id,
                "symbol": doc.get("name", symbol),
                "chromosome": doc.get("chromosome"),
                "description": doc.get("description"),
                "aliases": doc.get("otheraliases", "").split(", ") if doc.get("otheraliases") else [],
            }}

    fb = _fallback("ncbi_gene", symbol)
    if fb:
        return {"status": "offline_fallback", "data": fb}
    return {"status": "unavailable", "data": None}


def query_uniprot(symbol: str) -> dict:
    result = _get(f"{settings.UNIPROT_BASE}/search", params={
        "query": f"gene:{symbol} AND organism_id:9606 AND reviewed:true",
        "format": "json", "size": 1,
    })
    if result and result.get("results"):
        rec = result["results"][0]
        protein_desc = (
            rec.get("proteinDescription", {})
            .get("recommendedName", {})
            .get("fullName", {})
            .get("value")
        )
        function_texts = [
            c.get("texts", [{}])[0].get("value")
            for c in rec.get("comments", [])
            if c.get("commentType") == "FUNCTION" and c.get("texts")
        ]
        return {"status": "ok", "data": {
            "accession": rec.get("primaryAccession"),
            "protein_name": protein_desc,
            "length": rec.get("sequence", {}).get("length"),
            "function": function_texts[0] if function_texts else None,
        }}

    fb = _fallback("uniprot", symbol)
    if fb:
        return {"status": "offline_fallback", "data": fb}
    return {"status": "unavailable", "data": None}


def query_pdb(symbol: str) -> dict:
    query_body = {
        "query": {
            "type": "terminal",
            "service": "full_text",
            "parameters": {"value": symbol},
        },
        "return_type": "entry",
        "request_options": {"paginate": {"start": 0, "rows": 5}},
    }
    result = _post(settings.RCSB_SEARCH_BASE, query_body)
    if result and result.get("result_set"):
        ids = [r["identifier"] for r in result["result_set"]]
        return {"status": "ok", "data": {"structures": ids}}

    fb = _fallback("pdb", symbol)
    if fb:
        return {"status": "offline_fallback", "data": fb}
    return {"status": "unavailable", "data": None}


def query_kegg_pathway(term: str) -> dict:
    try:
        resp = requests.get(f"{settings.KEGG_BASE}/find/pathway/{term}",
                             timeout=settings.REQUEST_TIMEOUT_SECONDS)
        if resp.status_code == 200 and resp.text.strip():
            pathways = []
            for line in resp.text.strip().split("\n"):
                parts = line.split("\t")
                if len(parts) == 2:
                    pathways.append({"id": parts[0].replace("path:", ""), "name": parts[1]})
            if pathways:
                return {"status": "ok", "data": {"pathways": pathways}}
    except Exception:
        pass

    fb = _fallback("kegg", term)
    if fb:
        return {"status": "offline_fallback", "data": fb}
    return {"status": "unavailable", "data": None}


_GO_ASPECTS = {
    "biological_process": "biological_process", "P": "biological_process", "process": "biological_process",
    "molecular_function": "molecular_function", "F": "molecular_function", "function": "molecular_function",
    "cellular_component": "cellular_component", "C": "cellular_component", "component": "cellular_component",
}
GO_TERMS_PER_ASPECT = 8


def _go_term_names(go_ids: list[str]) -> dict[str, str]:
    """Look up names for GO ids when the annotation response didn't include them."""
    if not go_ids:
        return {}
    data = _get(f"{settings.QUICKGO_BASE}/ontology/go/terms/{','.join(go_ids)}")
    out = {}
    for row in (data or {}).get("results", []):
        if row.get("id") and row.get("name"):
            out[row["id"]] = row["name"]
    return out


def parse_go_annotations(results: list[dict]) -> dict:
    """Group QuickGO annotation rows into the three GO aspects (deduplicated
    by GO id, most frequently annotated terms first)."""
    counts: Counter = Counter()
    names: dict[str, str] = {}
    aspect_of: dict[str, str] = {}
    for row in results:
        go_id = row.get("goId")
        aspect = _GO_ASPECTS.get(row.get("goAspect", ""))
        if not go_id or not aspect:
            continue
        counts[go_id] += 1
        aspect_of[go_id] = aspect
        if row.get("goName"):
            names[go_id] = row["goName"]
    missing = [g for g in counts if g not in names]
    if missing:
        names.update(_go_term_names(missing[:50]))
    grouped: dict[str, list[str]] = {"biological_process": [], "molecular_function": [], "cellular_component": []}
    for go_id, _n in counts.most_common():
        aspect = aspect_of[go_id]
        if len(grouped[aspect]) < GO_TERMS_PER_ASPECT:
            grouped[aspect].append(names.get(go_id, go_id))
    grouped["distinct_terms"] = len(counts)
    return grouped


def query_go(symbol: str, accession: str | None = None) -> dict:
    """GO terms for a gene via the QuickGO annotation endpoint. The UniProt
    accession is passed in when the caller already resolved it, otherwise it is
    looked up here."""
    if accession is None:
        acc_result = _get(f"{settings.UNIPROT_BASE}/search", params={
            "query": f"gene:{symbol} AND organism_id:9606 AND reviewed:true",
            "format": "json", "size": 1, "fields": "accession",
        })
        hits = (acc_result or {}).get("results") or []
        accession = hits[0].get("primaryAccession") if hits else None

    if accession:
        ann = _get(f"{settings.QUICKGO_BASE}/annotation/search", params={
            "geneProductId": f"UniProtKB:{accession}", "limit": 100, "includeFields": "goName",
        })
        if ann and ann.get("results"):
            grouped = parse_go_annotations(ann["results"])
            if any(grouped[k] for k in ("biological_process", "molecular_function", "cellular_component")):
                grouped["accession"] = accession
                return {"status": "ok", "data": grouped}

    fb = _fallback("go", symbol)
    if fb:
        return {"status": "offline_fallback", "data": fb}
    return {"status": "unavailable", "data": None}


CLINVAR_SAMPLE_SIZE = 20


def _clinvar_significance(doc: dict) -> str | None:
    """ClinVar's esummary schema changed: newer records use
    germline_classification, older ones clinical_significance."""
    for key in ("germline_classification", "clinical_significance"):
        block = doc.get(key)
        if isinstance(block, dict) and block.get("description"):
            return block["description"]
    return None


def parse_clinvar_summary(summary: dict) -> dict:
    result = (summary or {}).get("result", {})
    uids = [u for u in result.get("uids", []) if u in result]
    breakdown: Counter = Counter()
    examples = []
    for uid in uids:
        doc = result[uid]
        sig = _clinvar_significance(doc) or "not reported"
        breakdown[sig] += 1
        if len(examples) < 5:
            examples.append({"id": uid, "title": doc.get("title"), "significance": sig})
    return {"significance_breakdown_in_sample": dict(breakdown), "example_variants": examples}


def query_clinvar(disease_term: str) -> dict:
    key = {"api_key": settings.NCBI_API_KEY} if settings.NCBI_API_KEY else {}
    search = _get(f"{settings.NCBI_EUTILS_BASE}/esearch.fcgi", params={
        "db": "clinvar", "term": disease_term, "retmode": "json", "retmax": CLINVAR_SAMPLE_SIZE, **key,
    })
    if search and "esearchresult" in search:
        count = search["esearchresult"].get("count")
        if count is not None:
            data: dict[str, Any] = {"variant_count_reported": count}
            ids = search["esearchresult"].get("idlist") or []
            if ids:
                summary = _get(f"{settings.NCBI_EUTILS_BASE}/esummary.fcgi", params={
                    "db": "clinvar", "id": ",".join(ids), "retmode": "json", **key,
                })
                if summary:
                    data.update(parse_clinvar_summary(summary))
            return {"status": "ok", "data": data}

    fb = _fallback("clinvar", disease_term)
    if fb:
        return {"status": "offline_fallback", "data": fb}
    return {"status": "unavailable", "data": None}


def query_string(symbol: str) -> dict:
    result = _get(f"{settings.STRING_BASE}/network", params={
        "identifiers": symbol, "species": 9606, "limit": 8,
    })
    if isinstance(result, list) and result:
        partners = sorted({row.get("preferredName_B") for row in result if row.get("preferredName_B")})
        return {"status": "ok", "data": {"partners": partners}}

    fb = _fallback("string", symbol)
    if fb:
        return {"status": "offline_fallback", "data": fb}
    return {"status": "unavailable", "data": None}


# ---------------------------------------------------------------------------
# Dispatch table: which databases get queried for which entity type,
# mirroring the worked example in the project brief (genes -> NCBI/
# UniProt/PDB/STRING/GO; diseases -> ClinVar/KEGG).
# ---------------------------------------------------------------------------
QUERIES_BY_TYPE = {
    "gene": [("ncbi_gene", query_ncbi_gene), ("uniprot", query_uniprot),
             ("pdb", query_pdb), ("string", query_string), ("go", query_go)],
    "protein": [("uniprot", query_uniprot), ("pdb", query_pdb)],
    "disease": [("clinvar", query_clinvar), ("kegg", query_kegg_pathway)],
    "pathway": [("kegg", query_kegg_pathway)],
    "snp": [("clinvar", query_clinvar)],
    "organism": [],
    "chemical": [],
}


def query_all_for_entity(entity_text: str, entity_type: str) -> list[dict]:
    """Run every relevant database query for one entity, returning a list
    of {"source": ..., "status": ..., "payload": ...} dicts in dispatch order.

    The queries for one entity are independent of each other, so they run
    concurrently. The one dependency is Gene Ontology, which needs the UniProt
    accession, so it waits for the UniProt result and reuses it.
    """
    plan = QUERIES_BY_TYPE.get(entity_type, [])
    if not plan:
        return []
    with ThreadPoolExecutor(max_workers=len(plan)) as pool:
        futures = {}
        for source, fn in plan:
            if source == "go" and "uniprot" in futures:
                up = futures["uniprot"]

                def go_task(sym=entity_text, up=up):
                    res = up.result()
                    acc = (res.get("data") or {}).get("accession") if res["status"] == "ok" else None
                    return query_go(sym, accession=acc)

                futures[source] = pool.submit(go_task)
            else:
                futures[source] = pool.submit(fn, entity_text)
        records = []
        for source, _fn in plan:
            result = futures[source].result()
            records.append({"source": source, "status": result["status"], "payload": result["data"]})
    return records


def query_all_for_entities(entities: list[tuple[str, str]], max_workers: int = 4) -> list[list[dict]]:
    """Look up many (text, type) entities concurrently, preserving input order."""
    if not entities:
        return []
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        return list(pool.map(lambda et: query_all_for_entity(*et), entities))

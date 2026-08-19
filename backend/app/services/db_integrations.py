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

import requests
from typing import Any

from ..config import get_settings

settings = get_settings()


def _get(url: str, params: dict | None = None, timeout: float | None = None) -> dict | None:
    try:
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


def query_go(symbol: str) -> dict:
    result = _get(f"{settings.QUICKGO_BASE}/geneproduct/search", params={"query": symbol, "limit": 1})
    # QuickGO's annotation search has a different shape; kept intentionally
    # simple here since the free-text search endpoint mainly confirms the
    # gene product exists. A production build would call the /annotation
    # endpoint with the resolved UniProt accession for full GO term lists.
    if result and result.get("results"):
        return {"status": "ok", "data": {"go_search_hit": result["results"][0]}}

    fb = _fallback("go", symbol)
    if fb:
        return {"status": "offline_fallback", "data": fb}
    return {"status": "unavailable", "data": None}


def query_clinvar(disease_term: str) -> dict:
    search = _get(f"{settings.NCBI_EUTILS_BASE}/esearch.fcgi", params={
        "db": "clinvar", "term": disease_term, "retmode": "json", "retmax": 0,
        **({"api_key": settings.NCBI_API_KEY} if settings.NCBI_API_KEY else {}),
    })
    if search and "esearchresult" in search:
        count = search["esearchresult"].get("count")
        if count is not None:
            return {"status": "ok", "data": {"variant_count_reported": count}}

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
    of {"source": ..., "status": ..., "payload": ...} dicts."""
    records = []
    for source, fn in QUERIES_BY_TYPE.get(entity_type, []):
        result = fn(entity_text)
        records.append({
            "source": source,
            "status": result["status"],
            "payload": result["data"],
        })
    return records

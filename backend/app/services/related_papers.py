"""
"Related papers": a sidebar of real PubMed articles related to the one
being viewed.

Two paths:
  - The paper has a PubMed ID (fetched via pubmed_fetch, not uploaded as a
    PDF): use PubMed's own ELink "neighbor" relation - the same algorithm
    that powers PubMed's own "Similar articles" list. Free, no rate-limit
    concerns, no LLM involved.
  - The paper has no PubMed ID (an uploaded PDF, or a fetched paper with
    no neighbors returned): there's no PubMed record to find neighbors
    *of*, so instead ask Gemini for one short PubMed search query built
    from the paper's own extracted top entities, and run that query
    against PubMed's real search index. One LLM call per paper (not per
    entity/sentence, unlike the mistake that caused the earlier rate-limit
    issue), and it's grounded by real PubMed results rather than the LLM
    inventing citations. Falls back to a plain keyword-OR query (no LLM
    involved at all) if GEMINI_API_KEY isn't configured or the call fails.
"""
from __future__ import annotations

import requests

from ..config import get_settings
from . import llm_client

settings = get_settings()

MAX_RELATED = 8
SEARCHABLE_TYPES = {"gene", "disease", "pathway", "protein"}


def get_related_papers(pubmed_id: str | None, entities: list) -> dict:
    if pubmed_id:
        papers = _related_by_pmid(pubmed_id)
        if papers:
            return {"papers": papers, "method": "pubmed_similar_articles"}

    query = _build_search_query(entities)
    if not query:
        return {"papers": [], "method": "none"}

    papers = _search_pubmed(query)
    if not papers:
        return {"papers": [], "method": "none"}
    method = "llm_keyword_search" if llm_client.is_available() else "keyword_search"
    return {"papers": papers, "method": method}


def _eutils_params(**extra) -> dict:
    params = dict(extra)
    if settings.NCBI_API_KEY:
        params["api_key"] = settings.NCBI_API_KEY
    return params


def _related_by_pmid(pmid: str) -> list[dict]:
    try:
        resp = requests.get(
            f"{settings.NCBI_EUTILS_BASE}/elink.fcgi",
            params=_eutils_params(
                dbfrom="pubmed", db="pubmed", id=pmid,
                linkname="pubmed_pubmed", retmode="json",
            ),
            timeout=settings.REQUEST_TIMEOUT_SECONDS,
        )
        resp.raise_for_status()
        related_ids = []
        for linkset in resp.json().get("linksets", []):
            for linksetdb in linkset.get("linksetdbs", []):
                for link in linksetdb.get("links", []):
                    link_id = str(link)
                    if link_id != str(pmid) and link_id not in related_ids:
                        related_ids.append(link_id)
        if not related_ids:
            return []
        return _summarize_pmids(related_ids[:MAX_RELATED])
    except Exception:
        return []


def _build_search_query(entities: list) -> str | None:
    ranked = sorted(
        (e for e in entities if e.entity_type in SEARCHABLE_TYPES),
        key=lambda e: e.confidence or 0,
        reverse=True,
    )
    top_terms = []
    seen = set()
    for e in ranked:
        key = e.text.upper()
        if key in seen:
            continue
        seen.add(key)
        top_terms.append(e.text)
        if len(top_terms) >= 6:
            break
    if not top_terms:
        return None

    return _llm_search_query(top_terms) or " OR ".join(f'"{t}"[tiab]' for t in top_terms[:5])


def _llm_search_query(terms: list[str]) -> str | None:
    prompt = (
        "Write ONE PubMed search query (standard PubMed search syntax - "
        "field tags like [tiab] or [mesh], boolean AND/OR) that would find "
        "papers related to a study centered on these biomedical entities: "
        + ", ".join(terms) + ". Respond with ONLY the query string: no "
        "explanation, no surrounding quotes, no markdown."
    )
    query = llm_client.call_gemini(prompt, max_output_tokens=120, temperature=0.2)
    if not query:
        return None
    return query.strip().strip("`").strip() or None


def _search_pubmed(query: str) -> list[dict]:
    try:
        resp = requests.get(
            f"{settings.NCBI_EUTILS_BASE}/esearch.fcgi",
            params=_eutils_params(
                db="pubmed", term=query, retmode="json",
                retmax=MAX_RELATED, sort="relevance",
            ),
            timeout=settings.REQUEST_TIMEOUT_SECONDS,
        )
        resp.raise_for_status()
        ids = resp.json().get("esearchresult", {}).get("idlist", [])
        return _summarize_pmids(ids) if ids else []
    except Exception:
        return []


def _summarize_pmids(pmids: list[str]) -> list[dict]:
    try:
        resp = requests.get(
            f"{settings.NCBI_EUTILS_BASE}/esummary.fcgi",
            params=_eutils_params(db="pubmed", id=",".join(pmids), retmode="json"),
            timeout=settings.REQUEST_TIMEOUT_SECONDS,
        )
        resp.raise_for_status()
        result = resp.json().get("result", {})
        papers = []
        for pmid in result.get("uids", pmids):
            doc = result.get(pmid)
            if not doc:
                continue
            author_list = doc.get("authors", []) or []
            authors = ", ".join(a.get("name", "") for a in author_list[:3] if a.get("name"))
            if len(author_list) > 3:
                authors += " et al."
            papers.append({
                "pmid": pmid,
                "title": (doc.get("title") or "Untitled").rstrip("."),
                "authors": authors or None,
                "journal": doc.get("fulljournalname") or doc.get("source") or None,
                "year": (doc.get("pubdate") or "").split(" ")[0] or None,
                "url": f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
            })
        return papers
    except Exception:
        return []

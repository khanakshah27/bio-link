"""
Fetch a paper's text/metadata from PubMed given a PMID or a PubMed URL, so
BioLink can run its pipeline on a published paper without a PDF upload.

Best-effort text depth: if the paper has an open-access copy on PubMed
Central, fetch the full text there (via ELink pubmed->pmc, then EFetch on
the PMC record); otherwise fall back to the PubMed abstract, which is
available for effectively every PubMed record (most records have no PMC
copy at all - most publishers don't deposit full text there). Either way,
whatever text comes back is run through the exact same NER/relationship/
summary pipeline as an uploaded PDF.
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass

import requests

from ..config import get_settings

settings = get_settings()

_PUBMED_URL_RE = re.compile(r"(?:ncbi\.nlm\.nih\.gov/pubmed|pubmed\.ncbi\.nlm\.nih\.gov)/(\d+)", re.IGNORECASE)
_BARE_ID_RE = re.compile(r"(\d{5,9})")


@dataclass
class PubmedPaper:
    pmid: str
    title: str
    text: str            # abstract, or full text if a PMC copy was found
    has_full_text: bool
    url: str


def parse_pmid(raw: str) -> str | None:
    """Accepts a bare PMID, a pubmed.ncbi.nlm.nih.gov / ncbi.nlm.nih.gov/pubmed
    URL, or free text containing one of those. Returns the numeric PMID, or
    None if nothing plausible was found."""
    raw = (raw or "").strip()
    if not raw:
        return None
    if raw.isdigit():
        return raw
    m = _PUBMED_URL_RE.search(raw)
    if m:
        return m.group(1)
    m = _BARE_ID_RE.search(raw)
    return m.group(1) if m else None


def fetch_pubmed_paper(pmid: str) -> PubmedPaper:
    """Raises ValueError if the PMID doesn't resolve to a usable PubMed
    record (not found, or has neither an abstract nor full text)."""
    title, abstract = _fetch_summary_and_abstract(pmid)
    if title is None and abstract is None:
        raise ValueError(f"No PubMed record found for PMID {pmid}.")

    full_text = _fetch_pmc_full_text(pmid)
    body = full_text or abstract
    if not body or not body.strip():
        raise ValueError(f"PubMed record {pmid} has no abstract or full text available.")

    display_title = title or f"PubMed {pmid}"
    return PubmedPaper(
        pmid=pmid,
        title=display_title,
        text=f"{display_title}\n\n{body}",
        has_full_text=bool(full_text),
        url=f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
    )


def _eutils_params(**extra) -> dict:
    params = dict(extra)
    if settings.NCBI_API_KEY:
        params["api_key"] = settings.NCBI_API_KEY
    return params


def _fetch_summary_and_abstract(pmid: str) -> tuple[str | None, str | None]:
    title = None
    try:
        resp = requests.get(
            f"{settings.NCBI_EUTILS_BASE}/esummary.fcgi",
            params=_eutils_params(db="pubmed", id=pmid, retmode="json"),
            timeout=settings.REQUEST_TIMEOUT_SECONDS,
        )
        resp.raise_for_status()
        doc = resp.json().get("result", {}).get(pmid)
        if doc and not doc.get("error"):
            title = doc.get("title") or None
    except Exception:
        pass

    abstract = None
    try:
        resp = requests.get(
            f"{settings.NCBI_EUTILS_BASE}/efetch.fcgi",
            params=_eutils_params(db="pubmed", id=pmid, rettype="abstract", retmode="xml"),
            timeout=settings.REQUEST_TIMEOUT_SECONDS,
        )
        resp.raise_for_status()
        root = ET.fromstring(resp.content)
        parts = [el.text or "" for el in root.iter("AbstractText")]
        abstract = "\n".join(p for p in parts if p).strip() or None
        if title is None:
            title_el = root.find(".//ArticleTitle")
            if title_el is not None and "".join(title_el.itertext()).strip():
                title = "".join(title_el.itertext()).strip()
    except Exception:
        pass

    return title, abstract


def _fetch_pmc_full_text(pmid: str) -> str | None:
    """Best-effort: only succeeds if this PubMed record has an open-access
    PubMed Central copy. Returns None otherwise (the caller falls back to
    the abstract) rather than raising, since that's the common case."""
    try:
        resp = requests.get(
            f"{settings.NCBI_EUTILS_BASE}/elink.fcgi",
            params=_eutils_params(dbfrom="pubmed", db="pmc", id=pmid, retmode="json"),
            timeout=settings.REQUEST_TIMEOUT_SECONDS,
        )
        resp.raise_for_status()
        pmc_id = None
        for linkset in resp.json().get("linksets", []):
            for linksetdb in linkset.get("linksetdbs", []):
                links = linksetdb.get("links") or []
                if links:
                    pmc_id = str(links[0])
                    break
            if pmc_id:
                break
        if not pmc_id:
            return None

        resp = requests.get(
            f"{settings.NCBI_EUTILS_BASE}/efetch.fcgi",
            params=_eutils_params(db="pmc", id=pmc_id, rettype="full", retmode="xml"),
            timeout=settings.REQUEST_TIMEOUT_SECONDS,
        )
        resp.raise_for_status()
        root = ET.fromstring(resp.content)
        body = root.find(".//body")
        if body is None:
            return None
        paragraphs = ["".join(p.itertext()).strip() for p in body.iter("p")]
        text = "\n".join(p for p in paragraphs if p)
        return text or None
    except Exception:
        return None

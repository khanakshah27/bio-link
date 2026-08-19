"""
Biomedical Named Entity Recognition.

Two backends, chosen automatically at import time:

1. scispacy (preferred) - a real biomedical NER model (en_core_sci_sm or
   larger). Install with:
       pip install scispacy
       pip install https://s3-us-west-2.amazonaws.com/ai2-s2-research-public/scispacy/20230811/en_core_sci_sm-0.5.3.tar.gz
   scispacy's generic model tags spans as "ENTITY" without fine-grained
   types, so we still run them through the same type-classification /
   lexicon step as the fallback to bucket them into gene / protein /
   disease / pathway / organism / chemical / snp.

2. Dictionary + regex fallback (always available, zero extra downloads).
   Good enough for demos and small curated corpora; not a substitute for
   a trained model in production.

Either way the output shape is identical: a list of ExtractedEntity.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

from .pdf_extract import split_sentences

# ---------------------------------------------------------------------------
# Curated biomedical lexicons (fallback mode + type disambiguation for
# scispacy mode, since scispacy's general "ENTITY" label isn't typed).
# In a production build these would be swapped for full HGNC / MeSH / GO
# term lists loaded from disk; a small curated set keeps this demo fast
# and dependency-free.
# ---------------------------------------------------------------------------
GENE_SYMBOLS = {
    "TP53", "BRCA1", "BRCA2", "EGFR", "KRAS", "MDM2", "RAD51", "PTEN",
    "MYC", "APC", "ALK", "BRAF", "PIK3CA", "RB1", "ATM", "CHEK2", "PALB2",
    "HER2", "ERBB2", "VEGFA", "IL6", "TNF", "INS", "APOE", "CFTR", "HTT",
    "MTHFR", "AKT1", "NRAS", "STK11", "SMAD4", "NOTCH1", "JAK2", "FLT3",
}
DISEASE_TERMS = {
    "breast cancer", "lung cancer", "ovarian cancer", "colorectal cancer",
    "pancreatic cancer", "prostate cancer", "melanoma", "leukemia",
    "alzheimer's disease", "alzheimer disease", "parkinson's disease",
    "type 2 diabetes", "diabetes mellitus", "cystic fibrosis",
    "huntington's disease", "cardiovascular disease", "hypertension",
    "obesity", "asthma", "rheumatoid arthritis",
}
PATHWAY_TERMS = {
    "dna repair", "dna damage response", "homologous recombination",
    "mapk signaling", "mapk pathway", "pi3k/akt pathway", "pi3k-akt pathway",
    "apoptosis", "cell cycle", "wnt signaling", "notch signaling",
    "jak-stat pathway", "mtor pathway", "p53 signaling pathway",
    "insulin signaling", "nf-kb pathway",
}
ORGANISM_TERMS = {
    "homo sapiens", "human", "humans", "mus musculus", "mouse", "mice",
    "escherichia coli", "e. coli", "saccharomyces cerevisiae",
    "drosophila melanogaster", "rattus norvegicus", "rat", "zebrafish",
    "danio rerio", "caenorhabditis elegans",
}
CHEMICAL_TERMS = {
    "cisplatin", "tamoxifen", "doxorubicin", "paclitaxel", "metformin",
    "olaparib", "imatinib", "erlotinib", "atp", "nadh", "glucose",
    "dopamine", "serotonin", "insulin",
}

SNP_PATTERN = re.compile(r"\brs\d{3,}\b", re.IGNORECASE)
# HGNC-style gene symbol heuristic: 2-8 chars, starts with a letter,
# uppercase letters/digits, not a common all-caps English acronym.
GENE_PATTERN = re.compile(r"\b[A-Z][A-Z0-9]{1,7}\b")
COMMON_ACRONYM_STOPLIST = {
    "DNA", "RNA", "PCR", "USA", "UK", "NIH", "WHO", "FDA", "PDF", "AI",
    "NLP", "API", "CSV", "JSON", "XML", "HTML", "CT", "MRI", "ELISA",
}


@dataclass
class ExtractedEntity:
    text: str
    entity_type: str          # gene|protein|disease|pathway|organism|chemical|snp
    start_char: int
    end_char: int
    sentence: str
    confidence: float = 0.75
    source: str = "dictionary"  # dictionary|scispacy


def _classify_span(surface: str) -> Optional[str]:
    """Map a surface string to a BioLink entity type using the lexicons."""
    lower = surface.lower().strip()
    if SNP_PATTERN.fullmatch(surface):
        return "snp"
    if lower in DISEASE_TERMS:
        return "disease"
    if lower in PATHWAY_TERMS:
        return "pathway"
    if lower in ORGANISM_TERMS:
        return "organism"
    if lower in CHEMICAL_TERMS:
        return "chemical"
    if surface.upper() in GENE_SYMBOLS:
        return "gene"
    return None


def _dictionary_extract(text: str) -> list[ExtractedEntity]:
    """Regex + curated-lexicon extraction. No external model required."""
    entities: list[ExtractedEntity] = []
    sentences = split_sentences(text)
    cursor = 0

    for sentence in sentences:
        start_offset = text.find(sentence, cursor)
        if start_offset == -1:
            start_offset = cursor
        cursor = start_offset + len(sentence)

        seen_spans: set[tuple[int, int]] = set()

        def add(match_text: str, m_start: int, etype: str, conf: float, src: str):
            span = (m_start, m_start + len(match_text))
            if span in seen_spans:
                return
            seen_spans.add(span)
            entities.append(ExtractedEntity(
                text=match_text,
                entity_type=etype,
                start_char=start_offset + m_start,
                end_char=start_offset + m_start + len(match_text),
                sentence=sentence,
                confidence=conf,
                source=src,
            ))

        # Multi-word dictionary terms (disease/pathway/organism/chemical)
        for term_set, etype in (
            (DISEASE_TERMS, "disease"),
            (PATHWAY_TERMS, "pathway"),
            (ORGANISM_TERMS, "organism"),
            (CHEMICAL_TERMS, "chemical"),
        ):
            for term in term_set:
                for m in re.finditer(re.escape(term), sentence, re.IGNORECASE):
                    add(sentence[m.start():m.end()], m.start(), etype, 0.8, "dictionary")

        # SNPs
        for m in SNP_PATTERN.finditer(sentence):
            add(m.group(0), m.start(), "snp", 0.9, "regex")

        # Gene symbols: curated list first (high confidence)...
        for gene in GENE_SYMBOLS:
            for m in re.finditer(rf"\b{re.escape(gene)}\b", sentence):
                add(m.group(0), m.start(), "gene", 0.9, "dictionary")

        # ...then a lower-confidence heuristic for unlisted gene-like tokens.
        for m in GENE_PATTERN.finditer(sentence):
            token = m.group(0)
            if token in COMMON_ACRONYM_STOPLIST or token in GENE_SYMBOLS:
                continue
            if any(ch.isdigit() for ch in token) or (len(token) <= 5 and token.isupper()):
                add(token, m.start(), "gene", 0.5, "heuristic")

    return entities


def _scispacy_extract(text: str, nlp) -> list[ExtractedEntity]:
    """Run a loaded scispacy/spaCy pipeline and classify spans via the
    lexicon (scispacy's generic model doesn't type-label its spans)."""
    entities: list[ExtractedEntity] = []
    doc = nlp(text)
    for sent in doc.sents:
        for ent in sent.ents:
            etype = _classify_span(ent.text) or "unclassified"
            if etype == "unclassified":
                # Skip spans we can't confidently bucket into one of
                # BioLink's seven categories, to keep the dashboard clean.
                continue
            entities.append(ExtractedEntity(
                text=ent.text,
                entity_type=etype,
                start_char=ent.start_char,
                end_char=ent.end_char,
                sentence=sent.text.strip(),
                confidence=0.85,
                source="scispacy",
            ))
    # scispacy's NER can miss SNP ids / dictionary-only terms it wasn't
    # trained on, so we still union in the deterministic regex/dictionary
    # pass for SNPs, diseases, pathways, organisms and known gene symbols.
    entities.extend([
        e for e in _dictionary_extract(text)
        if e.entity_type in {"snp"} or e.source == "dictionary"
    ])
    return _dedupe(entities)


def _dedupe(entities: list[ExtractedEntity]) -> list[ExtractedEntity]:
    seen = set()
    unique = []
    for e in entities:
        key = (e.text.upper(), e.entity_type, e.start_char)
        if key in seen:
            continue
        seen.add(key)
        unique.append(e)
    return unique


class _NlpSingleton:
    """Lazily loads a scispacy/spaCy pipeline once per process."""
    _nlp = None
    _tried = False
    _backend = "dictionary"

    @classmethod
    def get(cls):
        if cls._tried:
            return cls._nlp
        cls._tried = True
        from ..config import get_settings
        settings = get_settings()
        try:
            import spacy
            try:
                cls._nlp = spacy.load(settings.SCISPACY_MODEL)
                cls._backend = "scispacy"
            except OSError:
                cls._nlp = spacy.load(settings.SPACY_FALLBACK_MODEL)
                cls._backend = "spacy_fallback"
        except Exception:
            cls._nlp = None
            cls._backend = "dictionary"
        return cls._nlp

    @classmethod
    def backend(cls) -> str:
        cls.get()
        return cls._backend


def extract_entities(text: str) -> tuple[list[ExtractedEntity], str]:
    """
    Extract biomedical entities from raw paper text.

    Returns (entities, backend_used) where backend_used is one of
    'scispacy', 'spacy_fallback', or 'dictionary' — surfaced in the API
    response so the frontend can show which NER engine produced results.
    """
    nlp = _NlpSingleton.get()
    backend = _NlpSingleton.backend()

    if nlp is not None:
        try:
            return _scispacy_extract(text, nlp), backend
        except Exception:
            pass  # fall through to dictionary backend on any runtime error

    return _dictionary_extract(text), "dictionary"

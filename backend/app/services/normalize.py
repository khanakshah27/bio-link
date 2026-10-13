"""
Entity normalization: map surface forms/aliases to a single canonical
identifier so "TP53", "p53" and "tumor protein p53" all collapse to one
node in the knowledge graph instead of three.

Matching is case-insensitive and, for gene symbols, ignores hyphens and
spaces between letters and digits ("IL-6" -> IL6, "BRCA-1" -> BRCA1,
"HER-2" -> HER2 -> ERBB2).

Species: gene symbols are assumed to be human. Every gene lookup in
db_integrations.py is restricted to Homo sapiens (taxon 9606), so a mouse
"Trp53" is not told apart from human TP53.
"""
import re

ALIASES = {
    "gene": {
        "P53": "TP53", "TUMOR PROTEIN P53": "TP53", "TUMOUR PROTEIN P53": "TP53",
        "HER2": "ERBB2", "HER2NEU": "ERBB2", "NEU": "ERBB2",
        "TNFALPHA": "TNF", "TNFA": "TNF", "TNFΑ": "TNF",
        "INTERLEUKIN 6": "IL6", "INTERLEUKIN6": "IL6",
        "P16": "CDKN2A", "P21": "CDKN1A", "P27": "CDKN1B",
        "KI-RAS": "KRAS", "KIRAS": "KRAS",
        "HER1": "EGFR", "ERBB1": "EGFR",
    },
    "disease": {
        "ALZHEIMER DISEASE": "ALZHEIMER'S DISEASE", "ALZHEIMERS DISEASE": "ALZHEIMER'S DISEASE",
        "PARKINSON DISEASE": "PARKINSON'S DISEASE", "PARKINSONS DISEASE": "PARKINSON'S DISEASE",
        "HUNTINGTON DISEASE": "HUNTINGTON'S DISEASE", "HUNTINGTONS DISEASE": "HUNTINGTON'S DISEASE",
        "TYPE 2 DIABETES": "TYPE 2 DIABETES MELLITUS",
        "DIABETES MELLITUS": "TYPE 2 DIABETES MELLITUS",
        "T2DM": "TYPE 2 DIABETES MELLITUS",
        "LEUKAEMIA": "LEUKEMIA",
        "HYPERTENSIVE DISEASE": "HYPERTENSION", "HIGH BLOOD PRESSURE": "HYPERTENSION",
        "BREAST CARCINOMA": "BREAST CANCER", "LUNG CARCINOMA": "LUNG CANCER",
    },
    "organism": {
        "HUMAN": "HOMO SAPIENS", "HUMANS": "HOMO SAPIENS",
        "MOUSE": "MUS MUSCULUS", "MICE": "MUS MUSCULUS",
        "E. COLI": "ESCHERICHIA COLI", "E COLI": "ESCHERICHIA COLI",
        "RAT": "RATTUS NORVEGICUS", "RATS": "RATTUS NORVEGICUS",
        "YEAST": "SACCHAROMYCES CEREVISIAE", "FRUIT FLY": "DROSOPHILA MELANOGASTER",
        "ZEBRAFISH": "DANIO RERIO",
    },
}

_SEPARATORS = re.compile(r"[-_\s]+")


def _gene_key(text: str) -> str:
    """Upper-case and drop hyphens/underscores/spaces: IL-6 -> IL6."""
    return _SEPARATORS.sub("", text.strip().upper())


def gene_symbol_variants(symbol: str) -> list[str]:
    """Surface forms a canonical gene symbol may take in text (symbol with an
    optional hyphen between the letters and the digits)."""
    m = re.fullmatch(r"([A-Z]+)(\d+[A-Z]?)", symbol)
    return [symbol] + ([f"{m.group(1)}-{m.group(2)}"] if m else [])


def normalize(text: str, entity_type: str) -> str:
    """Return a canonical identifier string for an entity."""
    key = text.strip().upper()
    type_aliases = ALIASES.get(entity_type, {})
    if key in type_aliases:
        return type_aliases[key]
    if entity_type == "gene":
        collapsed = _gene_key(text)
        if collapsed in type_aliases:
            return type_aliases[collapsed]
        return collapsed
    # Non-gene types: apostrophes and extra spaces vary in running text.
    squashed = re.sub(r"\s+", " ", key.replace("\u2019", "'"))
    return type_aliases.get(squashed, squashed)

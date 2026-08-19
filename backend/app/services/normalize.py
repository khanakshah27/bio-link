"""
Entity normalization: map surface forms/aliases to a single canonical
identifier so "TP53", "p53" and "tumor protein p53" all collapse to one
node in the knowledge graph instead of three.
"""

ALIASES = {
    "gene": {
        "P53": "TP53", "TUMOR PROTEIN P53": "TP53", "TUMOUR PROTEIN P53": "TP53",
        "HER2": "ERBB2", "HER-2": "ERBB2", "NEU": "ERBB2",
        "BRCA-1": "BRCA1", "BRCA-2": "BRCA2",
    },
    "disease": {
        "ALZHEIMER DISEASE": "ALZHEIMER'S DISEASE",
        "PARKINSON DISEASE": "PARKINSON'S DISEASE",
        "TYPE 2 DIABETES": "TYPE 2 DIABETES MELLITUS",
        "DIABETES MELLITUS": "TYPE 2 DIABETES MELLITUS",
    },
    "organism": {
        "HUMAN": "HOMO SAPIENS", "HUMANS": "HOMO SAPIENS",
        "MOUSE": "MUS MUSCULUS", "MICE": "MUS MUSCULUS",
        "E. COLI": "ESCHERICHIA COLI", "RAT": "RATTUS NORVEGICUS",
    },
}


def normalize(text: str, entity_type: str) -> str:
    """Return a canonical identifier string for an entity."""
    key = text.strip().upper()
    type_aliases = ALIASES.get(entity_type, {})
    return type_aliases.get(key, key)

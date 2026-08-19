"""
Lightweight pattern-based relationship extraction.

Looks for two BioLink entities co-occurring in the same sentence with a
recognized relational verb phrase between them, e.g.:

    "TP53 inhibits MDM2"                -> (TP53, inhibits, MDM2)
    "BRCA1 interacts with RAD51"        -> (BRCA1, interacts_with, RAD51)
    "Mutations in TP53 ... increase breast cancer risk"
                                         -> (TP53, associated_with, breast cancer)

This is intentionally simple (regex over the sentence text, not a trained
relation-extraction model) but is enough to turn the knowledge graph from
a flat entity list into a real graph with labeled edges, and is easy to
swap for a BioBERT relation-classification head later.
"""
import re
from dataclasses import dataclass

RELATION_PATTERNS = [
    (r"\binhibits?\b", "inhibits"),
    (r"\bactivates?\b", "activates"),
    (r"\bsuppress(?:es)?\b", "suppresses"),
    (r"\bpromotes?\b", "promotes"),
    (r"\bregulat(?:es|ed|ion)\b", "regulates"),
    (r"\binteracts? with\b", "interacts_with"),
    (r"\bbinds? to\b|\bbinds?\b", "binds"),
    (r"\bassociated with\b", "associated_with"),
    (r"\bcauses?\b|\bincreases?\s+(?:the\s+)?risk\b|\bcontributes? to\b", "associated_with"),
]


@dataclass
class ExtractedRelation:
    source_text: str
    target_text: str
    relation_type: str
    sentence: str
    confidence: float = 0.6


def extract_relationships(entities: list, text_by_sentence: bool = True) -> list[ExtractedRelation]:
    """
    entities: list of ner.ExtractedEntity already grouped/sorted by
    sentence (as produced by ner.extract_entities).
    """
    relations: list[ExtractedRelation] = []

    # Group entities by the sentence they were found in.
    by_sentence: dict[str, list] = {}
    for e in entities:
        by_sentence.setdefault(e.sentence, []).append(e)

    for sentence, ents in by_sentence.items():
        if len(ents) < 2:
            continue
        pattern_hit = None
        for pattern, relation_type in RELATION_PATTERNS:
            if re.search(pattern, sentence, re.IGNORECASE):
                pattern_hit = relation_type
                break
        if not pattern_hit:
            continue

        # Naive pairing: connect every distinct entity pair in the
        # sentence with the detected relation type. Good enough for
        # short sentences typical of abstracts/result statements.
        seen_pairs = set()
        for i, a in enumerate(ents):
            for b in ents[i + 1:]:
                if a.text.upper() == b.text.upper():
                    continue
                pair_key = tuple(sorted([a.text.upper(), b.text.upper()]))
                if pair_key in seen_pairs:
                    continue
                seen_pairs.add(pair_key)
                relations.append(ExtractedRelation(
                    source_text=a.text,
                    target_text=b.text,
                    relation_type=pattern_hit,
                    sentence=sentence,
                    confidence=0.6,
                ))
    return relations

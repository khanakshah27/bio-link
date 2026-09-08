"""
Relationship extraction between BioLink entities that co-occur in the
same sentence.

Two backends:

1. LLM-powered (preferred). For each sentence with 2+ entities, asks
   Gemini to classify the biological relationship(s) between entity
   pairs directly from the sentence text. This catches phrasing a fixed
   verb list would miss entirely (passive voice, nominalizations like
   "loss of BRCA1 function", multi-clause sentences, negation) and was
   the explicit "Proposed Enhancement" in the original project brief:
   swap the pattern matcher for a model that actually reads the sentence.

2. Regex/pattern fallback (always available, zero dependencies). Looks
   for a fixed list of relational verbs ("inhibits", "interacts with",
   etc.) co-occurring with two or more entities in the sentence. Used
   whenever no GEMINI_API_KEY is configured, or the LLM call/response
   fails for a given sentence, so relationship extraction still works
   end-to-end offline.

    "TP53 inhibits MDM2"                -> (TP53, inhibits, MDM2)
    "BRCA1 interacts with RAD51"        -> (BRCA1, interacts_with, RAD51)
    "Mutations in TP53 ... increase breast cancer risk"
                                         -> (TP53, associated_with, breast cancer)
"""
import json
import re
from dataclasses import dataclass

from . import llm_client

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

# Controlled vocabulary the LLM is asked to classify into. Anything outside
# this set is dropped rather than persisted as an unvetted free-text label.
ALLOWED_RELATION_TYPES = {
    "inhibits", "activates", "suppresses", "promotes", "regulates",
    "interacts_with", "binds", "associated_with", "encodes", "causes",
    "part_of", "expressed_in",
}

# Sentences with more entities than this are skipped for the LLM path (the
# number of candidate pairs, and therefore prompt/response size, grows
# quadratically) and handled by the cheap regex fallback instead.
MAX_ENTITIES_PER_LLM_CALL = 8


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

    # Group entities by the sentence they were found in, deduping repeats
    # of the same surface text within one sentence.
    by_sentence: dict[str, list] = {}
    for e in entities:
        bucket = by_sentence.setdefault(e.sentence, [])
        if not any(existing.text.upper() == e.text.upper() for existing in bucket):
            bucket.append(e)

    for sentence, ents in by_sentence.items():
        if len(ents) < 2:
            continue

        sentence_relations = None
        if llm_client.is_available() and len(ents) <= MAX_ENTITIES_PER_LLM_CALL:
            sentence_relations = _llm_extract_for_sentence(sentence, ents)

        if sentence_relations is None:
            sentence_relations = _regex_extract_for_sentence(sentence, ents)

        relations.extend(sentence_relations)

    return relations


def _llm_extract_for_sentence(sentence: str, ents: list) -> list[ExtractedRelation] | None:
    """Returns None on any failure so the caller falls back to regex."""
    entity_list = ", ".join(f'"{e.text}" ({e.entity_type})' for e in ents)
    prompt = (
        "You are a biomedical relation extraction system. Given one sentence "
        "from a research paper and the entities found in it, identify which "
        "pairs of entities have a direct biological relationship stated or "
        "clearly implied by the sentence.\n\n"
        f"Sentence: \"{sentence}\"\n"
        f"Entities: {entity_list}\n\n"
        f"Allowed relation types: {', '.join(sorted(ALLOWED_RELATION_TYPES))}.\n\n"
        "Respond with ONLY a JSON array (no markdown fences, no prose). Each "
        'element must look like: {"source": "<entity text>", "target": '
        '"<entity text>", "relation": "<one allowed type>", "confidence": '
        "<0.0-1.0>}. Use entity texts exactly as given above. If no pair has "
        "a clear relationship, return []."
    )
    raw = llm_client.call_gemini(prompt, max_output_tokens=400, temperature=0.0)
    if raw is None:
        return None

    parsed = _parse_json_array(raw)
    if parsed is None:
        return None

    text_by_upper = {e.text.upper(): e.text for e in ents}
    results: list[ExtractedRelation] = []
    seen_pairs = set()
    for item in parsed:
        if not isinstance(item, dict):
            continue
        source = text_by_upper.get(str(item.get("source", "")).upper())
        target = text_by_upper.get(str(item.get("target", "")).upper())
        relation = str(item.get("relation", "")).strip().lower()
        if not source or not target or source.upper() == target.upper():
            continue
        if relation not in ALLOWED_RELATION_TYPES:
            continue
        try:
            confidence = float(item.get("confidence", 0.75))
        except (TypeError, ValueError):
            confidence = 0.75
        confidence = max(0.0, min(1.0, confidence))

        pair_key = (tuple(sorted([source.upper(), target.upper()])), relation)
        if pair_key in seen_pairs:
            continue
        seen_pairs.add(pair_key)
        results.append(ExtractedRelation(
            source_text=source,
            target_text=target,
            relation_type=relation,
            sentence=sentence,
            confidence=confidence,
        ))
    return results


def _parse_json_array(raw: str):
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?", "", cleaned).strip()
        cleaned = cleaned.rstrip("`").strip()
    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, list) else None


def _regex_extract_for_sentence(sentence: str, ents: list) -> list[ExtractedRelation]:
    pattern_hit = None
    for pattern, relation_type in RELATION_PATTERNS:
        if re.search(pattern, sentence, re.IGNORECASE):
            pattern_hit = relation_type
            break
    if not pattern_hit:
        return []

    # Naive pairing: connect every distinct entity pair in the sentence
    # with the detected relation type. Good enough for short sentences
    # typical of abstracts/result statements.
    relations = []
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

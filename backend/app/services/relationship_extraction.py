"""
Relationship extraction between BioLink entities that co-occur in the
same sentence.

Two backends:

1. LLM-powered (preferred). Sentences with 2+ entities are grouped into
   batches (see LLM_BATCH_SIZE) and each batch is sent to Gemini in a
   single call that asks it to classify the biological relationship(s)
   between entity pairs across every sentence in that batch. This reads
   the sentence rather than pattern-matching a fixed verb list, so it
   catches phrasing regex would miss entirely (passive voice,
   nominalizations like "loss of BRCA1 function", multi-clause sentences)
   - the explicit "Proposed Enhancement" in the original project brief.
   Batching many sentences per call (instead of one call per sentence)
   keeps the number of LLM requests per paper small, since Gemini's free
   tier has a fairly low requests-per-minute quota and a paper can easily
   have dozens of qualifying sentences.

2. Regex/pattern fallback (always available, zero dependencies). Looks
   for a fixed list of relational verbs ("inhibits", "interacts with",
   etc.) co-occurring with two or more entities in the sentence. Used
   whenever no GEMINI_API_KEY is configured, or an LLM batch call/response
   fails, so relationship extraction still works end-to-end offline.

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
MAX_ENTITIES_PER_SENTENCE = 8

# How many sentences go into one LLM call. Keeps requests-per-paper low
# (Gemini's free tier caps requests per minute) while keeping each
# request's prompt/response small enough to stay reliable.
LLM_BATCH_SIZE = 20


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
    # Group entities by the sentence they were found in, deduping repeats
    # of the same surface text within one sentence.
    by_sentence: dict[str, list] = {}
    for e in entities:
        bucket = by_sentence.setdefault(e.sentence, [])
        if not any(existing.text.upper() == e.text.upper() for existing in bucket):
            bucket.append(e)

    qualifying = [(s, ents) for s, ents in by_sentence.items() if len(ents) >= 2]
    if not qualifying:
        return []

    llm_eligible = [pair for pair in qualifying if len(pair[1]) <= MAX_ENTITIES_PER_SENTENCE]
    regex_only = [pair for pair in qualifying if len(pair[1]) > MAX_ENTITIES_PER_SENTENCE]

    relations: list[ExtractedRelation] = []

    if llm_client.is_available():
        for batch_start in range(0, len(llm_eligible), LLM_BATCH_SIZE):
            batch = llm_eligible[batch_start:batch_start + LLM_BATCH_SIZE]
            batch_relations = _llm_extract_for_batch(batch)
            if batch_relations is None:
                for sentence, ents in batch:
                    relations.extend(_regex_extract_for_sentence(sentence, ents))
            else:
                relations.extend(batch_relations)
    else:
        regex_only = llm_eligible + regex_only

    for sentence, ents in regex_only:
        relations.extend(_regex_extract_for_sentence(sentence, ents))

    return relations


def _llm_extract_for_batch(batch: list[tuple[str, list]]) -> list[ExtractedRelation] | None:
    """batch: list of (sentence, entities) pairs. Returns None on any
    failure so the caller falls back to regex for this batch."""
    sentence_blocks = []
    for i, (sentence, ents) in enumerate(batch):
        entity_list = ", ".join(f'"{e.text}" ({e.entity_type})' for e in ents)
        sentence_blocks.append(f'{i}. Sentence: "{sentence}"\n   Entities: {entity_list}')

    prompt = (
        "You are a biomedical relation extraction system. For EACH numbered "
        "sentence below, identify which pairs of its listed entities have a "
        "direct biological relationship stated or clearly implied by that "
        "sentence.\n\n"
        + "\n\n".join(sentence_blocks) + "\n\n"
        f"Allowed relation types: {', '.join(sorted(ALLOWED_RELATION_TYPES))}.\n\n"
        "Respond with ONLY a JSON array (no markdown fences, no prose). Each "
        'element must look like: {"sentence_index": <int>, "source": '
        '"<entity text>", "target": "<entity text>", "relation": "<one '
        'allowed type>", "confidence": <0.0-1.0>}. Use entity texts exactly '
        "as given for that sentence. Omit sentences/pairs with no clear "
        "relationship. If nothing qualifies anywhere, return []."
    )
    # Response grows with batch size; give it enough room per sentence.
    max_tokens = min(4000, 200 * len(batch) + 200)
    raw, _error = llm_client.call_gemini_verbose(prompt, max_output_tokens=max_tokens, temperature=0.0)
    if raw is None:
        return None

    parsed = _parse_json_array(raw)
    if parsed is None:
        return None

    results: list[ExtractedRelation] = []
    seen_pairs: dict[int, set] = {}
    for item in parsed:
        if not isinstance(item, dict):
            continue
        try:
            idx = int(item.get("sentence_index"))
        except (TypeError, ValueError):
            continue
        if idx < 0 or idx >= len(batch):
            continue
        sentence, ents = batch[idx]

        text_by_upper = {e.text.upper(): e.text for e in ents}
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
        if pair_key in seen_pairs.setdefault(idx, set()):
            continue
        seen_pairs[idx].add(pair_key)
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

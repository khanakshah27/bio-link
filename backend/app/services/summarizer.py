"""
Generates the "AI-generated biological summary" panel.

Default implementation is a dependency-free extractive summarizer: it
scores each sentence by how many distinct high-confidence entities it
mentions plus its position in the document, then stitches the top
sentences back together in original order. This needs no API key and
runs offline.

If ANTHROPIC_API_KEY is set in the environment, summarize() instead asks
Claude for a short abstractive summary grounded in the extracted
entities/relationships, which reads more naturally. Both paths return
plain text.
"""
import os
from .pdf_extract import split_sentences


def _extractive_summary(text: str, entities: list, max_sentences: int = 4) -> str:
    sentences = split_sentences(text)
    if not sentences:
        return "No summary available: the document contained no extractable text."

    entity_texts_by_sentence: dict[int, set] = {}
    for i, sent in enumerate(sentences):
        hits = {e.text.upper() for e in entities if e.sentence.strip() == sent.strip()}
        entity_texts_by_sentence[i] = hits

    scored = []
    for i, sent in enumerate(sentences):
        entity_score = len(entity_texts_by_sentence.get(i, set()))
        position_score = 1.0 if i < 2 else 0.3 if i < len(sentences) * 0.3 else 0.0
        length_penalty = 0.0 if 40 <= len(sent) <= 320 else -0.5
        scored.append((entity_score + position_score + length_penalty, i, sent))

    top = sorted(scored, key=lambda t: t[0], reverse=True)[:max_sentences]
    top_in_order = [s for _, i, s in sorted(top, key=lambda t: t[1])]

    if not top_in_order:
        return " ".join(sentences[:3])
    return " ".join(top_in_order)


def _claude_summary(text: str, entities: list, relations: list) -> str | None:
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        return None
    try:
        import anthropic
    except ImportError:
        return None

    gene_list = sorted({e.text for e in entities if e.entity_type == "gene"})
    disease_list = sorted({e.text for e in entities if e.entity_type == "disease"})
    pathway_list = sorted({e.text for e in entities if e.entity_type == "pathway"})
    relation_lines = [f"{r.source_text} {r.relation_type} {r.target_text}" for r in relations[:15]]

    prompt = (
        "Write a concise 3-4 sentence biological summary of the research paper "
        "excerpt below for a researcher's dashboard. Ground every claim in the "
        "provided entities/relationships; do not invent findings.\n\n"
        f"Genes: {', '.join(gene_list) or 'none detected'}\n"
        f"Diseases: {', '.join(disease_list) or 'none detected'}\n"
        f"Pathways: {', '.join(pathway_list) or 'none detected'}\n"
        f"Extracted relationships: {'; '.join(relation_lines) or 'none detected'}\n\n"
        f"Paper text (truncated):\n{text[:4000]}"
    )
    try:
        client = anthropic.Anthropic(api_key=api_key)
        resp = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=300,
            messages=[{"role": "user", "content": prompt}],
        )
        parts = [b.text for b in resp.content if getattr(b, "type", None) == "text"]
        return "\n".join(parts).strip() or None
    except Exception:
        return None


def summarize(text: str, entities: list, relations: list) -> str:
    claude_summary = _claude_summary(text, entities, relations)
    if claude_summary:
        return claude_summary
    return _extractive_summary(text, entities)

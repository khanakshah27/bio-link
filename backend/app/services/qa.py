"""
"Ask Bio-Link": retrieval-augmented Q&A over one paper's already-extracted
knowledge (entities, relationships, linked database records).

This is deliberately not free-form chat: the LLM is given a structured
digest of exactly what the pipeline extracted for this paper and told to
answer only from that, so a question like "which genes are linked to DNA
repair?" is answered from the paper's own extracted entities/relationships
rather than the model's general biomedical knowledge.

Takes a PaperOut-shaped object (works for both a real Paper loaded from
the DB, via schemas.PaperOut, and the frontend's client-only demo
dataset), so there's no separate DB round trip for this endpoint.
"""
from . import llm_client

# Caps keep the prompt (and therefore latency/cost) bounded for papers with
# an unusually large number of extracted entities/relationships.
MAX_ENTITIES_IN_CONTEXT = 200
MAX_RELATIONSHIPS_IN_CONTEXT = 150


def _build_context(paper) -> str:
    lines = [f"Paper: {paper.filename}"]
    if paper.summary:
        lines.append(f"Summary: {paper.summary}")

    entities = list(paper.entities or [])[:MAX_ENTITIES_IN_CONTEXT]
    lines.append("\nExtracted entities (surface text [type] normalized-id: evidence sentence):")
    for e in entities:
        normalized = f" ({e.normalized_id})" if e.normalized_id else ""
        evidence = f': "{e.sentence}"' if e.sentence else ""
        lines.append(f"- {e.text} [{e.entity_type}]{normalized}{evidence}")

    entity_by_id = {e.id: e for e in entities}
    relationships = list(paper.relationships or [])[:MAX_RELATIONSHIPS_IN_CONTEXT]
    if relationships:
        lines.append("\nExtracted relationships:")
        for r in relationships:
            src = entity_by_id.get(r.source_entity_id)
            tgt = entity_by_id.get(r.target_entity_id)
            if not src or not tgt:
                continue
            evidence = f' ("{r.sentence}")' if r.sentence else ""
            lines.append(f"- {src.text} {r.relation_type.replace('_', ' ')} {tgt.text}{evidence}")

    db_lines = []
    for e in entities:
        for rec in (e.records or []):
            if rec.status not in ("ok", "offline_fallback"):
                continue
            db_lines.append(f"- {e.text}: found in {rec.source} ({rec.status})")
    if db_lines:
        lines.append("\nLinked external database records:")
        lines.extend(db_lines)

    return "\n".join(lines)


def answer_question(paper, question: str) -> dict:
    if not llm_client.is_available():
        return {
            "answer": (
                "Ask Bio-Link needs a GEMINI_API_KEY configured on the "
                "backend to answer questions. Set it in your .env and "
                "restart the server."
            ),
            "grounded": False,
        }

    context = _build_context(paper)
    prompt = (
        "You are Bio-Link's research assistant. Answer the user's question "
        "using ONLY the extracted knowledge below, which comes from one "
        "biomedical paper's automated entity/relationship extraction "
        "pipeline. Do not use outside biomedical knowledge and do not "
        "invent entities, genes, or relationships that are not listed "
        "below. If the extracted knowledge doesn't contain the answer, say "
        "so plainly rather than guessing. Refer to entities by the exact "
        "text given. Keep the answer concise (2-5 sentences, or a short "
        "list if the question asks for multiple items).\n\n"
        f'=== Extracted knowledge for "{paper.filename}" ===\n{context}\n'
        "=== End of extracted knowledge ===\n\n"
        f"Question: {question}"
    )
    answer, error = llm_client.call_gemini_verbose(prompt, max_output_tokens=500, temperature=0.1)
    if not answer:
        return {
            "answer": (
                "Sorry, I couldn't reach the language model to answer that "
                f"just now ({error}). Please try again in a moment."
            ),
            "grounded": False,
        }
    return {"answer": answer, "grounded": True}

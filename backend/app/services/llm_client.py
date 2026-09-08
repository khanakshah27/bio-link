"""
Shared Gemini REST client.

Three features in this codebase call an LLM — the paper summary
(summarizer.py), relationship extraction (relationship_extraction.py), and
the "Ask Bio-Link" Q&A chat (qa.py) — and all three should behave
identically when no key is configured (fail soft, fall back to a
non-LLM path) or when the request errors out (timeout, rate limit, bad
response shape). Centralizing the HTTP call here keeps that behavior in
one place instead of three.
"""
import os
import requests

GEMINI_MODEL = "gemini-2.5-flash"
GEMINI_URL = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent"


def is_available() -> bool:
    return bool(os.getenv("GEMINI_API_KEY"))


def call_gemini(prompt: str, max_output_tokens: int = 300, temperature: float = 0.2) -> str | None:
    """Returns the model's text response, or None on any failure (including
    a missing API key) so callers can fall back to a non-LLM path."""
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        return None
    try:
        resp = requests.post(
            GEMINI_URL,
            params={"key": api_key},
            json={
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {
                    "maxOutputTokens": max_output_tokens,
                    "temperature": temperature,
                    # gemini-2.5-flash "thinks" before answering by default,
                    # and can burn the entire maxOutputTokens budget on
                    # internal reasoning with nothing left for the actual
                    # answer (content.parts comes back empty/missing) for
                    # these short, deterministic extraction/QA tasks that
                    # don't need multi-step reasoning. Disable it.
                    "thinkingConfig": {"thinkingBudget": 0},
                },
            },
            timeout=20,
        )
        resp.raise_for_status()
        data = resp.json()
        parts = data["candidates"][0]["content"]["parts"]
        text = "".join(p.get("text", "") for p in parts).strip()
        return text or None
    except Exception:
        return None

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
import time
import requests

GEMINI_MODEL = "gemini-2.5-flash"
GEMINI_URL = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent"

# Gemini's free tier has a fairly low requests-per-minute quota. A single
# retry with backoff smooths over a brief burst (e.g. relationship
# extraction's last batch for a paper landing right before an Ask Bio-Link
# question) without adding much latency; it isn't meant to paper over
# sustained quota exhaustion.
MAX_429_RETRIES = 1
DEFAULT_RETRY_DELAY_SECONDS = 5
MAX_RETRY_DELAY_SECONDS = 15


def is_available() -> bool:
    return bool(os.getenv("GEMINI_API_KEY"))


def call_gemini(prompt: str, max_output_tokens: int = 300, temperature: float = 0.2) -> str | None:
    """Returns the model's text response, or None on any failure (including
    a missing API key) so callers can fall back to a non-LLM path."""
    text, _error = call_gemini_verbose(prompt, max_output_tokens, temperature)
    return text


def call_gemini_verbose(
    prompt: str, max_output_tokens: int = 300, temperature: float = 0.2
) -> tuple[str | None, str | None]:
    """Same as call_gemini, but also returns a short, non-sensitive reason
    string on failure (never includes the API key) so a caller that wants
    to surface *why* the LLM call failed - e.g. the Ask Bio-Link chat,
    where a silent generic error is a dead end for the user/deployer - can
    do so instead of just getting None back."""
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        return None, "GEMINI_API_KEY is not set on the backend"

    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "maxOutputTokens": max_output_tokens,
            "temperature": temperature,
            # gemini-2.5-flash "thinks" before answering by default, and can
            # burn the entire maxOutputTokens budget on internal reasoning
            # with nothing left for the actual answer (content.parts comes
            # back empty/missing) for these short, deterministic
            # extraction/QA tasks that don't need multi-step reasoning.
            "thinkingConfig": {"thinkingBudget": 0},
        },
    }

    resp = None
    for attempt in range(MAX_429_RETRIES + 1):
        try:
            resp = requests.post(GEMINI_URL, params={"key": api_key}, json=payload, timeout=20)
        except requests.exceptions.RequestException as exc:
            return None, f"network error calling Gemini ({type(exc).__name__})"

        if resp.status_code == 429 and attempt < MAX_429_RETRIES:
            delay = _retry_delay_seconds(resp) or DEFAULT_RETRY_DELAY_SECONDS
            time.sleep(min(delay, MAX_RETRY_DELAY_SECONDS))
            continue
        break

    if not resp.ok:
        return None, f"Gemini API returned HTTP {resp.status_code}: {resp.text[:300]}"

    try:
        data = resp.json()
    except ValueError:
        return None, "Gemini API returned a non-JSON response"

    candidates = data.get("candidates") or []
    if not candidates:
        feedback = data.get("promptFeedback")
        return None, f"Gemini API returned no candidates (promptFeedback={feedback})"

    parts = (candidates[0].get("content") or {}).get("parts")
    if not parts:
        finish_reason = candidates[0].get("finishReason", "unknown")
        return None, f"Gemini API returned no content parts (finishReason={finish_reason})"

    text = "".join(p.get("text", "") for p in parts).strip()
    if not text:
        return None, "Gemini API returned an empty text response"
    return text, None


def _retry_delay_seconds(resp) -> float | None:
    """Gemini's 429 body includes a RetryInfo detail with the server's
    suggested wait (e.g. "34s"); use it when present instead of guessing."""
    try:
        details = (resp.json().get("error") or {}).get("details") or []
    except ValueError:
        return None
    for d in details:
        if str(d.get("@type", "")).endswith("RetryInfo"):
            delay = str(d.get("retryDelay", ""))
            if delay.endswith("s"):
                try:
                    return float(delay[:-1])
                except ValueError:
                    return None
    return None

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

# Override with GEMINI_MODEL. If the configured model is retired/unknown
# (HTTP 404), the others are tried in order so the AI features keep working.
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
FALLBACK_MODELS = ["gemini-flash-latest", "gemini-2.0-flash"]
GEMINI_URL_TEMPLATE = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

# Gemini's free tier has a fairly low requests-per-minute quota. A single
# retry with backoff smooths over a brief burst (e.g. relationship
# extraction's last batch for a paper landing right before an Ask Bio-Link
# question) without adding much latency; it isn't meant to paper over
# sustained quota exhaustion.
MAX_429_RETRIES = 1
REQUEST_TIMEOUT_SECONDS = 45
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
    api_key = (os.getenv("GEMINI_API_KEY") or "").strip()
    if not api_key:
        return None, "GEMINI_API_KEY is not set on the backend"

    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "maxOutputTokens": max_output_tokens,
            "temperature": temperature,
            # gemini-2.5-flash "thinks" by default and can burn the whole
            # output budget on reasoning, leaving no answer text.
            "thinkingConfig": {"thinkingBudget": 0},
        },
    }

    resp = None
    network_error = None
    models_to_try = [os.getenv("GEMINI_MODEL", GEMINI_MODEL)] + [
        m for m in FALLBACK_MODELS if m != os.getenv("GEMINI_MODEL", GEMINI_MODEL)
    ]
    for model in models_to_try:
        url = GEMINI_URL_TEMPLATE.format(model=model)
        for attempt in range(MAX_429_RETRIES + 1):
            try:
                # Key goes in a header, not the URL, so it can't leak via logs.
                resp = requests.post(
                    url, headers={"x-goog-api-key": api_key}, json=payload, timeout=REQUEST_TIMEOUT_SECONDS
                )
            except requests.exceptions.RequestException as exc:
                # A slow/overloaded model shouldn't end the request: move on
                # to the next model, and only report the error if none work.
                resp = None
                network_error = f"network error calling Gemini ({type(exc).__name__})"
                break

            # Models without thinking support reject thinkingConfig.
            if resp.status_code == 400 and "thinking" in resp.text.lower() \
                    and "thinkingConfig" in payload["generationConfig"]:
                del payload["generationConfig"]["thinkingConfig"]
                continue
            if resp.status_code == 429 and attempt < MAX_429_RETRIES:
                delay = _retry_delay_seconds(resp) or DEFAULT_RETRY_DELAY_SECONDS
                time.sleep(min(delay, MAX_RETRY_DELAY_SECONDS))
                continue
            break
        # Free-tier quotas are tracked per model, so when one model is
        # missing (404) or out of per-minute quota (429), try the next.
        if resp is not None and resp.status_code not in (404, 429):
            break

    if resp is None:
        return None, network_error
    if resp.status_code == 429:
        try:
            detail = (resp.json().get("error") or {}).get("message", "")
        except ValueError:
            detail = ""
        detail = " ".join(detail.split())[:240]
        return None, (
            "the AI service is rate-limited right now (Gemini quota)"
            + (f" - Google says: {detail}" if detail else "")
        )
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

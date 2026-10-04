"""Provider-neutral AI analysis. Pick the provider with AI_PROVIDER in .env."""
import base64
import json
import random
import time
import urllib.error
import urllib.request

from pydantic import ValidationError

from app.core.config import settings
from app.models.analysis import AnalysisResult
from app.services.errors import (
    AIMalformedResponseError,
    AINotConfiguredError,
    AIProviderError,
)
from app.services.prompts import (
    IMAGE_EXTRACTION_PROMPT,
    NO_TEXT_MARKER,
    SYSTEM_PROMPT,
    build_user_prompt,
)


def _call_openai(text: str) -> str:
    from openai import OpenAI, OpenAIError  # imported lazily: only needed if used

    try:
        client = OpenAI(api_key=settings.openai_api_key, timeout=60)
        resp = client.chat.completions.create(
            model=settings.openai_model,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": build_user_prompt(text)},
            ],
            response_format={"type": "json_object"},
            temperature=0,
        )
        return resp.choices[0].message.content or ""
    except OpenAIError as e:
        raise AIProviderError(f"OpenAI request failed: {e}")


GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
GEMINI_MAX_RETRIES = 4
GEMINI_BACKOFF_BASE = 1.0  # seconds
GEMINI_MAX_RETRY_DELAY = 60  # a 429 asking us to wait longer than this is not worth retrying


def _read_error(e: urllib.error.HTTPError) -> dict:
    """The "error" object from a Gemini error body ({} if it can't be read)."""
    try:
        return json.loads(e.read().decode()).get("error", {}) or {}
    except Exception:
        return {}


def _is_quota_exhausted(err: dict) -> bool:
    """True for a 429 that waiting a few seconds will not fix: a daily quota
    (e.g. free tier limit per day) or a retryDelay longer than we are willing to wait."""
    for detail in err.get("details") or []:
        for v in detail.get("violations") or []:
            quota_id = str(v.get("quotaId", ""))
            if "PerDay" in quota_id or "Daily" in quota_id:
                return True
        delay = str(detail.get("retryDelay", "")).rstrip("s")
        try:
            if float(delay) > GEMINI_MAX_RETRY_DELAY:
                return True
        except ValueError:
            pass
    return False


def _is_transient(code: int, err: dict) -> bool:
    """5xx and short-term 429 rate limits are worth retrying. Quota exhaustion is not."""
    return code >= 500 or (code == 429 and not _is_quota_exhausted(err))


def _gemini_generate(payload: dict, timeout: int = 60) -> str:
    """POST a generateContent payload and return the reply text.
    Plain REST (standard library only). The key goes in a header, never in
    the URL, so it cannot leak into error messages.

    Transient provider errors (5xx and short-term 429 rate limits) are retried up to GEMINI_MAX_RETRIES
    times with exponential backoff plus a little jitter. Other errors, including
    quota-exhausted 429s, fail at once."""
    request = urllib.request.Request(
        GEMINI_URL.format(model=settings.gemini_model),
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", "x-goog-api-key": settings.gemini_api_key or ""},
        method="POST",
    )
    body = None
    for attempt in range(GEMINI_MAX_RETRIES + 1):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as resp:
                body = json.loads(resp.read().decode())
            break
        except urllib.error.HTTPError as e:
            err = _read_error(e)  # the body can only be read once
            if _is_transient(e.code, err) and attempt < GEMINI_MAX_RETRIES:
                # 1s, 2s, 4s, 8s (+ up to 0.5s jitter)
                time.sleep(GEMINI_BACKOFF_BASE * 2**attempt + random.uniform(0, 0.5))
                continue
            retried = f" (after {attempt} retries)" if attempt else ""
            raise AIProviderError(f"Gemini request failed: HTTP {e.code} {_gemini_error_message(err, e.reason)}{retried}")
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            raise AIProviderError(f"Gemini request failed: could not reach the API ({e})")
        except ValueError:
            raise AIProviderError("Gemini request failed: response was not valid JSON.")

    # Join the text parts of the first candidate (skipping any "thought" parts).
    candidates = body.get("candidates") or [{}]
    parts = (candidates[0].get("content") or {}).get("parts") or []
    return "".join(p.get("text", "") for p in parts if not p.get("thought"))


def _call_gemini(text: str) -> str:
    return _gemini_generate({
        "systemInstruction": {"parts": [{"text": SYSTEM_PROMPT}]},
        "contents": [{"role": "user", "parts": [{"text": build_user_prompt(text)}]}],
        "generationConfig": {"responseMimeType": "application/json", "temperature": 0},
    })


def gemini_configured() -> bool:
    """Image/scanned-PDF reading always uses Gemini, whichever provider analyzes text."""
    return bool(settings.gemini_api_key)


def extract_text_from_images(images: list[tuple[bytes, str]]) -> str:
    """Send one or more (bytes, mime_type) images to Gemini vision and return
    the text/description it reads. Returns "" if nothing was found."""
    if not gemini_configured():
        raise AINotConfiguredError(
            "Image reading is not configured: set GEMINI_API_KEY in backend/.env."
        )
    parts: list[dict] = [{"text": IMAGE_EXTRACTION_PROMPT}]
    for data, mime in images:
        parts.append({"inlineData": {"mimeType": mime, "data": base64.b64encode(data).decode()}})
    reply = _gemini_generate(
        {"contents": [{"role": "user", "parts": parts}], "generationConfig": {"temperature": 0}},
        timeout=90,
    ).strip()
    return "" if reply == NO_TEXT_MARKER else reply


def _gemini_error_message(err: dict, reason: str) -> str:
    return f"{err.get('status', reason)}: {err.get('message', '')}".strip()


# provider name -> (function returning its API key, env var name, call function)
_PROVIDERS = {
    "openai": (lambda: settings.openai_api_key, "OPENAI_API_KEY", _call_openai),
    "gemini": (lambda: settings.gemini_api_key, "GEMINI_API_KEY", _call_gemini),
}


def _check_references(result: AnalysisResult) -> None:
    ids = [e.id for e in result.entities]
    if len(ids) != len(set(ids)):
        raise AIMalformedResponseError("AI returned duplicate entity ids.")
    known = set(ids)
    for r in result.relationships:
        if r.source not in known or r.target not in known:
            raise AIMalformedResponseError(
                "AI returned a relationship that points to an unknown entity."
            )


def analyze_text(text: str) -> AnalysisResult:
    provider = _PROVIDERS.get(settings.ai_provider)
    if provider is None:
        raise AINotConfiguredError(
            f"Unknown AI_PROVIDER '{settings.ai_provider}'. Use 'openai' or 'gemini'."
        )
    get_key, key_name, call = provider
    if not get_key():
        raise AINotConfiguredError(
            f"AI is not configured: set {key_name} in backend/.env "
            f"(AI_PROVIDER={settings.ai_provider})."
        )

    raw = call(text)
    try:
        result = AnalysisResult.model_validate_json(raw)
    except ValidationError:
        raise AIMalformedResponseError(
            "The AI response did not match the expected format. Please try again."
        )
    _check_references(result)
    return result

"""Provider-neutral AI analysis. Pick the provider with AI_PROVIDER in .env."""
import base64
import json
import logging
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

logger = logging.getLogger("nodex.ai")


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
GEMINI_MAX_RETRIES = 4  # no Groq backup: retrying is the only way to recover
GEMINI_MAX_RETRIES_WITH_BACKUP = 1  # Groq backup configured: one quick retry, then fail over
GEMINI_BACKOFF_BASE = 1.0  # seconds
# Per-attempt request timeouts (seconds). Shorter when a Groq backup can take over sooner.
GEMINI_TEXT_TIMEOUT, GEMINI_VISION_TIMEOUT = 60, 90
GEMINI_TEXT_TIMEOUT_WITH_BACKUP, GEMINI_VISION_TIMEOUT_WITH_BACKUP = 40, 60
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

    Transient provider errors (5xx and short-term 429 rate limits) are retried with exponential
    backoff plus a little jitter: up to GEMINI_MAX_RETRIES times, or just
    GEMINI_MAX_RETRIES_WITH_BACKUP time when a Groq backup is configured (fail over sooner).
    Other errors, including quota-exhausted 429s, timeouts and network failures, fail at once."""
    request = urllib.request.Request(
        GEMINI_URL.format(model=settings.gemini_model),
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", "x-goog-api-key": settings.gemini_api_key or ""},
        method="POST",
    )
    max_retries = GEMINI_MAX_RETRIES_WITH_BACKUP if groq_configured() else GEMINI_MAX_RETRIES
    body = None
    for attempt in range(max_retries + 1):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as resp:
                body = json.loads(resp.read().decode())
            break
        except urllib.error.HTTPError as e:
            err = _read_error(e)  # the body can only be read once
            if _is_transient(e.code, err) and attempt < max_retries:
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


def _gemini_timeouts() -> tuple[int, int]:
    """(text, vision) request timeouts: shorter when a Groq backup is configured."""
    if groq_configured():
        return GEMINI_TEXT_TIMEOUT_WITH_BACKUP, GEMINI_VISION_TIMEOUT_WITH_BACKUP
    return GEMINI_TEXT_TIMEOUT, GEMINI_VISION_TIMEOUT


def _call_gemini(text: str) -> str:
    return _gemini_generate({
        "systemInstruction": {"parts": [{"text": SYSTEM_PROMPT}]},
        "contents": [{"role": "user", "parts": [{"text": build_user_prompt(text)}]}],
        "generationConfig": {"responseMimeType": "application/json", "temperature": 0},
    }, timeout=_gemini_timeouts()[0])


def gemini_configured() -> bool:
    """Image/scanned-PDF reading always uses Gemini, whichever provider analyzes text."""
    return bool(settings.gemini_api_key)


# ---------------------------------------------------------------------------
# Groq: backup provider (OpenAI-compatible chat completions API).
# One attempt only: Gemini already retries, and a Groq failure is the end of the line.
# ---------------------------------------------------------------------------
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"


def groq_configured() -> bool:
    return bool(settings.groq_api_key)


def _redact(text: str) -> str:
    """Remove any configured API key from text before it is logged or put in an error."""
    for secret in (settings.gemini_api_key, settings.groq_api_key, settings.openai_api_key):
        if secret:
            text = text.replace(secret, "<redacted>")
    return text


def _groq_error_message(e: urllib.error.HTTPError) -> str:
    try:
        err = json.loads(e.read().decode()).get("error", {}) or {}
        return f"{err.get('type') or e.reason}: {err.get('message', '')}".strip()
    except Exception:
        return str(e.reason)


def _groq_generate(messages: list[dict], timeout: int = 60, json_mode: bool = False) -> str:
    """POST a chat completion to Groq and return the reply text. The key goes in the
    Authorization header only (never the URL)."""
    body: dict = {"model": settings.groq_model, "messages": messages, "temperature": 0}
    if json_mode:
        body["response_format"] = {"type": "json_object"}
    request = urllib.request.Request(
        GROQ_URL,
        data=json.dumps(body).encode(),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {settings.groq_api_key or ''}",
            "User-Agent": "nodex-backend/1.0",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as resp:
            data = json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        raise AIProviderError(_redact(f"Groq request failed: HTTP {e.code} {_groq_error_message(e)}"))
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise AIProviderError(_redact(f"Groq request failed: could not reach the API ({e})"))
    except ValueError:
        raise AIProviderError("Groq request failed: response was not valid JSON.")
    choices = data.get("choices") or [{}]
    return (choices[0].get("message") or {}).get("content") or ""


def _call_groq(text: str) -> str:
    return _groq_generate(
        [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": build_user_prompt(text)},
        ],
        json_mode=True,
    )


def _groq_extract_text_from_images(images: list[tuple[bytes, str]]) -> str:
    content: list[dict] = [{"type": "text", "text": IMAGE_EXTRACTION_PROMPT}]
    for data, mime in images:
        url = f"data:{mime};base64,{base64.b64encode(data).decode()}"
        content.append({"type": "image_url", "image_url": {"url": url}})
    return _groq_generate([{"role": "user", "content": content}], timeout=90)


# Shown to the frontend when Gemini AND Groq both fail: no provider names, codes or keys.
BOTH_FAILED_ANALYSIS = (
    "AI analysis failed: both the primary and the backup AI provider were unable "
    "to complete the request. Please try again later."
)
BOTH_FAILED_VISION = (
    "Reading the file failed: both the primary and the backup AI provider were unable "
    "to read it. Please try again later."
)


def _gemini_extract_text_from_images(images: list[tuple[bytes, str]]) -> str:
    parts: list[dict] = [{"text": IMAGE_EXTRACTION_PROMPT}]
    for data, mime in images:
        parts.append({"inlineData": {"mimeType": mime, "data": base64.b64encode(data).decode()}})
    return _gemini_generate(
        {"contents": [{"role": "user", "parts": parts}], "generationConfig": {"temperature": 0}},
        timeout=_gemini_timeouts()[1],
    )


def extract_text_from_images(images: list[tuple[bytes, str]]) -> str:
    """Send one or more (bytes, mime_type) images to Gemini vision and return
    the text/description it reads. Returns "" if nothing was found.
    If Gemini fails and Groq is configured, Groq reads the same images instead."""
    if not gemini_configured():
        raise AINotConfiguredError(
            "Image reading is not configured: set GEMINI_API_KEY in backend/.env."
        )
    try:
        reply = _gemini_extract_text_from_images(images)
        # HTTP 200 with an empty reply (e.g. the answer was blocked) is a failed read, not
        # "no text": a genuine empty page is reported with the explicit NO_TEXT_FOUND marker.
        if not reply.strip() and groq_configured():
            raise AIProviderError("Gemini returned an empty response.")
    except AIProviderError as primary_error:
        if not groq_configured():
            raise
        logger.warning("Gemini image reading failed (%s); trying Groq.", _redact(primary_error.message))
        try:
            reply = _groq_extract_text_from_images(images)
        except AIProviderError as backup_error:
            logger.error("Groq image reading also failed (%s).", _redact(backup_error.message))
            raise AIProviderError(BOTH_FAILED_VISION) from None
    reply = reply.strip()
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


def _parse_result(raw: str) -> AnalysisResult:
    try:
        result = AnalysisResult.model_validate_json(raw)
    except ValidationError:
        raise AIMalformedResponseError(
            "The AI response did not match the expected format. Please try again."
        )
    _check_references(result)
    return result


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

    try:
        return _parse_result(call(text))
    except (AIProviderError, AIMalformedResponseError) as primary_error:
        # Failover: Gemini is primary; Groq only runs after Gemini failed
        # (unavailable, rate limit/quota, timeout, network, or unusable output).
        if settings.ai_provider != "gemini" or not groq_configured():
            raise
        logger.warning("Gemini analysis failed (%s); trying Groq.", _redact(primary_error.message))
        try:
            return _parse_result(_call_groq(text))
        except (AIProviderError, AIMalformedResponseError) as backup_error:
            logger.error("Groq analysis also failed (%s).", _redact(backup_error.message))
            raise AIProviderError(BOTH_FAILED_ANALYSIS) from None

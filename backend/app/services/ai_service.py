"""Provider-neutral AI analysis. Pick the provider with AI_PROVIDER in .env."""
import json
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
from app.services.prompts import SYSTEM_PROMPT, build_user_prompt


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


def _call_gemini(text: str) -> str:
    # Plain REST call (standard library only). The key goes in a header, never in
    # the URL, so it cannot leak into error messages.
    payload = {
        "systemInstruction": {"parts": [{"text": SYSTEM_PROMPT}]},
        "contents": [{"role": "user", "parts": [{"text": build_user_prompt(text)}]}],
        "generationConfig": {"responseMimeType": "application/json", "temperature": 0},
    }
    request = urllib.request.Request(
        GEMINI_URL.format(model=settings.gemini_model),
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", "x-goog-api-key": settings.gemini_api_key or ""},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as resp:
            body = json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        raise AIProviderError(f"Gemini request failed: HTTP {e.code} {_gemini_error_message(e)}")
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise AIProviderError(f"Gemini request failed: could not reach the API ({e})")
    except ValueError:
        raise AIProviderError("Gemini request failed: response was not valid JSON.")

    # Join the text parts of the first candidate (skipping any "thought" parts).
    candidates = body.get("candidates") or [{}]
    parts = (candidates[0].get("content") or {}).get("parts") or []
    return "".join(p.get("text", "") for p in parts if not p.get("thought"))


def _gemini_error_message(e: urllib.error.HTTPError) -> str:
    try:
        err = json.loads(e.read().decode()).get("error", {})
        return f"{err.get('status', e.reason)}: {err.get('message', '')}".strip()
    except Exception:
        return str(e.reason)


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

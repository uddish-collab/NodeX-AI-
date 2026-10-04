"""Provider-neutral AI analysis. Pick the provider with AI_PROVIDER in .env."""
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


def _call_gemini(text: str) -> str:
    from google import genai
    from google.genai import errors, types

    try:
        client = genai.Client(api_key=settings.gemini_api_key)
        resp = client.models.generate_content(
            model=settings.gemini_model,
            contents=build_user_prompt(text),
            config=types.GenerateContentConfig(
                system_instruction=SYSTEM_PROMPT,
                response_mime_type="application/json",
                temperature=0,
            ),
        )
        return resp.text or ""
    except errors.APIError as e:
        raise AIProviderError(f"Gemini request failed: {e}")


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

"""Tests for the Gemini HTTP path. urllib is mocked: NO real API calls are made.
Run from backend/:  python tests/test_gemini_http.py
"""
import io
import json
import os
import sys
import tempfile
import urllib.error
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ["DB_PATH"] = os.path.join(tempfile.mkdtemp(), "test.db")
os.environ["AI_PROVIDER"] = "gemini"
os.environ["GEMINI_API_KEY"] = "test-key-123"
os.environ["GEMINI_MODEL"] = "test-model"

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402
from app.services import ai_service  # noqa: E402

ai_service.time.sleep = lambda s: None  # retries must not really wait in tests

GOOD = {
    "entities": [{"id": "e1", "name": "NodeX", "type": "project"},
                 {"id": "e2", "name": "AI API", "type": "technology"}],
    "relationships": [{"source": "e1", "target": "e2", "relationship": "uses",
                       "explanation": "NodeX uses an AI API.", "confidence": 0.9}],
    "source_summary": "About NodeX.",
}


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def gemini_reply(text: str, with_thought: bool = False) -> FakeResponse:
    parts = [{"text": text}]
    if with_thought:
        parts.insert(0, {"text": "thinking...", "thought": True})
    return FakeResponse(json.dumps({"candidates": [{"content": {"parts": parts}}]}).encode())


def http_error(code: int, status: str, message: str) -> urllib.error.HTTPError:
    body = json.dumps({"error": {"code": code, "status": status, "message": message}}).encode()
    return urllib.error.HTTPError("https://x", code, status, {}, io.BytesIO(body))


passed = 0


def check(name: str, cond: bool) -> None:
    global passed
    print(("PASS " if cond else "FAIL ") + name)
    if not cond:
        sys.exit(1)
    passed += 1


URLOPEN = "app.services.ai_service.urllib.request.urlopen"

with TestClient(app) as c:
    body = {"text": "NodeX uses an AI API."}

    with mock.patch(URLOPEN, return_value=gemini_reply(json.dumps(GOOD), with_thought=True)) as m:
        r = c.post("/analyze", json=body)
        req = m.call_args.args[0]
        sent = json.loads(req.data)
        check("success -> 200 with nodes and edges", r.status_code == 200 and len(r.json()["nodes"]) == 2 and len(r.json()["edges"]) == 1)
        check("thought parts are ignored", r.status_code == 200)
        check("URL uses configured model", "/models/test-model:generateContent" in req.full_url)
        check("API key is in header, not URL", req.get_header("X-goog-api-key") == "test-key-123" and "test-key-123" not in req.full_url)
        check("payload has system prompt, json mime type, temperature 0",
              sent["generationConfig"] == {"responseMimeType": "application/json", "temperature": 0}
              and "NodeX" in sent["systemInstruction"]["parts"][0]["text"]
              and "NodeX uses an AI API." in sent["contents"][0]["parts"][0]["text"])

    with mock.patch(URLOPEN, side_effect=lambda *a, **k: (_ for _ in ()).throw(http_error(503, "UNAVAILABLE", "high demand"))):
        r = c.post("/analyze", json=body)
        d = r.json()["detail"]
        check("HTTP 503 -> 502 with provider message", r.status_code == 502 and "503" in d and "UNAVAILABLE" in d and "high demand" in d)
        check("error does not leak the API key", "test-key-123" not in d)

    with mock.patch(URLOPEN, side_effect=http_error(400, "INVALID_ARGUMENT", "bad")):
        check("HTTP 400 -> 502", c.post("/analyze", json=body).status_code == 502)

    with mock.patch(URLOPEN, side_effect=urllib.error.URLError("no network")):
        r = c.post("/analyze", json=body)
        check("network failure -> 502", r.status_code == 502 and "could not reach" in r.json()["detail"])

    with mock.patch(URLOPEN, side_effect=TimeoutError()):
        check("timeout -> 502", c.post("/analyze", json=body).status_code == 502)

    with mock.patch(URLOPEN, return_value=FakeResponse(b"<html>not json</html>")):
        check("non-JSON HTTP body -> 502", c.post("/analyze", json=body).status_code == 502)

    with mock.patch(URLOPEN, return_value=gemini_reply("this is not json")):
        check("model text that is not JSON -> 502", c.post("/analyze", json=body).status_code == 502)

    with mock.patch(URLOPEN, return_value=FakeResponse(json.dumps({"candidates": []}).encode())):
        check("no candidates (e.g. blocked) -> 502, not fake data", c.post("/analyze", json=body).status_code == 502)

    g = c.get("/graph").json()
    check("only the one successful call was saved", len(g["nodes"]) == 2 and len(g["edges"]) == 1)

print(f"\nAll {passed} checks passed")

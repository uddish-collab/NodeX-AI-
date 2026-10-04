"""Gemini -> Groq failover tests. urllib is MOCKED and routed by URL: NO real API calls, no real waiting.
Run from backend/:  python tests/test_groq_failover.py
"""
import base64
import contextlib
import io
import json
import logging
import os
import sys
import tempfile
import urllib.error
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
GEMINI_KEY, GROQ_KEY, OPENAI_KEY = "gemini-secret-key-AAA", "groq-secret-key-BBB", "openai-secret-key-CCC"
os.environ["DB_PATH"] = os.path.join(tempfile.mkdtemp(), "test.db")
os.environ["AI_PROVIDER"] = "gemini"
os.environ["GEMINI_API_KEY"] = GEMINI_KEY
os.environ["GEMINI_MODEL"] = "test-gemini-model"
os.environ["GROQ_API_KEY"] = GROQ_KEY
os.environ["GROQ_MODEL"] = "test-groq-model"
os.environ["OPENAI_API_KEY"] = OPENAI_KEY

import pymupdf  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.core.config import settings  # noqa: E402
from app.main import app  # noqa: E402
from app.services import ai_service  # noqa: E402
from app.services.prompts import NO_TEXT_MARKER, SYSTEM_PROMPT  # noqa: E402

SLEEPS: list[float] = []
ai_service.time.sleep = SLEEPS.append  # Gemini's retries must not really wait (delays are recorded)
URLOPEN = "app.services.ai_service.urllib.request.urlopen"
ALL_KEYS = (GEMINI_KEY, GROQ_KEY, OPENAI_KEY)
passed = 0


def check(name: str, cond: bool) -> None:
    global passed
    print(("PASS " if cond else "FAIL ") + name)
    if not cond:
        sys.exit(1)
    passed += 1


# ---------- fakes ----------
class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


GOOD = {
    "entities": [{"id": "e1", "name": "NodeX", "type": "project"}, {"id": "e2", "name": "AI API", "type": "technology"}],
    "relationships": [{"source": "e1", "target": "e2", "relationship": "uses", "explanation": "x", "confidence": 0.9}],
    "source_summary": "About NodeX.",
}
GOOD_JSON = json.dumps(GOOD)
BAD_REF_JSON = json.dumps({**GOOD, "relationships": [{**GOOD["relationships"][0], "target": "e9"}]})


def gemini_ok(text: str) -> FakeResponse:
    return FakeResponse(json.dumps({"candidates": [{"content": {"parts": [{"text": text}]}}]}).encode())


def groq_ok(text: str) -> FakeResponse:
    return FakeResponse(json.dumps({"choices": [{"message": {"content": text}}]}).encode())


def gemini_http_error(code: int, status="UNAVAILABLE", message="busy", details=None):
    err = {"code": code, "status": status, "message": message}
    if details:
        err["details"] = details
    return urllib.error.HTTPError("https://x", code, status, {}, io.BytesIO(json.dumps({"error": err}).encode()))


DAILY_QUOTA = [{"@type": "QuotaFailure", "violations": [{"quotaId": "GenerateRequestsPerDayPerProjectPerModel-FreeTier"}]},
               {"@type": "RetryInfo", "retryDelay": "53847s"}]


def groq_http_error(code: int, message="overloaded"):
    body = json.dumps({"error": {"message": message, "type": "server_error"}}).encode()
    return urllib.error.HTTPError("https://x", code, "err", {}, io.BytesIO(body))


class Router:
    """Routes the mocked urlopen by URL and records every call per provider."""

    def __init__(self, gemini, groq):
        self.handlers = {"gemini": gemini, "groq": groq}
        self.calls = {"gemini": [], "groq": []}
        self.timeouts = {"gemini": [], "groq": []}

    def __call__(self, request, timeout=None):
        url = request.full_url
        provider = "gemini" if "googleapis.com" in url else "groq" if "api.groq.com" in url else None
        assert provider, f"unexpected URL: {url}"
        self.calls[provider].append(request)
        self.timeouts[provider].append(timeout)
        h = self.handlers[provider]
        result = h() if callable(h) else h  # handlers are factories so each call gets a fresh response/error
        if isinstance(result, BaseException):
            raise result
        return result


@contextlib.contextmanager
def setting(**changes):
    old = {k: getattr(settings, k) for k in changes}
    for k, v in changes.items():
        object.__setattr__(settings, k, v)
    try:
        yield
    finally:
        for k, v in old.items():
            object.__setattr__(settings, k, v)


class LogCapture(logging.Handler):
    def __init__(self):
        super().__init__()
        self.messages: list[str] = []

    def emit(self, record):
        self.messages.append(record.getMessage())


log = LogCapture()
logging.getLogger("nodex.ai").addHandler(log)
logging.getLogger("nodex.ai").setLevel(logging.DEBUG)


def png() -> bytes:
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 20, 20), False)
    pix.clear_with(200)
    return pix.tobytes("png")


def blank_pdf() -> bytes:
    d = pymupdf.open()
    d.new_page()
    return d.tobytes()


def no_keys_in(text: str) -> bool:
    return not any(k in text for k in ALL_KEYS)


analyze_body = {"json": {"text": "NodeX uses an AI API."}}


def run(c, gemini, groq, path="/analyze", **kw):
    router = Router(gemini, groq)
    with mock.patch(URLOPEN, side_effect=router):
        r = c.post(path, **(kw or analyze_body))
    return r, router


G503 = lambda: gemini_http_error(503)  # noqa: E731
upload_png = {"files": {"file": ("a.png", png(), "image/png")}}
upload_scan = {"files": {"file": ("scan.pdf", blank_pdf(), "application/pdf")}}

with TestClient(app) as c:
    # ================= text analysis =================
    r, rt = run(c, lambda: gemini_ok(GOOD_JSON), lambda: groq_ok(GOOD_JSON))
    check("Gemini success -> 200", r.status_code == 200 and len(r.json()["nodes"]) == 2)
    check("Gemini success -> Groq is NOT called", len(rt.calls["groq"]) == 0 and len(rt.calls["gemini"]) == 1)

    r, rt = run(c, G503, lambda: groq_ok(GOOD_JSON))
    check("Gemini 503 -> Groq called once and succeeds", r.status_code == 200 and len(rt.calls["groq"]) == 1)
    check("Gemini 503 with Groq configured: 2 Gemini attempts (1 retry), then Groq", len(rt.calls["gemini"]) == 2)
    body = r.json()
    check("Groq result keeps the frontend contract", set(body) == {"nodes", "edges", "source_summary", "source_id"}
          and body["nodes"][0]["source_id"] == body["source_id"] and body["edges"][0]["source"] in {n["id"] for n in body["nodes"]})

    r, rt = run(c, lambda: gemini_http_error(429, "RESOURCE_EXHAUSTED", "quota", DAILY_QUOTA), lambda: groq_ok(GOOD_JSON))
    check("Gemini quota 429 -> Groq succeeds", r.status_code == 200 and len(rt.calls["groq"]) == 1)
    check("quota 429: Gemini tried once only (not retried)", len(rt.calls["gemini"]) == 1)

    r, rt = run(c, lambda: urllib.error.URLError("no network"), lambda: groq_ok(GOOD_JSON))
    check("Gemini network failure -> Groq succeeds", r.status_code == 200 and len(rt.calls["groq"]) == 1)
    r, rt = run(c, lambda: TimeoutError(), lambda: groq_ok(GOOD_JSON))
    check("Gemini timeout -> Groq succeeds", r.status_code == 200 and len(rt.calls["groq"]) == 1)

    r, rt = run(c, lambda: gemini_ok("this is not json"), lambda: groq_ok(GOOD_JSON))
    check("Gemini malformed output -> Groq succeeds", r.status_code == 200 and len(rt.calls["groq"]) == 1 and len(rt.calls["gemini"]) == 1)
    r, rt = run(c, lambda: gemini_ok(BAD_REF_JSON), lambda: groq_ok(GOOD_JSON))
    check("Gemini output with an unknown entity id -> Groq succeeds", r.status_code == 200 and len(rt.calls["groq"]) == 1)

    # ---- the Groq request itself ----
    r, rt = run(c, G503, lambda: groq_ok(GOOD_JSON))
    gq = rt.calls["groq"][0]
    sent = json.loads(gq.data)
    check("Groq: chat completions URL, key only in the Authorization header",
          gq.full_url == "https://api.groq.com/openai/v1/chat/completions" and gq.get_header("Authorization") == f"Bearer {GROQ_KEY}"
          and GROQ_KEY not in gq.full_url and GROQ_KEY.encode() not in gq.data)
    check("Groq: configured model, temperature 0, JSON mode, NodeX system prompt + same text",
          sent["model"] == "test-groq-model" and sent["temperature"] == 0 and sent["response_format"] == {"type": "json_object"}
          and sent["messages"][0] == {"role": "system", "content": SYSTEM_PROMPT} and "NodeX uses an AI API." in sent["messages"][1]["content"])

    # ---- both fail ----
    before = c.get("/graph").json()
    r, rt = run(c, G503, lambda: groq_http_error(500))
    detail = r.json()["detail"]
    check("Gemini fails + Groq fails -> safe 502 that says both failed", r.status_code == 502 and "both" in detail.lower() and "backup" in detail.lower())
    check("both-failed error exposes no provider names, status codes or keys",
          no_keys_in(r.text) and not any(w in detail.lower() for w in ("gemini", "groq", "503", "500", "overloaded", "http")))
    check("both failed: nothing saved", c.get("/graph").json() == before)
    check("both failed: Groq called exactly once (no extra retries)", len(rt.calls["groq"]) == 1)

    r, rt = run(c, G503, lambda: groq_ok("not json at all"))
    check("Groq malformed output -> safe 502 (both failed)", r.status_code == 502 and "both" in r.json()["detail"].lower())
    r, rt = run(c, G503, lambda: groq_ok(BAD_REF_JSON))
    check("Groq output with unknown entity id -> safe 502", r.status_code == 502 and "both" in r.json()["detail"].lower())
    r, rt = run(c, G503, lambda: urllib.error.URLError("down"))
    check("Groq network failure -> safe 502", r.status_code == 502 and "both" in r.json()["detail"].lower())
    r, rt = run(c, G503, lambda: TimeoutError())
    check("Groq timeout -> safe 502", r.status_code == 502 and "both" in r.json()["detail"].lower())
    r, rt = run(c, G503, lambda: FakeResponse(b"<html>bad gateway</html>"))
    check("Groq non-JSON HTTP body -> safe 502", r.status_code == 502 and "both" in r.json()["detail"].lower())

    # ---- API keys never leak ----
    log.messages.clear()
    echo_gemini = lambda: gemini_http_error(500, "INTERNAL", f"bad key {GEMINI_KEY}")  # noqa: E731
    echo_groq = lambda: groq_http_error(401, f"invalid key {GROQ_KEY} / {GEMINI_KEY}")  # noqa: E731
    r, rt = run(c, echo_gemini, echo_groq)
    check("keys echoed by the providers do not reach the response", r.status_code == 502 and no_keys_in(r.text))
    check("keys echoed by the providers do not reach the logs", log.messages and all(no_keys_in(m) for m in log.messages))
    check("failover is logged (server side only)", any("trying Groq" in m for m in log.messages) and any("also failed" in m for m in log.messages))

    # ---- when failover must NOT happen ----
    with setting(groq_api_key=None):
        r, rt = run(c, G503, lambda: groq_ok(GOOD_JSON))
        check("Groq not configured: original Gemini error is returned, Groq never called",
              r.status_code == 502 and r.json()["detail"].startswith("Gemini request failed") and len(rt.calls["groq"]) == 0)
    with setting(gemini_api_key=None):
        r, rt = run(c, G503, lambda: groq_ok(GOOD_JSON))
        check("Gemini not configured: 503 not-configured error, Groq not called", r.status_code == 503 and len(rt.calls["groq"]) == 0)

    # ---- OpenAI provider is untouched ----
    def openai_ok(text):
        return GOOD_JSON

    def openai_fail(text):
        raise ai_service.AIProviderError("OpenAI request failed: boom")

    saved = ai_service._PROVIDERS["openai"]
    with setting(ai_provider="openai"):
        ai_service._PROVIDERS["openai"] = (lambda: OPENAI_KEY, "OPENAI_API_KEY", openai_ok)
        r, rt = run(c, G503, lambda: groq_ok(GOOD_JSON))
        check("OpenAI provider still works and calls neither Gemini nor Groq", r.status_code == 200 and not rt.calls["groq"] and not rt.calls["gemini"])
        ai_service._PROVIDERS["openai"] = (lambda: OPENAI_KEY, "OPENAI_API_KEY", openai_fail)
        r, rt = run(c, G503, lambda: groq_ok(GOOD_JSON))
        check("OpenAI failure is reported as before, with NO Groq failover",
              r.status_code == 502 and r.json()["detail"] == "OpenAI request failed: boom" and not rt.calls["groq"])
    ai_service._PROVIDERS["openai"] = saved

    # ================= vision / OCR =================
    r, rt = run(c, lambda: gemini_ok("Whiteboard text"), lambda: groq_ok("never"), "/upload", **upload_png)
    check("Gemini vision success -> Groq is NOT called", r.status_code == 200 and r.json()["text"] == "Whiteboard text" and not rt.calls["groq"])

    r, rt = run(c, G503, lambda: groq_ok("Text read by Groq"), "/upload", **upload_png)
    check("Gemini vision 503 -> Groq vision succeeds", r.status_code == 200 and r.json()["text"] == "Text read by Groq" and len(rt.calls["groq"]) == 1)
    check("vision response contract unchanged", r.json()["extraction_status"] == "extracted" and r.json()["kind"] == "image")
    gq = rt.calls["groq"][0]
    sent = json.loads(gq.data)
    parts = sent["messages"][0]["content"]
    url = parts[1]["image_url"]["url"]
    check("Groq vision: same image sent as a data URL, text prompt first, no JSON mode",
          parts[0]["type"] == "text" and parts[1]["type"] == "image_url" and url.startswith("data:image/png;base64,")
          and base64.b64decode(url.split(",", 1)[1]).startswith(b"\x89PNG") and "response_format" not in sent)
    check("Groq vision: key only in the Authorization header", gq.get_header("Authorization") == f"Bearer {GROQ_KEY}" and GROQ_KEY.encode() not in gq.data)

    r, rt = run(c, lambda: gemini_http_error(429, "RESOURCE_EXHAUSTED", "quota", DAILY_QUOTA), lambda: groq_ok("Groq read it"), "/upload", **upload_png)
    check("Gemini vision quota 429 -> Groq vision succeeds", r.status_code == 200 and r.json()["text"] == "Groq read it" and len(rt.calls["gemini"]) == 1)
    r, rt = run(c, lambda: urllib.error.URLError("down"), lambda: groq_ok("Groq read it"), "/upload", **upload_png)
    check("Gemini vision network failure -> Groq vision succeeds", r.status_code == 200 and r.json()["text"] == "Groq read it")

    r, rt = run(c, G503, lambda: groq_ok(NO_TEXT_MARKER), "/upload", **upload_png)
    check("Groq vision 'nothing found' -> no_text_found (not an error)", r.status_code == 200 and r.json()["extraction_status"] == "no_text_found")

    log.messages.clear()
    r, rt = run(c, G503, lambda: groq_http_error(500, f"oops {GROQ_KEY}"), "/upload", **upload_png)
    check("Gemini vision fails + Groq vision fails -> safe 502 (both failed)",
          r.status_code == 502 and "both" in r.json()["detail"].lower() and not any(w in r.json()["detail"].lower() for w in ("gemini", "groq", "503", "500")))
    check("vision failure: keys not in response or logs", no_keys_in(r.text) and all(no_keys_in(m) for m in log.messages))
    r, rt = run(c, G503, lambda: TimeoutError(), "/upload", **upload_png)
    check("Groq vision timeout -> safe 502", r.status_code == 502 and "both" in r.json()["detail"].lower())

    r, rt = run(c, G503, lambda: groq_ok("Scanned page text"), "/upload", **upload_scan)
    sent = json.loads(rt.calls["groq"][0].data) if rt.calls["groq"] else {}
    check("scanned PDF OCR: Gemini 503 -> Groq reads the page images",
          r.status_code == 200 and r.json()["text"] == "Scanned page text" and r.json()["kind"] == "pdf"
          and sent["messages"][0]["content"][1]["image_url"]["url"].startswith("data:image/png;base64,"))
    r, rt = run(c, G503, lambda: groq_http_error(500), "/upload", **upload_scan)
    check("scanned PDF OCR: both fail -> safe 502", r.status_code == 502 and "both" in r.json()["detail"].lower())

    with setting(groq_api_key=None):
        r, rt = run(c, G503, lambda: groq_ok("x"), "/upload", **upload_png)
        check("vision, Groq not configured: original Gemini error, Groq never called",
              r.status_code == 502 and r.json()["detail"].startswith("Gemini request failed") and not rt.calls["groq"])
    with setting(gemini_api_key=None):
        r, rt = run(c, G503, lambda: groq_ok("x"), "/upload", **upload_png)
        check("vision, Gemini not configured: stays 'not_configured', Groq not called",
              r.status_code == 200 and r.json()["extraction_status"] == "not_configured" and not rt.calls["groq"])

    # ---- Gemini vision returns HTTP 200 but an EMPTY / unusable reply ----
    gemini_blocked = lambda: FakeResponse(json.dumps({"candidates": []}).encode())  # noqa: E731  (answer blocked: no candidates)
    gemini_blank = lambda: gemini_ok("  \n ")  # noqa: E731
    gemini_thought_only = lambda: FakeResponse(json.dumps({"candidates": [{"content": {"parts": [{"text": "hmm", "thought": True}]}}]}).encode())  # noqa: E731

    for label, empty in [("no candidates (blocked)", gemini_blocked), ("whitespace-only text", gemini_blank), ("thought-only parts", gemini_thought_only)]:
        r, rt = run(c, empty, lambda: groq_ok("Text read by Groq"), "/upload", **upload_png)
        check(f"vision: Gemini 200 + empty reply [{label}] -> Groq reads it",
              r.status_code == 200 and r.json()["text"] == "Text read by Groq" and r.json()["extraction_status"] == "extracted"
              and len(rt.calls["groq"]) == 1 and len(rt.calls["gemini"]) == 1)

    r, rt = run(c, gemini_blocked, lambda: groq_ok("Scanned page via Groq"), "/upload", **upload_scan)
    check("scanned PDF OCR: Gemini empty reply -> Groq reads the pages", r.status_code == 200 and r.json()["text"] == "Scanned page via Groq" and len(rt.calls["groq"]) == 1)

    # the explicit marker is a genuine "nothing readable": NOT a failure, Groq not called
    r, rt = run(c, lambda: gemini_ok(NO_TEXT_MARKER), lambda: groq_ok("should not be used"), "/upload", **upload_png)
    check("vision: explicit NO_TEXT_FOUND -> no_text_found and Groq is NOT called",
          r.status_code == 200 and r.json()["extraction_status"] == "no_text_found" and r.json()["text"] == "" and not rt.calls["groq"])
    r, rt = run(c, lambda: gemini_ok(f"  {NO_TEXT_MARKER}\n"), lambda: groq_ok("x"), "/upload", **upload_png)
    check("vision: NO_TEXT_FOUND with surrounding whitespace still counts as the marker", r.json()["extraction_status"] == "no_text_found" and not rt.calls["groq"])

    # empty Gemini reply + Groq fails -> the existing safe "both failed" error
    log.messages.clear()
    r, rt = run(c, gemini_blocked, lambda: groq_http_error(500, f"oops {GROQ_KEY}"), "/upload", **upload_png)
    check("vision: Gemini empty reply + Groq fails -> safe 502 (both failed)",
          r.status_code == 502 and "both" in r.json()["detail"].lower() and len(rt.calls["groq"]) == 1
          and not any(w in r.json()["detail"].lower() for w in ("gemini", "groq", "empty", "500")))
    check("vision: that failure leaks no keys to the response or logs", no_keys_in(r.text) and all(no_keys_in(m) for m in log.messages)
          and any("empty response" in m for m in log.messages))
    r, rt = run(c, gemini_blocked, lambda: TimeoutError(), "/upload", **upload_png)
    check("vision: Gemini empty reply + Groq timeout -> safe 502", r.status_code == 502 and "both" in r.json()["detail"].lower())

    # without a Groq backup the old behavior is kept: an empty reply is "no text found"
    with setting(groq_api_key=None):
        r, rt = run(c, gemini_blocked, lambda: groq_ok("x"), "/upload", **upload_png)
        check("vision, Groq not configured: empty Gemini reply stays 'no_text_found' (unchanged)",
              r.status_code == 200 and r.json()["extraction_status"] == "no_text_found" and not rt.calls["groq"])

    # text analysis behavior is unchanged: an empty Gemini reply there already fails over
    r, rt = run(c, lambda: gemini_ok(""), lambda: groq_ok(GOOD_JSON))
    check("text analysis (unchanged): empty Gemini reply -> Groq succeeds", r.status_code == 200 and len(rt.calls["groq"]) == 1)

    # =====================================================================
    # Retry / timeout budget
    #   Groq configured     : Gemini 1 retry (2 attempts); timeouts 40 s text / 60 s vision
    #   Groq NOT configured : Gemini 4 retries (5 attempts); timeouts 60 s text / 90 s vision
    # =====================================================================
    PER_MINUTE = [{"@type": "QuotaFailure", "violations": [{"quotaId": "GenerateRequestsPerMinutePerProjectPerModel-FreeTier"}]},
                  {"@type": "RetryInfo", "retryDelay": "2s"}]
    G429_SHORT = lambda: gemini_http_error(429, "RESOURCE_EXHAUSTED", "slow down", PER_MINUTE)  # noqa: E731
    G429_DAILY = lambda: gemini_http_error(429, "RESOURCE_EXHAUSTED", "quota", DAILY_QUOTA)  # noqa: E731

    def then(*steps):
        """A handler that returns the given responses/errors one per call (a stateful Gemini)."""
        queue = list(steps)
        return lambda: queue.pop(0)()

    # ---- Groq configured: text analysis ----
    SLEEPS.clear()
    r, rt = run(c, G503, lambda: groq_ok(GOOD_JSON))
    check("[backup] Gemini 503 -> exactly 2 Gemini attempts, then Groq", r.status_code == 200 and len(rt.calls["gemini"]) == 2 and len(rt.calls["groq"]) == 1)
    check("[backup] Gemini 503 -> a single 1-1.5 s backoff between the 2 attempts", len(SLEEPS) == 1 and 1.0 <= SLEEPS[0] < 1.5)
    check("[backup] text timeouts: Gemini 40 s on every attempt, Groq unchanged at 60 s", rt.timeouts["gemini"] == [40, 40] and rt.timeouts["groq"] == [60])

    SLEEPS.clear()
    r, rt = run(c, G429_SHORT, lambda: groq_ok(GOOD_JSON))
    check("[backup] Gemini transient 429 -> exactly 2 Gemini attempts, then Groq", r.status_code == 200 and len(rt.calls["gemini"]) == 2 and len(rt.calls["groq"]) == 1 and len(SLEEPS) == 1)

    SLEEPS.clear()
    r, rt = run(c, G429_DAILY, lambda: groq_ok(GOOD_JSON))
    check("[backup] Gemini daily-quota 429 -> 1 Gemini attempt (no retry, no sleep), then Groq",
          r.status_code == 200 and len(rt.calls["gemini"]) == 1 and len(rt.calls["groq"]) == 1 and SLEEPS == [])

    SLEEPS.clear()
    r, rt = run(c, lambda: TimeoutError(), lambda: groq_ok(GOOD_JSON))
    check("[backup] Gemini timeout -> 1 attempt made with the 40 s timeout, not retried, then Groq",
          r.status_code == 200 and rt.timeouts["gemini"] == [40] and len(rt.calls["gemini"]) == 1 and len(rt.calls["groq"]) == 1 and SLEEPS == [])
    r, rt = run(c, lambda: urllib.error.URLError("refused"), lambda: groq_ok(GOOD_JSON))
    check("[backup] Gemini network failure -> 1 attempt, not retried, then Groq", len(rt.calls["gemini"]) == 1 and len(rt.calls["groq"]) == 1)
    r, rt = run(c, lambda: gemini_ok("not json"), lambda: groq_ok(GOOD_JSON))
    check("[backup] Gemini malformed output -> 1 attempt, then Groq", len(rt.calls["gemini"]) == 1 and len(rt.calls["groq"]) == 1)

    # the point of keeping one retry: a brief blip is absorbed WITHOUT touching Groq
    r, rt = run(c, then(G503, lambda: gemini_ok(GOOD_JSON)), lambda: groq_ok(GOOD_JSON))
    check("[backup] one transient 503 then success -> 2 Gemini attempts, Groq is NOT called", r.status_code == 200 and len(rt.calls["gemini"]) == 2 and len(rt.calls["groq"]) == 0)
    r, rt = run(c, then(G429_SHORT, lambda: gemini_ok(GOOD_JSON)), lambda: groq_ok(GOOD_JSON))
    check("[backup] one transient 429 then success -> Groq is NOT called", r.status_code == 200 and len(rt.calls["gemini"]) == 2 and len(rt.calls["groq"]) == 0)

    r, rt = run(c, lambda: gemini_ok(GOOD_JSON), lambda: groq_ok(GOOD_JSON))
    check("[backup] Gemini success -> 1 attempt with the 40 s timeout, Groq never called",
          r.status_code == 200 and rt.timeouts["gemini"] == [40] and len(rt.calls["groq"]) == 0)

    r, rt = run(c, G503, lambda: groq_http_error(500))
    check("[backup] Groq is attempted exactly once even when it fails (no Groq retries)", r.status_code == 502 and len(rt.calls["groq"]) == 1)

    # ---- Groq configured: vision / OCR ----
    SLEEPS.clear()
    r, rt = run(c, G503, lambda: groq_ok("Groq read it"), "/upload", **upload_png)
    check("[backup] vision: Gemini 503 -> exactly 2 Gemini attempts, then Groq", r.status_code == 200 and r.json()["text"] == "Groq read it" and len(rt.calls["gemini"]) == 2 and len(rt.calls["groq"]) == 1 and len(SLEEPS) == 1)
    check("[backup] vision timeouts: Gemini 60 s on every attempt, Groq unchanged at 90 s", rt.timeouts["gemini"] == [60, 60] and rt.timeouts["groq"] == [90])
    r, rt = run(c, G429_SHORT, lambda: groq_ok("Groq read it"), "/upload", **upload_png)
    check("[backup] vision: Gemini transient 429 -> 2 attempts, then Groq", len(rt.calls["gemini"]) == 2 and len(rt.calls["groq"]) == 1)
    r, rt = run(c, G429_DAILY, lambda: groq_ok("Groq read it"), "/upload", **upload_png)
    check("[backup] vision: Gemini daily-quota 429 -> 1 attempt, then Groq", len(rt.calls["gemini"]) == 1 and len(rt.calls["groq"]) == 1)
    r, rt = run(c, lambda: TimeoutError(), lambda: groq_ok("Groq read it"), "/upload", **upload_png)
    check("[backup] vision: Gemini timeout -> 1 attempt with the 60 s timeout, then Groq", rt.timeouts["gemini"] == [60] and len(rt.calls["gemini"]) == 1 and len(rt.calls["groq"]) == 1)
    r, rt = run(c, then(G503, lambda: gemini_ok("Gemini read it")), lambda: groq_ok("nope"), "/upload", **upload_png)
    check("[backup] vision: one transient 503 then success -> Groq is NOT called", r.json()["text"] == "Gemini read it" and len(rt.calls["groq"]) == 0)
    r, rt = run(c, lambda: gemini_ok("Gemini read it"), lambda: groq_ok("nope"), "/upload", **upload_png)
    check("[backup] vision: Gemini success -> 1 attempt with the 60 s timeout, Groq never called", rt.timeouts["gemini"] == [60] and len(rt.calls["groq"]) == 0)

    # ---- Groq NOT configured: the previous Gemini behavior is preserved ----
    with setting(groq_api_key=None):
        SLEEPS.clear()
        r, rt = run(c, G503, lambda: groq_ok(GOOD_JSON))
        check("[no backup] Gemini 503 -> 5 attempts (1 + 4 retries), Groq never called", r.status_code == 502 and len(rt.calls["gemini"]) == 5 and len(rt.calls["groq"]) == 0)
        check("[no backup] original 1/2/4/8 s backoff schedule kept", len(SLEEPS) == 4 and all(b <= x < b + 0.5 for x, b in zip(SLEEPS, [1, 2, 4, 8])))
        check("[no backup] original 60 s text timeout kept on every attempt", rt.timeouts["gemini"] == [60] * 5)
        check("[no backup] original Gemini error + retry count reported", r.json()["detail"].startswith("Gemini request failed") and "after 4 retries" in r.json()["detail"])

        r, rt = run(c, G429_SHORT, lambda: groq_ok(GOOD_JSON))
        check("[no backup] Gemini transient 429 -> 5 attempts", len(rt.calls["gemini"]) == 5)
        r, rt = run(c, G429_DAILY, lambda: groq_ok(GOOD_JSON))
        check("[no backup] Gemini daily-quota 429 -> 1 attempt", len(rt.calls["gemini"]) == 1)
        r, rt = run(c, lambda: TimeoutError(), lambda: groq_ok(GOOD_JSON))
        check("[no backup] Gemini timeout -> 1 attempt, 60 s timeout", len(rt.calls["gemini"]) == 1 and rt.timeouts["gemini"] == [60])

        r, rt = run(c, G503, lambda: groq_ok("x"), "/upload", **upload_png)
        check("[no backup] vision: Gemini 503 -> 5 attempts, original 90 s timeout on every attempt", len(rt.calls["gemini"]) == 5 and rt.timeouts["gemini"] == [90] * 5 and len(rt.calls["groq"]) == 0)
        r, rt = run(c, lambda: TimeoutError(), lambda: groq_ok("x"), "/upload", **upload_png)
        check("[no backup] vision: Gemini timeout -> 1 attempt, 90 s timeout", len(rt.calls["gemini"]) == 1 and rt.timeouts["gemini"] == [90])

print(f"\nAll {passed} checks passed")

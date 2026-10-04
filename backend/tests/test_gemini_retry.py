"""Tests for Gemini retry/backoff. urllib and sleep are MOCKED: no real API calls, no real waiting.
Run from backend/:  python tests/test_gemini_retry.py
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

import pymupdf  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402
from app.services import ai_service  # noqa: E402

URLOPEN = "app.services.ai_service.urllib.request.urlopen"
GOOD = {
    "entities": [{"id": "e1", "name": "NodeX", "type": "project"},
                 {"id": "e2", "name": "AI API", "type": "technology"}],
    "relationships": [{"source": "e1", "target": "e2", "relationship": "uses",
                       "explanation": "x", "confidence": 0.9}],
    "source_summary": "s",
}
passed = 0


def check(name: str, cond: bool) -> None:
    global passed
    print(("PASS " if cond else "FAIL ") + name)
    if not cond:
        sys.exit(1)
    passed += 1


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def ok(text: str) -> FakeResponse:
    return FakeResponse(json.dumps({"candidates": [{"content": {"parts": [{"text": text}]}}]}).encode())


def err(code: int, status: str = "UNAVAILABLE", message: str = "busy") -> urllib.error.HTTPError:
    body = json.dumps({"error": {"code": code, "status": status, "message": message}}).encode()
    return urllib.error.HTTPError("https://x", code, status, {}, io.BytesIO(body))


def err429(quota_id: str | None = None, retry_delay: str | None = None) -> urllib.error.HTTPError:
    """A 429 shaped like Gemini's real RESOURCE_EXHAUSTED body (QuotaFailure + RetryInfo details)."""
    details = []
    if quota_id:
        details.append({"@type": "type.googleapis.com/google.rpc.QuotaFailure",
                        "violations": [{"quotaMetric": "generativelanguage.googleapis.com/generate_content_free_tier_requests",
                                        "quotaId": quota_id, "quotaValue": "20"}]})
    if retry_delay:
        details.append({"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": retry_delay})
    body = json.dumps({"error": {"code": 429, "status": "RESOURCE_EXHAUSTED",
                                 "message": "You exceeded your current quota, please check your plan and billing details.",
                                 "details": details}}).encode()
    return urllib.error.HTTPError("https://x", 429, "RESOURCE_EXHAUSTED", {}, io.BytesIO(body))


def png() -> bytes:
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 20, 20), False)
    pix.clear_with(200)
    return pix.tobytes("png")


def run(c, side_effect, path="/analyze", **kw):
    """Call an endpoint with urllib mocked; returns (response, urlopen mock, sleep delays)."""
    delays: list[float] = []
    with mock.patch(URLOPEN, side_effect=side_effect) as m, \
            mock.patch.object(ai_service.time, "sleep", side_effect=delays.append):
        r = c.post(path, **kw)
    return r, m, delays


analyze = {"json": {"text": "NodeX uses an AI API."}}
upload = {"files": {"file": ("a.png", png(), "image/png")}}

with TestClient(app) as c:
    # ---- retry success ----
    r, m, d = run(c, [err(503), err(503), ok(json.dumps(GOOD))], **analyze)
    check("analysis: 503, 503, then success -> 200", r.status_code == 200 and len(r.json()["nodes"]) == 2)
    check("analysis: 3 requests made, 2 sleeps", m.call_count == 3 and len(d) == 2)
    check("backoff is exponential with small jitter", 1.0 <= d[0] < 1.5 and 2.0 <= d[1] < 2.5)
    r, m, d = run(c, [err(429, "RESOURCE_EXHAUSTED", "slow down"), ok(json.dumps(GOOD))], **analyze)
    check("429 is retried", r.status_code == 200 and m.call_count == 2)
    r, m, d = run(c, [err(500, "INTERNAL"), err(502, "BAD_GATEWAY"), err(504, "DEADLINE"), ok(json.dumps(GOOD))], **analyze)
    check("other 5xx (500/502/504) are retried", r.status_code == 200 and m.call_count == 4)

    # ---- retry exhaustion ----
    before = c.get("/graph").json()
    r, m, d = run(c, [err(503) for _ in range(10)], **analyze)
    detail = r.json()["detail"]
    check("exhaustion: 1 try + 4 retries = 5 requests", m.call_count == 5 and len(d) == 4)
    check("exhaustion: backoff 1s,2s,4s,8s (+jitter)", all(b <= x < b + 0.5 for x, b in zip(d, [1, 2, 4, 8])))
    check("exhaustion -> 502 with provider message and retry count",
          r.status_code == 502 and "503" in detail and "UNAVAILABLE" in detail and "after 4 retries" in detail)
    check("exhaustion: API key not exposed", "test-key-123" not in r.text)
    check("exhaustion: nothing new saved", c.get("/graph").json() == before)

    # ---- quota-exhausted 429: fail immediately, no retry, no sleep ----
    DAILY = "GenerateRequestsPerDayPerProjectPerModel-FreeTier"
    before = c.get("/graph").json()
    r, m, d = run(c, [err429(DAILY, "53847s"), ok(json.dumps(GOOD))], **analyze)
    check("daily quota 429 -> exactly 1 request, no sleep", m.call_count == 1 and d == [])
    check("daily quota 429 -> 502, existing error format", r.status_code == 502 and r.json()["detail"].startswith("Gemini request failed: HTTP 429 RESOURCE_EXHAUSTED:") and "retries" not in r.json()["detail"])
    check("daily quota 429: key not exposed, nothing saved", "test-key-123" not in r.text and c.get("/graph").json() == before)
    r, m, d = run(c, [err429(None, "53847s"), ok(json.dumps(GOOD))], **analyze)
    check("429 with only a very long retryDelay (>60s) -> not retried", r.status_code == 502 and m.call_count == 1 and d == [])
    r, m, d = run(c, [err429(DAILY, None), ok(json.dumps(GOOD))], **analyze)
    check("429 with a per-day quotaId and no retryDelay -> not retried", r.status_code == 502 and m.call_count == 1 and d == [])
    r, m, d = run(c, [err429(DAILY, "53847s"), ok("never reached")], "/upload", **upload)
    check("vision: daily quota 429 -> 1 request, no sleep, 502", r.status_code == 502 and m.call_count == 1 and d == [])

    # ---- transient 429 (short-term rate limit): retries preserved ----
    PER_MIN = "GenerateRequestsPerMinutePerProjectPerModel-FreeTier"
    r, m, d = run(c, [err429(PER_MIN, "2s"), err429(PER_MIN, "1s"), ok(json.dumps(GOOD))], **analyze)
    check("per-minute 429 (retryDelay 2s) is retried until success", r.status_code == 200 and m.call_count == 3 and len(d) == 2)
    r, m, d = run(c, [err429(None, "60s"), ok(json.dumps(GOOD))], **analyze)
    check("429 with retryDelay at the 60s limit is still retried", r.status_code == 200 and m.call_count == 2)
    r, m, d = run(c, [err429(PER_MIN, "2s") for _ in range(10)], **analyze)
    check("persistent transient 429 -> 5 requests, 4 sleeps, 502 after retries",
          r.status_code == 502 and m.call_count == 5 and len(d) == 4 and "after 4 retries" in r.json()["detail"])
    r, m, d = run(c, [err429(PER_MIN, "2s"), ok("Scanned text")], "/upload", **upload)
    check("vision: transient 429 retried", r.status_code == 200 and m.call_count == 2)

    # ---- 503 still retried ----
    r, m, d = run(c, [err(503), ok(json.dumps(GOOD))], **analyze)
    check("503 still retried after the quota change", r.status_code == 200 and m.call_count == 2 and len(d) == 1)

    # ---- not retried ----
    for code, status in [(400, "INVALID_ARGUMENT"), (401, "UNAUTHENTICATED"), (403, "PERMISSION_DENIED"), (404, "NOT_FOUND")]:
        r, m, d = run(c, [err(code, status), ok(json.dumps(GOOD))], **analyze)
        check(f"{code} is NOT retried", r.status_code == 502 and m.call_count == 1 and d == [] and str(code) in r.json()["detail"])
    r, m, d = run(c, [urllib.error.URLError("no network"), ok(json.dumps(GOOD))], **analyze)
    check("network failure is NOT retried", r.status_code == 502 and m.call_count == 1 and d == [])
    r, m, d = run(c, [FakeResponse(b"<html>"), ok(json.dumps(GOOD))], **analyze)
    check("non-JSON provider body is NOT retried", r.status_code == 502 and m.call_count == 1)

    # ---- timeouts preserved ----
    r, m, d = run(c, [err(503), ok(json.dumps(GOOD))], **analyze)
    check("analysis keeps the 60s timeout on every attempt", [x.kwargs["timeout"] for x in m.call_args_list] == [60, 60])

    # ---- vision/OCR uses the same retrying function ----
    r, m, d = run(c, [err(503), err(429, "RESOURCE_EXHAUSTED"), ok("Scanned text here")], "/upload", **upload)
    check("vision (image upload): retried then success", r.status_code == 200 and r.json()["text"] == "Scanned text here" and m.call_count == 3)
    check("vision keeps its existing 90s timeout", [x.kwargs["timeout"] for x in m.call_args_list] == [90, 90, 90])
    r, m, d = run(c, [err(503) for _ in range(10)], "/upload", **upload)
    check("vision: exhaustion -> 502, 5 requests, key not leaked",
          r.status_code == 502 and m.call_count == 5 and "test-key-123" not in r.text)
    r, m, d = run(c, [err(400, "INVALID_ARGUMENT")], "/upload", **upload)
    check("vision: 400 not retried", r.status_code == 502 and m.call_count == 1)

print(f"\nAll {passed} checks passed")

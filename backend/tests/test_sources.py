"""Tests for GET /sources. Uses a temporary DB and a canned AI reply (test-only).
Run from backend/:  python tests/test_sources.py
"""
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ["DB_PATH"] = os.path.join(tempfile.mkdtemp(), "test.db")
os.environ["AI_PROVIDER"] = "openai"
os.environ.pop("OPENAI_API_KEY", None)

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402
from app.services import ai_service  # noqa: E402

GOOD = (
    '{"entities":[{"id":"e1","name":"NodeX","type":"project"},{"id":"e2","name":"AI API","type":"technology"}],'
    '"relationships":[{"source":"e1","target":"e2","relationship":"uses","explanation":"x","confidence":0.9}],'
    '"source_summary":"s"}'
)
passed = 0


def check(name: str, cond: bool) -> None:
    global passed
    print(("PASS " if cond else "FAIL ") + name)
    if not cond:
        sys.exit(1)
    passed += 1


with TestClient(app) as c:
    r = c.get("/sources")
    check("empty database -> 200 with an empty list", r.status_code == 200 and r.json() == [])

    # failed analyses must not create sources
    check("failed analysis (no AI key) -> 503", c.post("/analyze", json={"text": "x"}).status_code == 503)
    check("failed analysis created no source", c.get("/sources").json() == [])

    ai_service._PROVIDERS["openai"] = (lambda: "fake", "OPENAI_API_KEY", lambda t: GOOD)
    long_text = "NodeX uses an AI API. " * 50
    a1 = c.post("/analyze", json={"text": long_text, "source_name": "notes.txt", "source_type": "text"}).json()
    a2 = c.post("/analyze", json={"text": "Second document.", "source_name": "memo.pdf", "source_type": "pdf"}).json()

    r = c.get("/sources")
    body = r.json()
    check("two analyses -> two sources", r.status_code == 200 and len(body) == 2)
    check("fields are exactly id, name, source_type, created_at", all(set(s) == {"id", "name", "source_type", "created_at"} for s in body))
    check("full content is not returned", "content" not in body[0] and long_text[:30] not in r.text)
    check("ids match the source_id returned by /analyze", [s["id"] for s in body] == [a1["source_id"], a2["source_id"]])
    check("names and types are stored values", [(s["name"], s["source_type"]) for s in body] == [("notes.txt", "text"), ("memo.pdf", "pdf")])
    check("created_at is a timestamp string", all(isinstance(s["created_at"], str) and "T" in s["created_at"] for s in body))
    check("default name is used when none is given", c.post("/analyze", json={"text": "Third."}).status_code == 200 and c.get("/sources").json()[-1]["name"] == "Pasted text")

    # existing behavior untouched
    g = c.get("/graph").json()
    check("GET /graph still works", len(g["nodes"]) == 6 and len(g["edges"]) == 3)
    check("GET /graph/node/{id} still works", c.get(f"/graph/node/{g['nodes'][0]['id']}").status_code == 200)

    # read-only: no write methods were added
    check("POST /sources not allowed", c.post("/sources").status_code == 405)
    check("DELETE /sources not allowed", c.delete("/sources").status_code == 405)

    # after a reset, the list is empty again
    c.delete("/graph")
    check("sources empty after DELETE /graph", c.get("/sources").json() == [])

print(f"\nAll {passed} checks passed")

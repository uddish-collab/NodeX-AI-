"""Tests for DELETE /graph. Uses a temporary DB and a canned AI reply (test-only).
Run from backend/:  python tests/test_reset.py
"""
import os
import sqlite3
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
DB = os.path.join(tempfile.mkdtemp(), "test.db")
os.environ["DB_PATH"] = DB
os.environ["GROQ_API_KEY"] = ""  # keep the real .env's Groq key (backup provider) out of these tests
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
ai_service._PROVIDERS["openai"] = (lambda: "fake", "OPENAI_API_KEY", lambda t: GOOD)

passed = 0


def check(name: str, cond: bool) -> None:
    global passed
    print(("PASS " if cond else "FAIL ") + name)
    if not cond:
        sys.exit(1)
    passed += 1


def counts() -> list[int]:
    conn = sqlite3.connect(DB)
    try:
        return [conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                for t in ("sources", "entities", "relationships")]
    finally:
        conn.close()


with TestClient(app) as c:
    check("reset on empty database -> cleared", c.delete("/graph").json() == {"status": "cleared"})

    c.post("/analyze", json={"text": "NodeX uses an AI API."})
    c.post("/analyze", json={"text": "Second note."})
    check("data exists before reset", counts() == [2, 4, 2])

    r = c.delete("/graph")
    check("DELETE /graph -> 200 {status: cleared}", r.status_code == 200 and r.json() == {"status": "cleared"})
    check("all three tables emptied", counts() == [0, 0, 0])
    check("GET /graph is empty after reset", c.get("/graph").json() == {"nodes": [], "edges": []})
    check("database file still exists", os.path.exists(DB))
    check("old node now 404", c.get("/graph/node/e1").status_code == 404)

    r = c.post("/analyze", json={"text": "After reset."})
    check("analyze works after reset", r.status_code == 200 and counts() == [1, 2, 1])
    g = c.get("/graph").json()
    check("new data readable after reset", len(g["nodes"]) == 2 and len(g["edges"]) == 1)

print(f"\nAll {passed} checks passed")

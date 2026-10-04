"""Plumbing tests (no real AI key needed). Run from backend/:  python tests/run_tests.py

The AI call is replaced with a canned reply ONLY inside this test file.
"""
import os
import sqlite3
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
tmp = tempfile.mkdtemp()
os.environ["DB_PATH"] = os.path.join(tmp, "test.db")  # never touch the real database
os.environ["GROQ_API_KEY"] = ""  # keep the real .env's Groq key (backup provider) out of these tests
for k in ("OPENAI_API_KEY", "GEMINI_API_KEY"):
    os.environ.pop(k, None)
os.environ["AI_PROVIDER"] = "openai"

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402
from app.services import ai_service  # noqa: E402

GOOD = (
    '{"entities":[{"id":"e1","name":"HackFusion","type":"event"},'
    '{"id":"e2","name":"NodeX","type":"project"},{"id":"e3","name":"AI API","type":"technology"}],'
    '"relationships":[{"source":"e2","target":"e3","relationship":"uses",'
    '"explanation":"The text states that NodeX uses an AI API.","confidence":0.95}],'
    '"source_summary":"About NodeX."}'
)


def fake_ai(reply: str) -> None:
    ai_service._PROVIDERS["openai"] = (lambda: "fake", "OPENAI_API_KEY", lambda t: reply)


def count(table: str) -> int:
    conn = sqlite3.connect(os.environ["DB_PATH"])
    try:
        return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    finally:
        conn.close()


passed = 0


def check(name: str, cond: bool) -> None:
    global passed
    print(("PASS " if cond else "FAIL ") + name)
    if not cond:
        sys.exit(1)
    passed += 1


with TestClient(app) as c:  # context manager runs startup (creates the tables)
    check("GET /", c.get("/").status_code == 200)
    check("GET /health", c.get("/health").json() == {"status": "ok"})

    # --- failures must not create records ---
    r = c.post("/analyze", json={"text": "NodeX uses an AI API."})
    check("missing API key -> 503", r.status_code == 503 and "OPENAI_API_KEY" in r.json()["detail"])
    check("blank text -> 400", c.post("/analyze", json={"text": "  "}).status_code == 400)
    check("missing text -> 422", c.post("/analyze", json={}).status_code == 422)
    fake_ai("not json")
    check("malformed AI output -> 502", c.post("/analyze", json={"text": "hi"}).status_code == 502)
    check("failures saved nothing", count("sources") == 0 and count("entities") == 0)
    check("empty graph at start", c.get("/graph").json() == {"nodes": [], "edges": []})

    # --- success path ---
    fake_ai(GOOD)
    r = c.post("/analyze", json={"text": "HackFusion deadline is 7 PM. NodeX uses an AI API.",
                                  "source_name": "notes.txt"})
    body = r.json()
    check("analyze -> 200", r.status_code == 200)
    check("analyze returns nodes/edges/source_summary",
          len(body["nodes"]) == 3 and len(body["edges"]) == 1 and body["source_summary"])
    ids = {n["id"] for n in body["nodes"]}
    e = body["edges"][0]
    check("edge points at valid node ids", e["source"] in ids and e["target"] in ids)
    check("nodes/edges carry source_id",
          all(n["source_id"] == body["source_id"] for n in body["nodes"]) and e["source_id"] == body["source_id"])
    check("rows saved", count("sources") == 1 and count("entities") == 3 and count("relationships") == 1)

    # second source, to check ids stay unique and filtering works
    c.post("/analyze", json={"text": "Another note."})
    g = c.get("/graph").json()
    check("GET /graph returns all saved data", len(g["nodes"]) == 6 and len(g["edges"]) == 2)
    check("node ids unique", len({n["id"] for n in g["nodes"]}) == 6)
    check("graph matches analyze output", [n for n in g["nodes"] if n["source_id"] == body["source_id"]] == body["nodes"])
    g1 = c.get("/graph", params={"source_id": body["source_id"]}).json()
    check("filter by source_id", len(g1["nodes"]) == 3 and len(g1["edges"]) == 1)
    gt = c.get("/graph", params={"type": "project"}).json()
    check("filter by type drops dangling edges", len(gt["nodes"]) == 2 and gt["edges"] == [])
    check("bad source_id -> 400", c.get("/graph", params={"source_id": "x"}).status_code == 400)

    # --- node details ---
    nx = next(n for n in body["nodes"] if n["label"] == "NodeX")
    d = c.get(f"/graph/node/{nx['id']}").json()
    check("node details: node", d["node"] == nx)
    check("node details: connected node", [n["label"] for n in d["connected_nodes"]] == ["AI API"])
    check("node details: relationship", len(d["relationships"]) == 1 and d["relationships"][0]["relationship"] == "uses")
    check("node details: source", d["source"]["name"] == "notes.txt" and d["source"]["id"] == body["source_id"]
          and "NodeX uses" in d["source"]["excerpt"])
    lonely = next(n for n in body["nodes"] if n["label"] == "HackFusion")
    d2 = c.get(f"/graph/node/{lonely['id']}").json()
    check("node with no connections", d2["connected_nodes"] == [] and d2["relationships"] == [])
    check("unknown node -> 404", c.get("/graph/node/e9999").status_code == 404)
    check("malformed node id -> 404", c.get("/graph/node/abc").status_code == 404)

    # --- uploads still work ---
    check("unsupported upload -> 415",
          c.post("/upload", files={"file": ("a.exe", b"x", "application/octet-stream")}).status_code == 415)

print(f"\nAll {passed} checks passed")

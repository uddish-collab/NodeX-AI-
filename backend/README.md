# NodeX Backend

FastAPI backend for NodeX, an AI-powered knowledge mapping system. It extracts
text from PDFs and text files, asks an AI model to find the key entities and the
relationships the text supports, saves them in SQLite, and serves the result as a
`nodes` + `edges` graph for the frontend.

## Setup

Run these from the `backend/` folder.

```powershell
# Activate the virtual environment (Windows PowerShell)
.\venv\Scripts\Activate.ps1
# Git Bash: source venv/Scripts/activate

# Install dependencies
pip install -r requirements.txt
```

Dependencies: `fastapi`, `uvicorn`, `python-multipart`, `python-dotenv`,
`pymupdf` (PDF text extraction), `openai` and `google-genai` (the two
supported AI providers; you only need the key for one of them).

## Configure the AI provider

```powershell
copy .env.example .env
```

Edit `.env` (it is git-ignored, never commit it):

```
AI_PROVIDER=openai        # or: gemini
OPENAI_API_KEY=sk-...     # needed if AI_PROVIDER=openai
GEMINI_API_KEY=...        # needed if AI_PROVIDER=gemini
```

Optional: `OPENAI_MODEL` (default `gpt-4o-mini`), `GEMINI_MODEL` (default
`gemini-2.0-flash`), `MAX_UPLOAD_MB`, `MAX_TEXT_CHARS`. Restart the server after
editing `.env`. Without a key, `/analyze` returns a clear `503` error.

## Run

```powershell
uvicorn app.main:app --reload
```

- API: http://127.0.0.1:8000
- Interactive docs: http://127.0.0.1:8000/docs

## Endpoints

| Method | Path       | Purpose                                                      |
|--------|------------|--------------------------------------------------------------|
| GET    | `/`        | Identifies the NodeX backend                                 |
| GET    | `/health`  | Returns `{"status": "ok"}`                                   |
| POST   | `/upload`  | Validates a PDF / image / text file and extracts its text    |
| POST   | `/analyze` | Sends text to the AI, saves the result, returns nodes + edges |
| GET    | `/graph`   | Returns the saved graph (nodes + edges), optional filters      |
| GET    | `/graph/node/{id}` | Node details: connections + source                    |

### POST /upload

Multipart form with a `file` field. Allowed: `.pdf`, `.txt`, `.md`, `.png`,
`.jpg`, `.jpeg`, `.webp` (max 10 MB). Files are processed in memory, never saved.

```bash
curl -F "file=@notes.pdf;type=application/pdf" http://127.0.0.1:8000/upload
```

```json
{
  "filename": "notes.pdf",
  "content_type": "application/pdf",
  "size": 12345,
  "status": "received",
  "kind": "pdf",
  "extraction_status": "extracted",
  "text": "HackFusion deadline is 7 PM.
NodeX uses an AI API.",
  "truncated": false,
  "message": null
}
```

`extraction_status` is `extracted`, `no_text_found` (e.g. a scanned PDF), or
`not_configured` (images, for now). Send the returned `text` to `/analyze`.

Errors: `400` empty/missing file, `413` too large, `415` unsupported type,
`422` unreadable/corrupt/password-protected PDF.

### POST /analyze

Sends text to the AI, **saves the result to SQLite**, and returns it as `nodes` + `edges`.

Request (`source_name` and `source_type` are optional, used for traceability):

```json
{
  "text": "HackFusion deadline is 7 PM. NodeX uses an AI API.",
  "source_name": "notes.txt",
  "source_type": "text"
}
```

Response:

```json
{
  "nodes": [
    { "id": "e1", "label": "HackFusion", "type": "event", "source_id": "s1" },
    { "id": "e2", "label": "NodeX", "type": "project", "source_id": "s1" },
    { "id": "e3", "label": "AI API", "type": "technology", "source_id": "s1" }
  ],
  "edges": [
    {
      "id": "r1", "source": "e2", "target": "e3", "relationship": "uses",
      "explanation": "The text states that NodeX uses an AI API.",
      "confidence": 0.95, "source_id": "s1"
    }
  ],
  "source_summary": "The text describes NodeX, HackFusion, and the use of an AI API.",
  "source_id": "s1"
}
```

Errors: `400` blank text, `422` missing text or over 20000 chars, `502` AI provider
failed or returned malformed output, `503` AI not configured. On any error
**nothing is saved**.

### GET /graph

The whole saved knowledge graph. This is what the frontend renders.
Optional filters: `?source_id=s1`, `?type=project` (edges are kept only when both ends remain).

```json
{
  "nodes": [
    { "id": "e2", "label": "NodeX", "type": "project", "source_id": "s1" },
    { "id": "e3", "label": "AI API", "type": "technology", "source_id": "s1" }
  ],
  "edges": [
    {
      "id": "r1", "source": "e2", "target": "e3", "relationship": "uses",
      "explanation": "The text states that NodeX uses an AI API.",
      "confidence": 0.95, "source_id": "s1"
    }
  ]
}
```

### GET /graph/node/{node_id}

Details for the node inspector panel. `404` if the node does not exist.

```json
{
  "node": { "id": "e2", "label": "NodeX", "type": "project", "source_id": "s1" },
  "connected_nodes": [
    { "id": "e3", "label": "AI API", "type": "technology", "source_id": "s1" }
  ],
  "relationships": [
    {
      "id": "r1", "source": "e2", "target": "e3", "relationship": "uses",
      "explanation": "The text states that NodeX uses an AI API.",
      "confidence": 0.95, "source_id": "s1"
    }
  ],
  "source": {
    "id": "s1", "name": "notes.txt", "source_type": "text",
    "created_at": "2026-10-04T12:00:00Z",
    "excerpt": "HackFusion deadline is 7 PM. NodeX uses an AI API."
  }
}
```

## What the frontend uses

The frontend only needs **nodes + edges**. It never sees the AI's internal
`entities`/`relationships` format; `app/services/graph_mapper.py` converts it.
Flow: upload a file (`/upload`) -> send the returned `text` to `/analyze` ->
draw `GET /graph` -> click a node -> `GET /graph/node/{id}`.

Ids are prefixed and unique across sources: `s` = source, `e` = node, `r` = edge.
Every node and edge has a `source_id` so it can be traced back to where it came from.

Allowed values:
- node `type`: person, project, organization, event, topic, technology, document, deadline, resource, concept
- edge `relationship`: related_to, mentions, uses, belongs_to, deadline_for, references, same_topic, depends_on
- `confidence`: 0.0 to 1.0

## Database (SQLite)

No setup needed. The file `backend/data/nodex.db` and its tables are created
automatically when the server starts (change the location with `DB_PATH` in `.env`).
The `data/` folder is git-ignored. To reset everything, stop the server and delete `data/nodex.db`.

| Table | Columns |
|-------|---------|
| `sources` | id, name, source_type (text/pdf/image), content, created_at |
| `entities` | id, source_id -> sources, name, type |
| `relationships` | id, source_id -> sources, source_entity_id -> entities, target_entity_id -> entities, relationship_type, explanation, confidence |

The code uses Python's built-in `sqlite3` (no ORM, no extra dependency); all SQL is in `app/db/repository.py`.

## Tests

```powershell
python tests/run_tests.py
```

Runs against a temporary database with a canned AI reply (inside the test file only),
so no API key is needed. It covers analyze, graph, node details, filtering,
and failure cases (no key, malformed AI output, blank input) leaving the database empty.

## Project layout

```
app/
  main.py                        app setup, routes, startup (creates the DB)
  core/config.py                 settings from environment / .env
  db/database.py, models.py      SQLite connection + table definitions
  db/repository.py              all SQL queries
  models/graph.py                Node / Edge / GraphResponse (what the frontend consumes)
  models/analysis.py             Entity / Relationship / AnalysisResult (internal AI output)
  models/requests.py             request bodies
  routes/                        health, upload, analyze, graph
  services/ai_service.py         provider-neutral AI call + output validation
  services/graph_mapper.py       AI entities/relationships -> API nodes/edges
  services/prompts.py            the NodeX analysis prompt
  services/content_extractor.py  PDF / text / image extraction
  services/text_utils.py         text normalization
  services/file_validation.py    allowed upload types
  services/errors.py             errors mapped to HTTP codes
tests/run_tests.py               plumbing tests
```

## Current limitations

- Image/screenshot extraction is not implemented (`extract_from_image` reports "not configured").
- Scanned PDFs without a text layer give no text (no OCR).
- Input is capped at 20000 characters; longer uploads are truncated (`truncated: true`).
- File type is checked by extension and declared content type, not file contents.
- Each `/analyze` call creates its own nodes. The same thing mentioned in two
  sources (e.g. "NodeX") becomes two separate nodes; there is no cross-source merging yet.
- `/upload` does not save anything; only `/analyze` writes to the database.
- No delete/update endpoints, no auth, and SQLite is for a single-server MVP only.
- The AI is not retried on failure, and relationship quality depends on the model.
- CORS allows all origins (local development).

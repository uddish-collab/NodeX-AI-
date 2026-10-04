# NodeX Backend

FastAPI backend for NodeX, an AI-powered knowledge mapping system. It will take
text and files, find the key ideas in them, and return a graph of **nodes**
(concepts) and **edges** (relationships) for the frontend to draw.

> Status: foundation only. File upload and the `/analyze` contract work, but
> **no AI analysis is implemented yet**.

## Setup

Run these from the `backend/` folder.

```powershell
# Activate the virtual environment (Windows PowerShell)
.\venv\Scripts\Activate.ps1
# Git Bash: source venv/Scripts/activate

# Install dependencies
pip install -r requirements.txt

# Optional: create local config (never commit .env)
copy .env.example .env
```

## Run

```powershell
uvicorn app.main:app --reload
```

- API: http://127.0.0.1:8000
- Interactive docs (try endpoints in the browser): http://127.0.0.1:8000/docs

## Endpoints

| Method | Path       | Purpose                                              |
|--------|------------|------------------------------------------------------|
| GET    | `/`        | Identifies the NodeX backend                         |
| GET    | `/health`  | Returns `{"status": "ok"}`                           |
| POST   | `/upload`  | Accepts a PDF / image / text file, returns metadata  |
| POST   | `/analyze` | Accepts text, returns a graph (placeholder for now)  |

### POST /upload

Multipart form with a `file` field. Allowed: `.pdf`, `.txt`, `.md`, `.png`,
`.jpg`, `.jpeg`, `.webp` (max 10 MB by default).

```bash
curl -F "file=@notes.pdf;type=application/pdf" http://127.0.0.1:8000/upload
```

```json
{ "filename": "notes.pdf", "content_type": "application/pdf", "size": 12345, "status": "received" }
```

Errors: `400` empty/missing file, `413` too large, `415` unsupported type, `422` missing field.

### POST /analyze

Request:

```json
{ "text": "HackFusion deadline is 7 PM. Our NodeX project uses an AI API." }
```

Response (placeholder, the graph is empty on purpose):

```json
{
  "nodes": [],
  "edges": [],
  "status": "placeholder",
  "message": "AI analysis is not implemented yet. No nodes or edges were extracted.",
  "input_length": 66
}
```

Errors: `400` empty/blank text, `422` missing `text` or text over the length limit.

Once implemented, nodes look like `{id, label, type}` and edges like
`{source, target, relationship, explanation}` (see `app/models/graph.py`).

## Project layout

```
app/
  main.py              creates the app, registers routes
  core/config.py       settings from environment / .env
  models/              Pydantic data shapes (graph, request/response)
  routes/              HTTP endpoints (health, upload, analyze)
  services/            logic behind the routes (analyzer placeholder, file validation)
```

## Current limitations

- `/analyze` does no AI work; it returns empty `nodes`/`edges`.
- `/upload` only validates and reports metadata. Files are not saved or read for content.
- File type is checked by extension and the declared content type, not file contents.
- No database, auth, or relationship engine.
- CORS allows all origins (fine for local development).

## Next stage: AI processing

1. Pick a provider (OpenAI or Gemini) and put its key in `.env`.
2. Replace the body of `analyze_text()` in `app/services/analyzer.py` with a call
   that extracts entities and relationships and returns them as `Node`/`Edge`.
3. Extract text from uploaded PDFs/images, then feed it to the same analyzer.

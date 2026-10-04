# NodeX Backend

FastAPI backend for NodeX, an AI-powered knowledge mapping system. It extracts
text from PDF, DOCX, image and text files, asks an AI model to find the key entities
and the relationships the text supports, saves them in SQLite, and serves the result
as a `nodes` + `edges` graph for the frontend.

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
`pymupdf` (PDF text extraction) and `openai` (OpenAI provider SDK). Gemini is
called over plain HTTP using the standard library, so it needs no extra package.
You only need the key for the provider you pick.

## Configure the AI provider

```powershell
copy .env.example .env
```

Edit `.env` (it is git-ignored, never commit it). Our current working setup:

```
AI_PROVIDER=gemini
GEMINI_API_KEY=<your key>       # never commit a real key
GEMINI_MODEL=gemini-3.7-flash
```

`.env.example` already has these defaults with the key left empty. To use OpenAI for
text analysis instead, set `AI_PROVIDER=openai` and `OPENAI_API_KEY` (`OPENAI_MODEL`
defaults to `gpt-4o-mini`).

**Gemini is also required for images and scanned PDFs.** PNG/JPG/JPEG/WEBP reading and
scanned-PDF OCR always use Gemini, so they need `GEMINI_API_KEY` even when
`AI_PROVIDER=openai`.

### Backup provider: Groq (automatic failover)

Gemini stays the primary provider. If you also set a Groq key, NodeX falls back to Groq
automatically:

```
GROQ_API_KEY=<your key>         # leave empty to disable the backup
GROQ_MODEL=qwen/qwen3.8-27b     # default
```

- **Text analysis** (`AI_PROVIDER=gemini`): if Gemini fails (unavailable, rate limit or
  quota, timeout, network error, or unusable/malformed output), the same request is sent
  to Groq. Groq is **not** called when Gemini succeeds. Gemini gets a short retry budget
  first (see "Retries and timeouts" below); Groq gets a single attempt.
- **Image and scanned-PDF reading:** if Gemini vision fails, Groq reads the same images.
- If **both** fail, `/analyze` and `/upload` return one generic `502` ("both the primary
  and the backup AI provider were unable..."). The frontend never sees provider names,
  error details or keys; the details are logged on the server only.
- With no Groq key, behavior is unchanged: Gemini's own error is returned.
- Failover only applies to `AI_PROVIDER=gemini`; the OpenAI provider is unchanged. It does
  not take over when Gemini is simply not configured (that stays a `503` config error).
- Groq's free tier has a tokens-per-minute limit (8,000 on our key), so very large
  documents or rapid uploads may fail on the backup path.

Other optional settings: `MAX_UPLOAD_MB` (default 10), `MAX_TEXT_CHARS` (default 20000),
`DB_PATH`. Restart the server after editing `.env`. Without a key for the chosen
provider, `/analyze` returns a clear `503` error.

> If there is no `.env` at all, the code falls back to `AI_PROVIDER=openai` and
> `GEMINI_MODEL=gemini-2.0-flash`, which is not our working setup. Always copy
> `.env.example` to `.env`.

## Run

```powershell
uvicorn app.main:app --reload
```

- API: http://127.0.0.1:8000
- Interactive docs: http://127.0.0.1:8000/docs

## Endpoints

| Method | Path               | Purpose                                                              |
|--------|--------------------|----------------------------------------------------------------------|
| GET    | `/`                | Identifies the NodeX backend                                         |
| GET    | `/health`          | Returns `{"status": "ok"}`                                           |
| POST   | `/upload`          | Validates a PDF / DOCX / image / text file and extracts its text     |
| POST   | `/analyze`         | Sends text to the AI, saves the result, returns nodes + edges        |
| GET    | `/graph`           | Returns the saved graph (nodes + edges), optional filters            |
| GET    | `/graph/node/{id}` | Node details: connections + source                                   |
| GET    | `/sources`         | Lists every saved source (no full text)                              |
| DELETE | `/graph`           | **Demo reset:** deletes all saved sources, nodes and edges           |

### POST /upload

Multipart form with a `file` field. Returns the extracted, normalized `text`; send it to `/analyze`.

Supported formats:

| Format | Extensions | How text is read | Needs Gemini key? |
|--------|-----------|------------------|-------------------|
| PDF | `.pdf` | Text layer via PyMuPDF. If the PDF has no text (scanned), the first 5 pages are read with Gemini vision (OCR) | Only for scanned PDFs |
| Word | `.docx` | Paragraphs and table rows from the document XML (standard library, no extra package) | No |
| Text | `.txt`, `.md` | Decoded and whitespace-normalized | No |
| Image | `.png`, `.jpg`, `.jpeg`, `.webp` | Gemini vision: transcribes text and briefly describes diagrams/screenshots | **Yes** |

- **PNG/JPG/JPEG/WEBP and scanned-PDF OCR require a configured `GEMINI_API_KEY`** (even if
  `AI_PROVIDER=openai`). Without one, those files return `extraction_status: "not_configured"`
  with an explanatory `message` and empty text (no fake text). PDFs with a text layer, DOCX and
  TXT/MD work without any key.
- **Legacy `.doc` is rejected** (`415`) with the message to save the file as `.docx`.
- Max 10 MB. Files are processed in memory, never saved.
- The browser's declared MIME type must match the extension (e.g. `image/png` for `.png`).
  `.docx` also accepts a generic ZIP/octet-stream type.

```bash
curl -F "file=@notes.docx" http://127.0.0.1:8000/upload
```

```json
{
  "filename": "notes.docx",
  "content_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
  "size": 12345,
  "status": "received",
  "kind": "docx",
  "extraction_status": "extracted",
  "extraction_method": "direct",
  "text": "Project Plan\nNodeX uses an AI API.\nOwner | Deadline\nAlice | 7 PM",
  "truncated": false,
  "message": null
}
```

- `kind`: `pdf`, `docx`, `text` or `image`.
- `extraction_status`: `extracted`, `no_text_found`, or `not_configured`.
- `extraction_method`: `direct` (read from the file) or `gemini_vision` (OCR / image reading).
- `truncated`: text was cut to the 20000-character analysis limit.

Errors: `400` empty/missing file, `413` too large, `415` unsupported type or wrong MIME type,
`422` corrupt/unreadable file (bad PDF/DOCX/image, password-protected PDF),
`502` Gemini failed while reading an image or scanned PDF.

### POST /analyze

Sends text to the AI, **saves the result to SQLite**, and returns it as `nodes` + `edges`.

Request (`source_name` and `source_type` are optional, used for traceability).
`source_type` must be `text`, `pdf` or `image`; send `text` for DOCX/TXT/MD (the frontend does):

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

Errors: `400` blank text, `422` missing text, over 20000 chars or an invalid `source_type`,
`502` AI provider failed or returned malformed output (with the Groq backup configured, a
generic "both providers failed" message), `503` AI not configured.
On any error **nothing is saved**.

#### Retries and timeouts (Gemini)

Gemini retries (exponential backoff with a little jitter) only on `5xx` and short-term `429`
rate limits. A daily-quota `429`, a timeout, a network failure and unusable output are **not**
retried. The budget depends on whether the Groq backup is configured, so a Gemini outage
reaches Groq quickly instead of waiting through long retries:

| | Groq backup configured | No Groq backup |
|---|---|---|
| Retries on `5xx` / short-term `429` | **1** (2 attempts, ~1-1.5 s apart) | 4 (5 attempts: 1, 2, 4, 8 s apart) |
| Gemini timeout per attempt, text analysis | **40 s** | 60 s |
| Gemini timeout per attempt, image/OCR | **60 s** | 90 s |

If Gemini still fails, the request goes to the Groq backup (when configured), otherwise it
ends in a `502`. Typical Gemini overload (`503` returned in about 5 s) now reaches Groq in
roughly 11 s instead of about 40 s. Groq's own timeouts (60 s text / 90 s vision) are unchanged.

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

### GET /sources

Every saved source, oldest first. The full stored text is **not** included. Returns `[]` when empty.

```json
[
  { "id": "s1", "name": "notes.txt", "source_type": "text", "created_at": "2026-10-04T12:00:00Z" }
]
```

### DELETE /graph

**Demo reset.** Deletes every saved source, node and edge (the tables and the database file
stay). No confirmation and no authentication, so only use it locally.

```bash
curl -X DELETE http://127.0.0.1:8000/graph
```

```json
{ "status": "cleared" }
```

New ids keep counting up after a reset (they are not reused).

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
The `data/` folder is git-ignored. To reset everything, call `DELETE /graph` (see above),
or stop the server and delete `data/nodex.db`.

| Table | Columns |
|-------|---------|
| `sources` | id, name, source_type (text/pdf/image), content, created_at |
| `entities` | id, source_id -> sources, name, type |
| `relationships` | id, source_id -> sources, source_entity_id -> entities, target_entity_id -> entities, relationship_type, explanation, confidence |

The code uses Python's built-in `sqlite3` (no ORM, no extra dependency); all SQL is in `app/db/repository.py`.

## Tests

There are seven standalone scripts (no pytest needed). Run each from `backend/`:

```powershell
python tests/run_tests.py          # analyze, graph, node details, filtering, failure cases
python tests/test_gemini_http.py   # Gemini HTTP request/response handling
python tests/test_gemini_retry.py  # retry/backoff and quota-exhausted 429 (no Groq backup)
python tests/test_uploads.py       # PDF, DOCX, TXT/MD, PNG/JPG, validation errors
python tests/test_reset.py         # DELETE /graph
python tests/test_sources.py       # GET /sources
python tests/test_groq_failover.py # Gemini -> Groq failover, retry budget and timeouts (text and vision)
```

They use a temporary database and mocked/canned AI replies (defined only inside the tests),
so no API key and no network are needed.

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
  routes/                        health, upload, analyze, graph, sources
  services/ai_service.py         provider-neutral AI call + output validation
  services/graph_mapper.py       AI entities/relationships -> API nodes/edges
  services/prompts.py            the NodeX analysis prompt
  services/content_extractor.py  PDF / text / image extraction
  services/text_utils.py         text normalization
  services/file_validation.py    allowed upload types
  services/errors.py             errors mapped to HTTP codes
tests/                           standalone test scripts (see Tests)
```

## Current limitations

- Image reading and scanned-PDF OCR depend on Gemini vision and need `GEMINI_API_KEY`; only the first 5 pages of a scanned PDF are read.
- Legacy `.doc` is not supported; DOCX headers, footers, footnotes and text inside images are not extracted.
- Input is capped at 20000 characters; longer uploads are truncated (`truncated: true`).
- File type is checked by extension and declared content type; images and DOCX are also
  checked by their contents (image signature, ZIP structure). Generic `application/octet-stream`
  is only accepted for `.docx`.
- Each `/analyze` call creates its own nodes. The same thing mentioned in two
  sources (e.g. "NodeX") becomes two separate nodes; there is no cross-source merging yet.
- `/upload` does not save anything; only `/analyze` writes to the database.
- The only delete is `DELETE /graph` (wipes everything); there is no per-source delete or update.
  No auth, and SQLite is for a single-server MVP only.
- Gemini's free tier has a daily request limit (we saw 20/day for `gemini-3.7-flash`). When it is
  exhausted, `/analyze` and image/OCR reading move to the Groq backup if configured, otherwise
  they fail with a `502` carrying the provider's message.
  Relationship quality depends on the model.
- CORS allows all origins (local development).

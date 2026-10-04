# NodeX

**AI-powered knowledge mapping.** NodeX reads your documents, screenshots and notes, finds the people, projects, technologies and deadlines inside them, and draws how they connect, with an explanation for every connection and a link back to the source it came from.

> Built for HackFusion. Two-part project: a React frontend and a FastAPI + AI backend.

---

## The problem

Information is scattered across PDFs, Word files, screenshots and plain notes. The relationships that matter ("this project uses that tool", "this deadline belongs to that event") are buried in the text, and nobody has time to read everything and connect the dots by hand.

## The solution

Upload a file and NodeX does the rest:

1. **Extracts** the text, including OCR for screenshots and scanned PDFs.
2. **Analyzes** it with an AI model to identify entities and the relationships the text actually supports.
3. **Saves** the result, so every node and edge stays traceable to the document it came from.
4. **Visualizes** it as an interactive knowledge graph. Click any node to see its source, its connections, why each connection exists, and how confident the AI is.

---

## Key features

- **Multi-format upload**: drag and drop one or many files. See [Supported file formats](#supported-file-formats).
- **Real AI extraction**: entities (person, project, organization, event, topic, technology, document, deadline, resource, concept) and relationships (`uses`, `belongs_to`, `deadline_for`, `depends_on`, `related_to`, `mentions`, `references`, `same_topic`), each with a short explanation grounded in the text and a confidence score.
- **Image and scanned-PDF reading**: screenshots, diagrams and scanned pages are read with a vision model.
- **Interactive Knowledge Map**: readable, non-overlapping layout; relationship labels on the selected node's connections; type legend; per-document view or the whole library.
- **Connection Inspector**: for the selected node, shows the source document and excerpt, every connected node, and each relationship with its explanation and confidence.
- **Documents library**: every analyzed document is saved and listed with its node and connection counts, and survives a page reload.
- **Search** across documents and graph nodes.
- **Automatic AI failover**: Gemini is the primary model; if it is unavailable, rate-limited, times out or returns unusable output, NodeX transparently retries on Groq. The frontend never sees which provider answered.
- **Source traceability**: every node and edge carries the `source_id` of the document it came from.
- **Clear error handling**: unsupported types, empty or corrupt files, oversized files and AI failures each return a specific message that the UI shows on the document.
- **UI polish**: dark / dim / light themes, optional graph animations, responsive layout down to phone width.
- **Tested backend**: 218 automated checks that run entirely against mocked AI, so no API calls or keys are needed to run them.

---

## Supported file formats

| Format | Extensions | How the text is read |
|---|---|---|
| PDF | `.pdf` | Text layer via PyMuPDF. If the PDF has no text (scanned), the first 5 pages are read with a vision model (OCR). |
| Word | `.docx` | Paragraphs and table rows. |
| Text | `.txt`, `.md` | Read directly. |
| Images | `.png`, `.jpg`, `.jpeg`, `.webp` | Vision model transcribes the text and briefly describes diagrams. |

- The web UI's file picker offers PDF, PNG, JPG/JPEG, DOCX and TXT. The backend additionally accepts `.md` and `.webp`.
- **Legacy `.doc` files are not supported.** Please save them as `.docx` first. The API rejects `.doc` with that instruction.
- Maximum upload size is 10 MB. Files are processed in memory and are not stored; only the extracted text is saved.

---

## Architecture

```
 React + Vite  ──►  FastAPI  ──►  Content extraction  ──►  AI analysis  ──►  SQLite  ──►  Knowledge Graph API
 (frontend)         (backend)     PDF · DOCX · TXT         Gemini (primary)   (sources,      nodes + edges
                                  images / scans via        │                  entities,         │
                                  vision model              └─ on failure ─►   relationships)    ▼
                                                               Groq (backup)                  React graph,
                                                                                              Inspector, Documents
```

**Flow for one file**

1. `POST /upload`: validate the file and extract its text (vision model for images and scanned PDFs).
2. `POST /analyze`: the AI returns entities and relationships as structured JSON, which the backend validates strictly (allowed types, valid references between nodes). Anything malformed is rejected, never saved.
3. The validated result is saved to SQLite and returned as `nodes` + `edges`.
4. The frontend loads `GET /graph`, `GET /graph/node/{id}` and `GET /sources` to draw the map, the Inspector and the Documents page.

**AI providers**

- **Gemini** is the primary provider for text analysis and vision.
- **Groq** is the automatic backup for both. On a Gemini failure, NodeX makes one quick retry for temporary errors, then switches to Groq. If both fail, the user gets one generic "both providers failed" message and nothing is saved.
- **OpenAI** is also supported as an alternative text-analysis provider (`AI_PROVIDER=openai`). Failover to Groq applies when Gemini is the provider.
- Model names are configurable in `backend/.env`.

---

## Tech stack

| Layer | Technology |
|---|---|
| Frontend | React 19, Vite 8, plain CSS (no UI or graph library; the graph is HTML + SVG) |
| Backend | Python, FastAPI, Uvicorn, Pydantic |
| AI | Google Gemini (primary), Groq (backup), optional OpenAI, all called over HTTPS from the backend |
| Extraction | PyMuPDF (PDF), built-in ZIP/XML parsing (DOCX), vision model for images and scans |
| Storage | SQLite via Python's built-in `sqlite3` |
| Testing | Standalone Python test scripts (mocked AI), ESLint |

---

## API endpoints

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/` | Identifies the backend |
| `GET` | `/health` | Health check, returns `{"status": "ok"}` |
| `POST` | `/upload` | Validates a file and returns its extracted text |
| `POST` | `/analyze` | Sends text to the AI, saves the result, returns `nodes` + `edges` |
| `GET` | `/graph` | The saved knowledge graph (optional `?source_id=` and `?type=` filters) |
| `GET` | `/graph/node/{id}` | One node's connections, relationships and source |
| `GET` | `/sources` | Lists saved documents (without their full text) |
| `DELETE` | `/graph` | Demo reset: deletes all saved data |

Interactive API docs are available at `http://127.0.0.1:8000/docs` while the backend is running. Full request and response examples are in [`backend/README.md`](backend/README.md).

---

## Run it locally

You will need **Python 3.10+** and **Node.js 20.19+ or 22.12+** (required by Vite 8).

### 1. Backend

```bash
cd backend

# create and activate a virtual environment (first time only)
python -m venv venv
# Windows PowerShell:  .\venv\Scripts\Activate.ps1
# macOS / Linux:       source venv/bin/activate

pip install -r requirements.txt

# create your local config, then add your API keys (see the note below)
cp .env.example .env          # Windows: copy .env.example .env

uvicorn app.main:app --reload
```

The API runs at `http://127.0.0.1:8000`. The SQLite database (`backend/data/nodex.db`) is created automatically.

### 2. Frontend

In a second terminal:

```bash
cd frontend/app
npm install
npm run dev
```

Open `http://localhost:5173`. The frontend talks to `http://127.0.0.1:8000` by default. To use a different backend address, copy `frontend/app/.env.example` to `.env.local` and set `VITE_API_URL`.

### 3. Try it

1. Open the Dashboard and upload a `.txt`, `.docx`, `.pdf` or image file.
2. Watch it go from *Processing* to *Processed*.
3. Open **Knowledge Map**, click a node, and read its connections in the Inspector.
4. Open **Documents** to see everything you have analyzed.

### Run the checks

```bash
# backend: seven standalone scripts, all mocked (no keys, no network)
cd backend
python tests/run_tests.py
python tests/test_gemini_http.py
python tests/test_gemini_retry.py
python tests/test_uploads.py
python tests/test_reset.py
python tests/test_sources.py
python tests/test_groq_failover.py

# frontend
cd frontend/app
npm run lint
npm run build
```

---

## Security and API keys

- **API keys live only in `backend/.env`.** Set `GEMINI_API_KEY` (primary) and `GROQ_API_KEY` (backup); `OPENAI_API_KEY` is only needed if you choose OpenAI.
- **`.env` is git-ignored and must never be committed.** Only `backend/.env.example`, which contains empty placeholders, is in the repository.
- **Never put API keys in the frontend.** Any `VITE_*` variable is bundled into the browser code and is public. The frontend only knows the backend's address; it never sees a key, and the backend never returns one.
- API keys never appear in responses, error messages or server logs. With the Groq backup configured, AI provider failures are reported to the browser as one generic message without provider details.
- `frontend/app/.env.example` contains only the backend address (`VITE_API_URL`), never a key.
- Note that the text of uploaded files is sent to the configured AI providers (Gemini and, on failover, Groq) for analysis.

---

## Current limitations

These are real limitations of the current version:

- **No cross-document merging.** The same entity appearing in two documents (for example "NodeX") becomes two separate nodes, so documents are not yet linked to each other.
- **Provider limits.** Gemini's free tier has a daily request limit (we observed 20 per day for `gemini-3.7-flash`), and Groq's free tier limits tokens per minute (8,000 on our key). When Gemini is overloaded, analysis falls back to Groq. If both are unavailable, the upload shows an error. Large documents can hit Groq's limit on the backup path.
- **Input limits.** Analysis uses at most 20,000 characters per document (longer text is truncated), and scanned PDFs are read for their first 5 pages only.
- **DOCX extraction.** Headers, footers and footnotes are not read, and text inside Word text boxes can appear twice.
- **No authentication and open CORS.** This is a local demo build. `DELETE /graph` wipes all data with no confirmation.
- **No per-document deletion.** The × button on a document only removes it from the current session. Previously analyzed documents stay saved until `DELETE /graph` is called or `backend/data/nodex.db` is deleted.
- **Original files are not kept.** Only the extracted text is saved, so a saved document cannot be re-opened as a file after a page reload.
- **Uploads are processed one at a time.** If "Auto process uploads" is switched off in Settings, files wait as "Ready to process" and there is no button yet to start them.
- **Quality depends on the AI model.** Entity types and relationships are model judgments, validated for structure but not fact-checked.
- **Single-server storage.** SQLite is suited to a demo or single-machine deployment.

---

## Project structure

```
NodeX-AI-/
├── backend/
│   ├── app/
│   │   ├── main.py            FastAPI app and routes registration
│   │   ├── routes/            upload, analyze, graph, sources, health
│   │   ├── services/          extraction, AI (Gemini/Groq/OpenAI), prompts, graph mapping
│   │   ├── models/            Pydantic models (graph, AI output, requests)
│   │   ├── db/                SQLite schema and queries
│   │   └── core/config.py     settings from environment / .env
│   ├── tests/                 mocked test scripts
│   ├── requirements.txt
│   └── .env.example           placeholders only; copy to .env
├── frontend/
│   └── app/                   React + Vite application
│       └── src/               App.jsx, App.css, api.js
└── README.md
```

For detailed backend documentation (request and response examples, error codes, retry and timeout behavior, database schema), see [`backend/README.md`](backend/README.md).

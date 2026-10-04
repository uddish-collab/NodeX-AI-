"""Upload/extraction tests for PDF, DOCX, TXT/MD, PNG, JPG. Gemini is MOCKED: no real API calls.
Run from backend/:  python tests/test_uploads.py
"""
import base64
import io
import json
import os
import sys
import tempfile
import zipfile
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ["DB_PATH"] = os.path.join(tempfile.mkdtemp(), "test.db")
os.environ["GROQ_API_KEY"] = ""  # keep the real .env's Groq key (backup provider) out of these tests
os.environ["GEMINI_API_KEY"] = "test-key-123"
os.environ["GEMINI_MODEL"] = "test-model"
os.environ["MAX_UPLOAD_MB"] = "1"
os.environ["MAX_TEXT_CHARS"] = "500"

import pymupdf  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402
from app.services import ai_service  # noqa: E402

ai_service.time.sleep = lambda s: None  # retries must not really wait in tests
from app.services.file_validation import DOCX_MIME  # noqa: E402

URLOPEN = "app.services.ai_service.urllib.request.urlopen"
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


def gemini_text(text: str) -> FakeResponse:
    return FakeResponse(json.dumps({"candidates": [{"content": {"parts": [{"text": text}]}}]}).encode())


# ---------- fixtures built in memory ----------
def make_pdf(text: str | None) -> bytes:
    doc = pymupdf.open()
    page = doc.new_page()
    if text:
        page.insert_text((72, 72), text)
    return doc.tobytes()


def make_image(fmt: str) -> bytes:
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 20, 20), False)
    pix.clear_with(200)
    return pix.tobytes(fmt)


W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def make_docx(body_xml: str, valid: bool = True) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("[Content_Types].xml", "<Types/>")
        if valid:
            z.writestr("word/document.xml", f'<w:document xmlns:w="{W}"><w:body>{body_xml}</w:body></w:document>')
        else:
            z.writestr("other.txt", "x")
    return buf.getvalue()


def para(t: str) -> str:
    return f"<w:p><w:r><w:t>{t}</w:t></w:r></w:p>"


def cell(t: str) -> str:
    return f"<w:tc>{para(t)}</w:tc>"


DOCX_BODY = (
    para("Project Plan") + para("NodeX uses an AI API.")
    + "<w:tbl><w:tr>" + cell("Owner") + cell("Deadline") + "</w:tr>"
    + "<w:tr>" + cell("Alice") + cell("7 PM") + "</w:tr></w:tbl>"
    + para("Closing line.")
)


def upload(c, name: str, data: bytes, mime: str):
    return c.post("/upload", files={"file": (name, data, mime)})


with TestClient(app) as c:
    # ---- TXT / MD ----
    r = upload(c, "a.txt", b"Hello   there\r\n\r\n\r\n\r\nline2  ", "text/plain").json()
    check("TXT: extracted + normalized", r["text"] == "Hello there\n\nline2" and r["extraction_status"] == "extracted" and r["extraction_method"] == "direct")
    r = upload(c, "a.md", b"# Title\n\ntext", "text/markdown").json()
    check("MD: extracted", r["kind"] == "text" and r["text"] == "# Title\n\ntext")
    r = upload(c, "w.txt", b"   \n  ", "text/plain")
    check("TXT: whitespace-only -> no_text_found", r.json()["extraction_status"] == "no_text_found")

    # ---- PDF with text (no Gemini call) ----
    with mock.patch(URLOPEN) as m:
        r = upload(c, "a.pdf", make_pdf("HackFusion deadline is 7 PM."), "application/pdf").json()
        check("PDF text layer: extracted directly", r["text"] == "HackFusion deadline is 7 PM." and r["extraction_method"] == "direct")
        check("PDF text layer: Gemini not called", m.call_count == 0)

    # ---- scanned PDF -> OCR fallback ----
    with mock.patch(URLOPEN, return_value=gemini_text("Scanned: NodeX uses an AI API.")) as m:
        r = upload(c, "scan.pdf", make_pdf(None), "application/pdf").json()
        sent = json.loads(m.call_args.args[0].data)
        parts = sent["contents"][0]["parts"]
        check("scanned PDF: OCR text returned", r["text"] == "Scanned: NodeX uses an AI API." and r["extraction_method"] == "gemini_vision")
        check("scanned PDF: page image sent as PNG", parts[1]["inlineData"]["mimeType"] == "image/png" and base64.b64decode(parts[1]["inlineData"]["data"]).startswith(b"\x89PNG"))
    with mock.patch(URLOPEN, return_value=gemini_text("NO_TEXT_FOUND")):
        r = upload(c, "scan.pdf", make_pdf(None), "application/pdf").json()
        check("scanned PDF: nothing found -> no_text_found", r["extraction_status"] == "no_text_found" and r["text"] == "")
    with mock.patch.object(ai_service, "gemini_configured", return_value=False):
        r = upload(c, "scan.pdf", make_pdf(None), "application/pdf").json()
        check("scanned PDF without key -> not_configured", r["extraction_status"] == "not_configured" and "GEMINI_API_KEY" in r["message"])
    check("corrupt PDF -> 422", upload(c, "b.pdf", b"garbage", "application/pdf").status_code == 422)

    # ---- DOCX ----
    r = upload(c, "a.docx", make_docx(DOCX_BODY), DOCX_MIME).json()
    check("DOCX: paragraphs extracted in order", r["text"].startswith("Project Plan\nNodeX uses an AI API.") and r["text"].endswith("Closing line."))
    check("DOCX: table rows extracted", "Owner | Deadline" in r["text"] and "Alice | 7 PM" in r["text"])
    check("DOCX: kind/method", r["kind"] == "docx" and r["extraction_method"] == "direct")
    check("DOCX: generic octet-stream MIME accepted", upload(c, "a.docx", make_docx(DOCX_BODY), "application/octet-stream").status_code == 200)
    check("DOCX: empty document -> no_text_found", upload(c, "e.docx", make_docx(""), DOCX_MIME).json()["extraction_status"] == "no_text_found")
    check("DOCX: not a zip -> 422", upload(c, "b.docx", b"not a zip file", DOCX_MIME).status_code == 422)
    check("DOCX: zip without document.xml -> 422", upload(c, "b.docx", make_docx("", valid=False), DOCX_MIME).status_code == 422)

    # ---- PNG / JPG / JPEG via Gemini vision ----
    for name, fmt, mime in [("a.png", "png", "image/png"), ("a.jpg", "jpeg", "image/jpeg"), ("a.jpeg", "jpeg", "image/jpeg")]:
        with mock.patch(URLOPEN, return_value=gemini_text("Whiteboard: NodeX -> AI API")) as m:
            r = upload(c, name, make_image(fmt), mime).json()
            req = m.call_args.args[0]
            sent = json.loads(req.data)
            inline = sent["contents"][0]["parts"][1]["inlineData"]
            check(f"{name}: text from Gemini vision", r["text"] == "Whiteboard: NodeX -> AI API" and r["extraction_method"] == "gemini_vision" and r["extraction_status"] == "extracted")
            check(f"{name}: mime + base64 image sent", inline["mimeType"] == mime and len(base64.b64decode(inline["data"])) > 0)
            check(f"{name}: API key in header only", req.get_header("X-goog-api-key") == "test-key-123" and "test-key-123" not in req.full_url)
    with mock.patch(URLOPEN, return_value=gemini_text("NO_TEXT_FOUND")):
        check("image: nothing found -> no_text_found", upload(c, "a.png", make_image("png"), "image/png").json()["extraction_status"] == "no_text_found")
    with mock.patch.object(ai_service, "gemini_configured", return_value=False):
        r = upload(c, "a.png", make_image("png"), "image/png").json()
        check("image without key -> not_configured, no fake text", r["extraction_status"] == "not_configured" and r["text"] == "")
    import urllib.error
    with mock.patch(URLOPEN, side_effect=lambda *a, **k: (_ for _ in ()).throw(urllib.error.HTTPError("https://x", 503, "UNAVAILABLE", {}, io.BytesIO(b'{"error":{"status":"UNAVAILABLE","message":"busy"}}')))):
        r = upload(c, "a.png", make_image("png"), "image/png")
        check("image: Gemini 503 -> 502 with message, key not leaked", r.status_code == 502 and "busy" in r.json()["detail"] and "test-key-123" not in r.text)
    check("image: PNG bytes that are not an image -> 422", upload(c, "a.png", b"definitely not a png", "image/png").status_code == 422)
    with mock.patch(URLOPEN, return_value=gemini_text("x")) as m:
        upload(c, "a.png", make_image("jpeg"), "image/png")
        sent = json.loads(m.call_args.args[0].data)
        check("image: real type (from bytes) is sent, not the filename's", sent["contents"][0]["parts"][1]["inlineData"]["mimeType"] == "image/jpeg")

    # ---- validation ----
    check("unsupported .exe -> 415", upload(c, "a.exe", b"x", "application/octet-stream").status_code == 415)
    r = upload(c, "old.doc", b"x", "application/msword")
    check("legacy .doc -> 415 with .docx hint", r.status_code == 415 and ".docx" in r.json()["detail"])
    check("PNG with wrong MIME -> 415", upload(c, "a.png", make_image("png"), "text/plain").status_code == 415)
    check("PDF with wrong MIME -> 415", upload(c, "a.pdf", make_pdf("x"), "text/plain").status_code == 415)
    check("empty file -> 400", upload(c, "e.txt", b"", "text/plain").status_code == 400)
    check("oversized file -> 413", upload(c, "big.txt", b"a" * (1024 * 1024 + 1), "text/plain").status_code == 413)

    # ---- length limit (MAX_TEXT_CHARS=500 here) ----
    r = upload(c, "long.txt", b"word " * 400, "text/plain").json()
    check("long text truncated to the limit", r["truncated"] is True and len(r["text"]) == 500)

print(f"\nAll {passed} checks passed")

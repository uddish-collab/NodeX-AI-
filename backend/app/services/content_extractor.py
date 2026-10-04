"""Turns uploaded file bytes into clean text. Nothing is written to disk."""
import io
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass

import pymupdf

from app.services import ai_service
from app.services.errors import UnreadableFileError
from app.services.file_validation import image_mime_from_bytes
from app.services.text_utils import normalize_text

MAX_OCR_PAGES = 5  # scanned PDFs: only the first pages are read by the vision model
MAX_DOCX_XML_BYTES = 50 * 1024 * 1024  # guard against zip bombs


@dataclass
class Extraction:
    text: str
    status: str  # "extracted" | "no_text_found" | "not_configured"
    method: str  # "direct" (text read from the file) | "gemini_vision" (OCR / image reading)
    message: str | None = None


def _vision(images: list[tuple[bytes, str]], empty_message: str) -> Extraction:
    text = normalize_text(ai_service.extract_text_from_images(images))
    if text:
        return Extraction(text, "extracted", "gemini_vision")
    return Extraction("", "no_text_found", "gemini_vision", empty_message)


# ---------- PDF ----------

def extract_from_pdf(data: bytes) -> Extraction:
    """Read the PDF text layer. If there is none (scanned PDF), fall back to
    Gemini vision on the first pages. Raises UnreadableFileError for corrupt files."""
    try:
        with pymupdf.open(stream=data, filetype="pdf") as doc:
            if doc.needs_pass:
                raise UnreadableFileError("This PDF is password-protected.")
            text = normalize_text("\n\n".join(page.get_text() for page in doc))
            if text:
                return Extraction(text, "extracted", "direct")
            if not ai_service.gemini_configured():
                return Extraction(
                    "", "not_configured", "direct",
                    "This PDF has no text layer (it looks scanned). Set GEMINI_API_KEY to enable OCR.",
                )
            pages = [
                (page.get_pixmap(dpi=150).tobytes("png"), "image/png")
                for page in list(doc)[:MAX_OCR_PAGES]
            ]
    except UnreadableFileError:
        raise
    except Exception:
        raise UnreadableFileError("Could not read this PDF. The file may be corrupted.")
    return _vision(pages, "No readable text was found in this PDF, even with OCR.")


# ---------- DOCX ----------

_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def _paragraph_text(p: ET.Element) -> str:
    out = []
    for el in p.iter():
        if el.tag == _W + "t":
            out.append(el.text or "")
        elif el.tag == _W + "tab":
            out.append("\t")
        elif el.tag in (_W + "br", _W + "cr"):
            out.append("\n")
    return "".join(out)


def _block_lines(parent: ET.Element) -> list[str]:
    """Paragraphs and tables of a body/cell, in document order."""
    lines: list[str] = []
    for child in parent:
        if child.tag == _W + "p":
            lines.append(_paragraph_text(child))
        elif child.tag == _W + "tbl":
            for row in child.findall(_W + "tr"):
                cells = []
                for cell in row.findall(_W + "tc"):
                    cells.append(" ".join(x for x in _block_lines(cell) if x.strip()))
                if any(c.strip() for c in cells):
                    lines.append(" | ".join(cells))
        elif child.tag == _W + "sdt":  # content controls wrap real content
            content = child.find(_W + "sdtContent")
            if content is not None:
                lines.extend(_block_lines(content))
    return lines


def extract_from_docx(data: bytes) -> Extraction:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            info = z.getinfo("word/document.xml")
            if info.file_size > MAX_DOCX_XML_BYTES:
                raise UnreadableFileError("This DOCX is too large to read.")
            root = ET.fromstring(z.read(info))
    except UnreadableFileError:
        raise
    except (zipfile.BadZipFile, KeyError, ET.ParseError):
        raise UnreadableFileError("Could not read this DOCX. It may be corrupted or not a real .docx file.")
    body = root.find(_W + "body")
    text = normalize_text("\n".join(_block_lines(body))) if body is not None else ""
    if text:
        return Extraction(text, "extracted", "direct")
    return Extraction("", "no_text_found", "direct", "No readable text found in this DOCX.")


# ---------- Text ----------

def extract_from_text(data: bytes) -> Extraction:
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = data.decode("latin-1")  # never fails; good enough for an MVP
    text = normalize_text(text)
    if text:
        return Extraction(text, "extracted", "direct")
    return Extraction("", "no_text_found", "direct", "No readable text found in this file.")


# ---------- Images ----------

def extract_from_image(data: bytes) -> Extraction:
    """Read text from a PNG/JPG/WEBP with Gemini vision (OCR + short description of diagrams)."""
    mime = image_mime_from_bytes(data)
    if mime is None:
        raise UnreadableFileError("This file is not a valid PNG, JPG or WEBP image.")
    if not ai_service.gemini_configured():
        return Extraction(
            "", "not_configured", "gemini_vision",
            "Image reading is not configured. Set GEMINI_API_KEY in backend/.env.",
        )
    return _vision([(data, mime)], "No readable text or content was found in this image.")


def extract(kind: str, data: bytes) -> Extraction:
    return {
        "pdf": extract_from_pdf,
        "docx": extract_from_docx,
        "text": extract_from_text,
        "image": extract_from_image,
    }[kind](data)

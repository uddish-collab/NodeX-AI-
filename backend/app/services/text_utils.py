import re


def normalize_text(text: str) -> str:
    """Tidy text for an LLM: consistent newlines, no trailing spaces,
    collapsed runs of spaces, and at most one blank line in a row.
    Punctuation and paragraph structure are kept."""
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\x00", "")
    text = re.sub(r"[ \t\f\v ]+", " ", text)  # collapse spaces/tabs
    text = re.sub(r" ?\n ?", "\n", text)  # trim around line breaks
    text = re.sub(r"\n{3,}", "\n\n", text)  # at most one blank line
    return text.strip()

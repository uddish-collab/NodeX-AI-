"""App settings, loaded from environment variables (and a local .env file)."""
import os
from dataclasses import dataclass

from dotenv import load_dotenv

# Reads backend/.env if it exists. Real environment variables take priority.
load_dotenv()


@dataclass(frozen=True)
class Settings:
    app_name: str = "NodeX API"
    version: str = "0.1.0"
    # Optional: set whichever AI provider key you choose later. Neither is required.
    openai_api_key: str | None = os.getenv("OPENAI_API_KEY") or None
    gemini_api_key: str | None = os.getenv("GEMINI_API_KEY") or None
    max_upload_mb: int = int(os.getenv("MAX_UPLOAD_MB", "10"))
    max_text_chars: int = int(os.getenv("MAX_TEXT_CHARS", "20000"))


settings = Settings()

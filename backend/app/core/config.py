"""App settings, loaded from environment variables (and a local .env file)."""
import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

# Reads backend/.env if it exists. Real environment variables take priority.
load_dotenv()


@dataclass(frozen=True)
class Settings:
    app_name: str = "NodeX API"
    version: str = "0.1.0"
    # Choose "openai" or "gemini". Only the chosen provider's key is needed.
    ai_provider: str = os.getenv("AI_PROVIDER", "openai").strip().lower()
    openai_model: str = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
    gemini_model: str = os.getenv("GEMINI_MODEL", "gemini-2.0-flash")
    openai_api_key: str | None = os.getenv("OPENAI_API_KEY") or None
    gemini_api_key: str | None = os.getenv("GEMINI_API_KEY") or None
    # SQLite file; the data/ folder is created automatically.
    db_path: str = os.getenv(
        "DB_PATH", str(Path(__file__).resolve().parents[2] / "data" / "nodex.db")
    )
    max_upload_mb: int = int(os.getenv("MAX_UPLOAD_MB", "10"))
    max_text_chars: int = int(os.getenv("MAX_TEXT_CHARS", "20000"))


settings = Settings()

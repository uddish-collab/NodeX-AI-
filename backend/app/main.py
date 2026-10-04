from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import settings
from app.routes import analyze, health, upload

app = FastAPI(
    title=settings.app_name,
    description="AI-powered knowledge mapping backend",
    version=settings.version,
)

# Lets the frontend dev server (a different port) call this API. Tighten later.
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

app.include_router(health.router)
app.include_router(upload.router)
app.include_router(analyze.router)

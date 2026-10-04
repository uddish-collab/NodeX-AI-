from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.core.config import settings
from app.db.database import init_db
from app.routes import analyze, graph, health, upload
from app.services.errors import ServiceError


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()  # creates data/nodex.db and the tables if missing
    yield


app = FastAPI(
    title=settings.app_name,
    description="AI-powered knowledge mapping backend",
    version=settings.version,
    lifespan=lifespan,
)

# Lets the frontend dev server (a different port) call this API. Tighten later.
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

app.include_router(health.router)
app.include_router(upload.router)
app.include_router(analyze.router)
app.include_router(graph.router)


@app.exception_handler(ServiceError)
async def service_error_handler(request: Request, exc: ServiceError) -> JSONResponse:
    # Service-layer failures (missing AI key, provider error, unreadable PDF...)
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.message})

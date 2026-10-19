from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from app.config import get_settings
from app.db import init_db
from app.routes.realtime import router as realtime_router
from app.routes.twilio_hooks import router as twilio_router
from app.routes.voice_api import router as voice_api_router
from app.routes.web import RedirectNeeded, router as web_router

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger(__name__)
settings = get_settings()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    log.info("Receptionist ready. Qwen model=%s configured=%s", settings.qwen_model, settings.qwen_configured)
    yield


app = FastAPI(title="Receptionist", docs_url=None, redoc_url=None, lifespan=lifespan)
app.add_middleware(
    SessionMiddleware,
    secret_key=settings.secret_key,
    same_site="lax",
    https_only=settings.public_base_url.startswith("https://"),
)

static_dir = Path(__file__).resolve().parent / "static"
app.mount("/static", StaticFiles(directory=static_dir), name="static")
app.include_router(web_router)
app.include_router(voice_api_router)
app.include_router(twilio_router)
app.include_router(realtime_router)


@app.get("/health")
def health() -> dict:
    return {
        "ok": True,
        "provider": settings.active_provider,
        "qwen_configured": settings.active_configured,
        "model": settings.active_model,
    }


@app.exception_handler(RedirectNeeded)
async def redirect_needed(_request: Request, exc: RedirectNeeded):
    return RedirectResponse(exc.url, status_code=303)

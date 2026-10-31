"""GemmaCommit Pulse — FastAPI application.

Single process serves both the JSON API and the static frontend so deployment
is one command with no build step.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import database
from .ai_service import analyze_diff
from .changelog import build_changelog
from .config import FRONTEND_DIR, STATIC_DIR, get_settings
from .diff_parser import parse_diff

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("gc-pulse")

settings = get_settings()
app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    description="Turn raw Git changes into engineering intelligence.",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

MIN_DIFF_CHARS = 12
DIFF_MARKERS = ("@@", "diff --git", "--- ", "+++ ")


# --------------------------------------------------------------------------- #
# Models
# --------------------------------------------------------------------------- #
class AnalyzeRequest(BaseModel):
    diff: str = Field(..., description="Raw unified diff text")
    title: str | None = Field(default=None, max_length=200)
    save: bool = True


def _derive_title(facts: dict) -> str:
    files = facts.get("files") or []
    if not files:
        return "Release"
    if len(files) == 1:
        return f"Changes to {files[0]['path']}"
    return f"Changes across {len(files)} files"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# --------------------------------------------------------------------------- #
# Lifecycle
# --------------------------------------------------------------------------- #
@app.on_event("startup")
async def _startup() -> None:
    database.init_db(settings.db_path)
    log.info(
        "%s v%s starting | AI mode: %s | models: %s",
        settings.app_name,
        settings.app_version,
        "enabled" if settings.ai_enabled else "DISABLED (heuristic fallback)",
        ", ".join(settings.models_to_try()),
    )


# --------------------------------------------------------------------------- #
# API
# --------------------------------------------------------------------------- #
@app.get("/api/health")
async def health() -> dict:
    return {
        "status": "ok",
        "app": settings.app_name,
        "version": settings.app_version,
        "ai_enabled": settings.ai_enabled,
        "models": settings.models_to_try(),
    }


@app.get("/api/config")
async def config() -> dict:
    return {
        "ai_enabled": settings.ai_enabled,
        "model": settings.gemma_model,
        "models": settings.models_to_try(),
        "max_diff_chars": settings.max_diff_chars,
        "history_limit": settings.history_limit,
    }


@app.post("/api/analyze")
async def analyze(payload: AnalyzeRequest) -> JSONResponse:
    diff = (payload.diff or "").strip()

    if len(diff) < MIN_DIFF_CHARS:
        raise HTTPException(status_code=400, detail="Diff is empty or too short to analyse.")

    if len(diff) > settings.max_diff_chars:
        raise HTTPException(
            status_code=413,
            detail=(
                f"Diff is {len(diff):,} characters — the limit is "
                f"{settings.max_diff_chars:,}. Split it into smaller changes."
            ),
        )

    if not any(marker in diff for marker in DIFF_MARKERS):
        raise HTTPException(
            status_code=422,
            detail=(
                "This does not look like a unified diff. Expected markers such as "
                "'diff --git', '@@', '---' or '+++'. Try `git diff > change.diff`."
            ),
        )

    facts = parse_diff(diff, max_chars=settings.max_diff_chars)
    if not facts["files"]:
        raise HTTPException(
            status_code=422,
            detail="No changed files could be parsed from this diff.",
        )

    result = await analyze_diff(diff, facts)
    created_at = _now()
    title = (payload.title or "").strip() or _derive_title(facts)

    meta = {
        "title": title,
        "created_at": created_at,
        "mode": result["mode"],
        "model": result["model"],
    }

    record = {
        "id": None,
        "created_at": created_at,
        "title": title,
        "mode": result["mode"],
        "model": result["model"],
        "notes": result["notes"],
        "facts": facts,
        "analysis": result["analysis"],
        "changelog": build_changelog(result["analysis"], facts, meta),
    }

    if payload.save:
        record["id"] = database.save_analysis(settings.db_path, record)

    return JSONResponse(record)


@app.get("/api/history")
async def history(limit: int = 25) -> dict:
    return {"items": database.list_analyses(settings.db_path, limit=min(limit, 100))}


@app.get("/api/history/{analysis_id}")
async def history_item(analysis_id: int) -> dict:
    record = database.get_analysis(settings.db_path, analysis_id)
    if not record:
        raise HTTPException(status_code=404, detail="Analysis not found.")
    return record


@app.delete("/api/history/{analysis_id}")
async def history_delete(analysis_id: int) -> dict:
    if not database.delete_analysis(settings.db_path, analysis_id):
        raise HTTPException(status_code=404, detail="Analysis not found.")
    return {"deleted": analysis_id}


@app.delete("/api/history")
async def history_clear() -> dict:
    return {"deleted": database.clear_history(settings.db_path)}


# --------------------------------------------------------------------------- #
# Static frontend
# --------------------------------------------------------------------------- #
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/")
async def index():
    index_file = FRONTEND_DIR / "index.html"
    if not index_file.exists():
        raise HTTPException(status_code=500, detail="Frontend assets are missing.")
    return FileResponse(index_file)


@app.get("/favicon.ico")
async def favicon():
    icon = STATIC_DIR / "favicon.svg"
    if icon.exists():
        return FileResponse(icon, media_type="image/svg+xml")
    return JSONResponse({}, status_code=204)
"""Environment validation and application settings.

Single source of truth for every tunable value. Imported lazily by the
services so the app boots even when optional settings are missing.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

try:  # optional dependency, graceful if absent
    from dotenv import load_dotenv

    load_dotenv()
except Exception:  # pragma: no cover
    pass

BASE_DIR = Path(__file__).resolve().parent.parent
FRONTEND_DIR = BASE_DIR / "frontend"
STATIC_DIR = FRONTEND_DIR / "static"


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, "").strip() or default)
    except (TypeError, ValueError):
        return default


def _float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, "").strip() or default)
    except (TypeError, ValueError):
        return default


@dataclass(frozen=True)
class Settings:
    app_name: str
    app_version: str
    gemma_api_key: str
    gemma_model: str
    gemma_fallback_models: tuple[str, ...]
    max_diff_chars: int
    max_output_tokens: int
    temperature: float
    request_timeout: float
    db_path: str
    history_limit: int

    @property
    def ai_enabled(self) -> bool:
        return bool(self.gemma_api_key)

    def models_to_try(self) -> list[str]:
        """Ordered, de-duplicated list of model ids to attempt."""
        ordered = [self.gemma_model, *self.gemma_fallback_models]
        seen: set[str] = set()
        result: list[str] = []
        for model in ordered:
            model = (model or "").strip()
            if model and model not in seen:
                seen.add(model)
                result.append(model)
        return result


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    fallbacks = tuple(
        m.strip()
        for m in os.getenv("GEMMA_FALLBACK_MODELS", "gemma-3-27b-it,gemini-2.0-flash").split(",")
        if m.strip()
    )
    return Settings(
        app_name="GemmaCommit Pulse",
        app_version="1.0.0",
        gemma_api_key=os.getenv("GEMMA_API_KEY", "").strip(),
        gemma_model=os.getenv("GEMMA_MODEL", "gemma-3-27b-it").strip() or "gemma-3-27b-it",
        gemma_fallback_models=fallbacks,
        max_diff_chars=_int("MAX_DIFF_CHARS", 120_000),
        max_output_tokens=_int("MAX_OUTPUT_TOKENS", 4096),
        temperature=_float("TEMPERATURE", 0.2),
        request_timeout=_float("REQUEST_TIMEOUT", 60.0),
        db_path=os.getenv("DB_PATH", str(BASE_DIR / "data" / "pulse.db")),
        history_limit=_int("HISTORY_LIMIT", 25),
    )
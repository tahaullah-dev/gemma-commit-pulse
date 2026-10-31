"""Gemma integration: prompt construction, structured JSON extraction,
normalisation, retry, and graceful degradation to the heuristic engine.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any

from .config import get_settings
from .diff_parser import heuristic_analysis

log = logging.getLogger("gc-pulse.ai")

_client = None

CANONICAL_CATEGORIES = {
    "feature", "fix", "refactor", "performance", "security",
    "docs", "test", "chore", "dependency", "breaking",
}

CATEGORY_ALIASES = {
    "features": "feature", "feat": "feature", "enhancement": "feature", "new": "feature",
    "bugfix": "fix", "bug": "fix", "bug_fix": "fix", "fixes": "fix", "hotfix": "fix",
    "refactoring": "refactor", "cleanup": "refactor", "chore_refactor": "refactor",
    "perf": "performance", "optimization": "performance",
    "documentation": "docs", "doc": "docs",
    "tests": "test", "testing": "test",
    "deps": "dependency", "dependencies": "dependency", "build": "dependency",
    "breaking_change": "breaking", "breaking_changes": "breaking",
    "ci": "chore", "maintenance": "chore", "misc": "chore",
}

VALID_SEVERITIES = {"info", "low", "medium", "high", "critical"}
VALID_LEVELS = {"low", "medium", "high", "critical"}

PROMPT_TEMPLATE = """You are a staff software engineer performing a pre-merge review of a Git diff.

Return ONLY a single raw JSON object. No markdown fences, no prose before or after, no comments.

The JSON object MUST match this schema exactly:

{{
  "executive_summary": "2-4 sentences describing what this change set does and why it matters",
  "inferred_intent": "one sentence on the likely developer intent",
  "risk_level": "low | medium | high | critical",
  "risk_score": 0,
  "confidence": "high | medium | low",
  "affected_files": [
    {{"path": "exact/path/from/diff", "change_type": "added | modified | deleted | renamed", "summary": "what changed in this file"}}
  ],
  "categories": [
    {{"name": "feature | fix | refactor | performance | security | docs | test | chore | dependency | breaking", "items": ["short bullet"]}}
  ],
  "security_observations": [
    {{"severity": "info | low | medium | high | critical", "detail": "specific observation", "file": "path or empty string"}}
  ],
  "potential_bugs": [
    {{"severity": "info | low | medium | high | critical", "detail": "specific concern", "file": "path or empty string"}}
  ],
  "breaking_changes": [
    {{"detail": "what breaks", "migration": "what a consumer must do"}}
  ]
}}

Rules:
- risk_score is an integer 0-100 reflecting blast radius, not code quality.
- Only describe changes that are actually present in the diff. Never invent files.
- Use an empty array when a section has nothing to report. Do not omit keys.
- Keep every "detail" and "summary" under 220 characters.
- Paths in affected_files must be copied verbatim from the diff.

=== DIFF FACTS (verified, authoritative) ===
{facts}

=== RAW DIFF ===
{diff}
"""


def _get_client():
    global _client
    if _client is None:
        from google import genai

        _client = genai.Client(api_key=get_settings().gemma_api_key)
    return _client


def build_prompt(diff_text: str, facts: dict) -> str:
    compact = [
        {
            "path": f["path"],
            "status": f["status"],
            "additions": f["additions"],
            "deletions": f["deletions"],
        }
        for f in facts.get("files", [])
    ]
    facts_block = json.dumps(
        {"stats": facts.get("stats", {}), "files": compact},
        separators=(",", ":"),
    )
    settings = get_settings()
    diff = diff_text[: settings.max_diff_chars]
    return PROMPT_TEMPLATE.format(facts=facts_block, diff=diff)


async def _call_model(model: str, prompt: str) -> str:
    from google.genai import types

    settings = get_settings()
    client = _get_client()
    config = types.GenerateContentConfig(
        temperature=settings.temperature,
        max_output_tokens=settings.max_output_tokens,
    )
    response = await asyncio.wait_for(
        client.aio.models.generate_content(model=model, contents=prompt, config=config),
        timeout=settings.request_timeout,
    )

    text = getattr(response, "text", None)
    if text and text.strip():
        return text

    # Defensive extraction across SDK shapes
    chunks: list[str] = []
    for candidate in getattr(response, "candidates", None) or []:
        content = getattr(candidate, "content", None)
        for part in getattr(content, "parts", None) or []:
            piece = getattr(part, "text", None)
            if piece:
                chunks.append(piece)
    text = "".join(chunks)
    if not text.strip():
        raise ValueError("Model returned an empty response")
    return text


_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL | re.IGNORECASE)


def extract_json(raw: str) -> dict:
    """Best-effort JSON recovery from model output. Raises ValueError."""
    text = (raw or "").strip()
    if not text:
        raise ValueError("Empty model output")

    candidates: list[str] = [text]

    fence = _FENCE_RE.search(text)
    if fence:
        candidates.append(fence.group(1).strip())

    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        candidates.append(text[start:end + 1])

    for candidate in candidates:
        for attempt in (candidate, re.sub(r",\s*([}\]])", r"\1", candidate)):
            try:
                parsed = json.loads(attempt)
            except (json.JSONDecodeError, TypeError):
                continue
            return _unwrap(parsed)

    raise ValueError("Could not parse JSON from model output")


def _unwrap(parsed: Any) -> dict:
    if isinstance(parsed, list):
        for item in parsed:
            if isinstance(item, dict):
                return item
        raise ValueError("Model returned a list without objects")
    if not isinstance(parsed, dict):
        raise ValueError("Model returned a non-object JSON payload")
    for key in ("analysis", "result", "data", "output"):
        inner = parsed.get(key)
        if isinstance(inner, dict) and len(parsed) <= 2:
            return inner
    return parsed


def _as_str(value: Any, default: str = "") -> str:
    return value.strip() if isinstance(value, str) and value.strip() else default


def _as_list(value: Any) -> list:
    return value if isinstance(value, list) else []


def _norm_category_name(name: Any) -> str:
    key = _as_str(name).lower().replace(" ", "_").replace("-", "_")
    key = CATEGORY_ALIASES.get(key, key)
    return key if key in CANONICAL_CATEGORIES else "chore"


def _norm_severity(value: Any) -> str:
    sev = _as_str(value).lower()
    return sev if sev in VALID_SEVERITIES else "medium"


def _stringify_items(items: list) -> list[str]:
    out: list[str] = []
    for item in items[:15]:
        if isinstance(item, str):
            text = item.strip()
        elif isinstance(item, dict):
            text = _as_str(item.get("detail") or item.get("title") or item.get("description"))
        else:
            text = str(item).strip()
        if text:
            out.append(text[:280])
    return out


def normalize(data: dict, facts: dict) -> dict:
    """Coerce arbitrary model output into the contract the frontend expects."""
    known_paths = {f["path"] for f in facts.get("files", [])}

    affected: list[dict] = []
    for entry in _as_list(data.get("affected_files"))[:60]:
        if isinstance(entry, str):
            path, summary, change_type = entry.strip(), "", "modified"
        elif isinstance(entry, dict):
            path = _as_str(entry.get("path") or entry.get("file"))
            summary = _as_str(entry.get("summary") or entry.get("description"))
            change_type = _as_str(entry.get("change_type") or entry.get("status"), "modified").lower()
        else:
            continue
        if not path:
            continue
        if change_type not in {"added", "modified", "deleted", "renamed"}:
            change_type = "modified"
        affected.append({
            "path": path,
            "change_type": change_type,
            "summary": summary[:280],
            "verified": path in known_paths,
        })

    categories: list[dict] = []
    bucket: dict[str, list[str]] = {}
    for entry in _as_list(data.get("categories"))[:12]:
        if not isinstance(entry, dict):
            continue
        name = _norm_category_name(entry.get("name"))
        bucket.setdefault(name, []).extend(_stringify_items(_as_list(entry.get("items"))))
    for name, items in bucket.items():
        if items:
            categories.append({"name": name, "items": items[:12]})

    def _findings(key: str) -> list[dict]:
        out: list[dict] = []
        for entry in _as_list(data.get(key))[:15]:
            if isinstance(entry, str):
                out.append({"severity": "medium", "detail": entry[:280], "file": ""})
            elif isinstance(entry, dict):
                detail = _as_str(entry.get("detail") or entry.get("description") or entry.get("issue"))
                if not detail:
                    continue
                out.append({
                    "severity": _norm_severity(entry.get("severity")),
                    "detail": detail[:280],
                    "file": _as_str(entry.get("file") or entry.get("path"))[:200],
                })
        return out

    breaking: list[dict] = []
    for entry in _as_list(data.get("breaking_changes"))[:10]:
        if isinstance(entry, str):
            breaking.append({"detail": entry[:280], "migration": ""})
        elif isinstance(entry, dict):
            detail = _as_str(entry.get("detail") or entry.get("description"))
            if detail:
                breaking.append({
                    "detail": detail[:280],
                    "migration": _as_str(entry.get("migration"))[:280],
                })

    try:
        score = int(float(data.get("risk_score", 0)))
    except (TypeError, ValueError):
        score = 0
    score = max(0, min(100, score))

    level = _as_str(data.get("risk_level")).lower()
    if level not in VALID_LEVELS:
        level = "low" if score < 30 else "medium" if score < 55 else "high" if score < 80 else "critical"

    confidence = _as_str(data.get("confidence")).lower()
    if confidence not in {"high", "medium", "low"}:
        confidence = "medium"

    return {
        "executive_summary": _as_str(
            data.get("executive_summary") or data.get("summary"),
            "No summary was produced for this diff.",
        )[:1200],
        "inferred_intent": _as_str(
            data.get("inferred_intent") or data.get("intent"),
            "Intent could not be determined from the diff.",
        )[:400],
        "risk_score": score,
        "risk_level": level,
        "confidence": confidence,
        "affected_files": affected,
        "categories": categories,
        "security_observations": _findings("security_observations"),
        "potential_bugs": _findings("potential_bugs"),
        "breaking_changes": breaking,
    }


def _fallback(diff_text: str, facts: dict, notes: list[str]) -> dict:
    return {
        "mode": "heuristic",
        "model": None,
        "notes": notes,
        "analysis": heuristic_analysis(diff_text, facts),
    }


async def analyze_diff(diff_text: str, facts: dict) -> dict:
    """Analyse a diff. Always returns a usable result, never raises."""
    settings = get_settings()
    if not settings.ai_enabled:
        return _fallback(
            diff_text, facts,
            ["GEMMA_API_KEY is not configured — deterministic heuristic engine used."],
        )

    prompt = build_prompt(diff_text, facts)
    errors: list[str] = []

    for model in settings.models_to_try():
        for attempt in (1, 2):
            try:
                active_prompt = prompt
                if attempt == 2:
                    active_prompt += (
                        "\n\nREMINDER: Your previous response was not valid JSON. "
                        "Respond with the raw JSON object only — no fences, no commentary."
                    )
                raw = await _call_model(model, active_prompt)
                analysis = normalize(extract_json(raw), facts)
                notes: list[str] = []
                if attempt == 2:
                    notes.append("Recovered on a second attempt with a stricter instruction.")
                return {"mode": "ai", "model": model, "notes": notes, "analysis": analysis}

            except ValueError as exc:
                errors.append(f"{model}: unparsable output ({exc})")
                log.warning("Model %s returned unparsable output (attempt %s)", model, attempt)
                continue  # retry same model with stricter instruction
            except Exception as exc:  # transport, auth, quota, timeout...
                errors.append(f"{model}: {type(exc).__name__}: {exc}")
                log.warning("Model %s failed: %s", model, exc)
                break  # move to next model

    return _fallback(
        diff_text, facts,
        ["Gemma was unavailable or unparsable — heuristic engine used."] + errors[-3:],
    )
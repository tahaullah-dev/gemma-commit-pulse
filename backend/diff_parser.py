"""Deterministic unified-diff parsing.

This module is the *trust* layer: everything it reports is derived directly
from the diff text, never from a model. It also powers the offline heuristic
engine used when the Gemma call fails.
"""
from __future__ import annotations

import re
from typing import Iterator

FILE_RE = re.compile(r"^diff --git a/(.+?) b/(.+)$")
HUNK_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+\d+(?:,\d+)? @@")
MINUS_HDR_RE = re.compile(r"^--- (.+)$")
PLUS_HDR_RE = re.compile(r"^\+\+\+ (.+)$")

MAX_ADDED_LINES_SCAN = 50_000

# (regex, label, severity) — tuned for signal over noise.
SECURITY_PATTERNS: list[tuple[str, str, str]] = [
    (r"""(?i)\b(api[_-]?key|secret|token|passwd|password)\b\s*[:=]\s*['"][^'"]{8,}['"]""",
     "Possible hardcoded credential or secret", "high"),
    (r"\bAKIA[0-9A-Z]{16}\b", "AWS access key pattern detected", "critical"),
    (r"(?i)-----BEGIN (RSA |EC |OPENSSH )?PRIVATE KEY-----", "Private key material committed", "critical"),
    (r"\beval\s*\(", "Dynamic eval() usage", "high"),
    (r"\bexec\s*\(", "Dynamic exec() usage", "medium"),
    (r"\bos\.system\s*\(", "Shell command via os.system()", "high"),
    (r"subprocess\.\w+\([^)]*shell\s*=\s*True", "subprocess with shell=True", "high"),
    (r"(?i)verify\s*=\s*False", "TLS certificate verification disabled", "high"),
    (r"(?i)\bmd5\s*\(", "Weak hash algorithm (MD5)", "medium"),
    (r"(?i)\b(DROP|TRUNCATE)\s+(TABLE|DATABASE)\b", "Destructive SQL statement", "critical"),
    (r"(?i)\bDEBUG\s*=\s*True\b", "Debug mode enabled", "low"),
    (r"dangerouslySetInnerHTML", "Raw HTML injection sink (React)", "medium"),
]

TEST_HINTS = ("test", "spec", "__tests__", "tests/")
DOC_HINTS = (".md", ".rst", ".adoc", "docs/", "readme")
DEP_HINTS = ("package.json", "requirements", "poetry.lock", "pyproject.toml",
             "cargo.toml", "go.mod", "gemfile", "yarn.lock", "package-lock.json")
CI_HINTS = (".github/workflows", ".gitlab-ci", "dockerfile", "makefile", ".circleci")


def _strip_prefix(path: str) -> str:
    p = path.strip()
    if p.startswith(("a/", "b/")):
        return p[2:]
    return p


def _new_entry(path: str) -> dict:
    return {
        "path": path,
        "old_path": None,
        "status": "modified",
        "additions": 0,
        "deletions": 0,
        "hunks": 0,
    }


def parse_diff(diff_text: str, max_chars: int = 120_000) -> dict:
    """Parse a unified diff into structured, verifiable facts."""
    truncated = len(diff_text) > max_chars
    text = diff_text[:max_chars] if truncated else diff_text

    files: list[dict] = []
    current: dict | None = None
    in_hunk = False

    for line in text.splitlines():
        if line.startswith("diff --git"):
            m = FILE_RE.match(line)
            path = m.group(2) if m else line.split(" b/")[-1].strip()
            current = _new_entry(path)
            files.append(current)
            in_hunk = False
            continue

        if line.startswith("new file mode"):
            if current:
                current["status"] = "added"
            continue

        if line.startswith("deleted file mode"):
            if current:
                current["status"] = "deleted"
            continue

        if line.startswith("rename from ") and current:
            current["old_path"] = line[len("rename from "):].strip()
            current["status"] = "renamed"
            continue

        if line.startswith("rename to ") and current:
            current["path"] = line[len("rename to "):].strip()
            current["status"] = "renamed"
            continue

        if line.startswith("--- "):
            target = MINUS_HDR_RE.match(line)
            raw = target.group(1).strip() if target else ""
            if raw == "/dev/null":
                if current:
                    current["status"] = "added"
            elif current is None:
                current = _new_entry(_strip_prefix(raw))
                files.append(current)
            continue

        if line.startswith("+++ "):
            target = PLUS_HDR_RE.match(line)
            raw = target.group(1).strip() if target else ""
            if raw == "/dev/null":
                if current:
                    current["status"] = "deleted"
            elif current is not None:
                current["path"] = _strip_prefix(raw)
            continue

        if HUNK_RE.match(line):
            in_hunk = True
            if current:
                current["hunks"] += 1
            continue

        if in_hunk and current is not None:
            if line.startswith("+"):
                current["additions"] += 1
            elif line.startswith("-"):
                current["deletions"] += 1

    total_add = sum(f["additions"] for f in files)
    total_del = sum(f["deletions"] for f in files)
    total_hunks = sum(f["hunks"] for f in files)

    return {
        "files": files,
        "stats": {
            "files_changed": len(files),
            "additions": total_add,
            "deletions": total_del,
            "hunks": total_hunks,
            "chars": len(text),
            "truncated": truncated,
        },
    }


def iter_added_lines(diff_text: str) -> Iterator[tuple[str, str]]:
    """Yield (file_path, added_line) for every '+' line, skipping headers."""
    current_path = "unknown"
    in_hunk = False
    emitted = 0

    for line in diff_text.splitlines():
        if line.startswith("diff --git"):
            m = FILE_RE.match(line)
            current_path = m.group(2) if m else current_path
            in_hunk = False
            continue
        if line.startswith("+++ "):
            target = PLUS_HDR_RE.match(line)
            if target:
                raw = _strip_prefix(target.group(1))
                if raw != "/dev/null":
                    current_path = raw
            continue
        if line.startswith("--- "):
            continue
        if HUNK_RE.match(line):
            in_hunk = True
            continue
        if in_hunk and line.startswith("+"):
            emitted += 1
            if emitted > MAX_ADDED_LINES_SCAN:
                return
            yield current_path, line[1:]


def _classify_path(path: str) -> str:
    low = path.lower()
    if any(h in low for h in TEST_HINTS):
        return "test"
    if any(h in low for h in DOC_HINTS):
        return "docs"
    if any(h in low for h in DEP_HINTS):
        return "dependency"
    if any(h in low for h in CI_HINTS):
        return "chore"
    return "feature"


def heuristic_analysis(diff_text: str, facts: dict) -> dict:
    """Deterministic fallback analysis. Never fails, never calls the network."""
    stats = facts.get("stats", {})
    files = facts.get("files", [])

    observations: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for path, line in iter_added_lines(diff_text):
        for pattern, label, severity in SECURITY_PATTERNS:
            if (path, label) in seen:
                continue
            if re.search(pattern, line):
                seen.add((path, label))
                observations.append({
                    "severity": severity,
                    "detail": f"{label}: {line.strip()[:160]}",
                    "file": path,
                })
                if len(observations) >= 12:
                    break
        if len(observations) >= 12:
            break

    risky_paths = [
        f["path"] for f in files
        if re.search(r"(?i)(auth|login|session|token|crypto|payment|billing|migration|config)", f["path"])
    ]

    score = 20
    score += min(25, stats.get("files_changed", 0) * 3)
    score += min(20, (stats.get("additions", 0) + stats.get("deletions", 0)) // 60)
    score += min(30, len(observations) * 8)
    score += min(15, len(risky_paths) * 5)
    if stats.get("deletions", 0) > stats.get("additions", 0) and stats.get("deletions", 0) > 30:
        score += 10
    score = max(0, min(100, score))

    level = "low" if score < 30 else "medium" if score < 55 else "high" if score < 80 else "critical"

    by_cat: dict[str, list[str]] = {}
    for f in files:
        cat = _classify_path(f["path"])
        by_cat.setdefault(cat, []).append(
            f"{f['path']} ({f['status']}, +{f['additions']}/-{f['deletions']})"
        )

    deleted = [f["path"] for f in files if f["status"] == "deleted"]

    summary = (
        f"This change set touches {stats.get('files_changed', 0)} file(s) with "
        f"+{stats.get('additions', 0)}/−{stats.get('deletions', 0)} lines across "
        f"{stats.get('hunks', 0)} hunk(s). "
        f"{'Security-sensitive patterns were detected and should be reviewed. ' if observations else ''}"
        f"{'Deleted files may indicate breaking changes. ' if deleted else ''}"
        "This summary was produced by the offline heuristic engine, not a language model."
    )

    return {
        "executive_summary": summary,
        "inferred_intent": "Heuristic mode cannot infer developer intent. Review the diff facts directly.",
        "risk_score": score,
        "risk_level": level,
        "confidence": "low",
        "affected_files": [
            {
                "path": f["path"],
                "change_type": f["status"],
                "summary": f"+{f['additions']}/-{f['deletions']} across {f['hunks']} hunk(s)",
                "verified": True,
            }
            for f in files
        ],
        "categories": [
            {"name": name, "items": items[:12]} for name, items in by_cat.items()
        ],
        "security_observations": observations,
        "potential_bugs": [],
        "breaking_changes": [
            {"detail": f"File deleted: {p}", "migration": "Confirm no remaining imports or references."}
            for p in deleted[:5]
        ],
    }
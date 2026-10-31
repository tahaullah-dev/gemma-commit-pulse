# ⚡ GemmaCommit Pulse

**Turn raw Git changes into engineering intelligence.**

Paste a unified Git diff and get back a structured review: risk score, verified diff
facts, categorized changes, security observations, potential bugs, breaking changes,
and a GitHub-ready Markdown changelog — all generated in one request.

Built for **Hacktoberfest Hack Day 2026**.

---

## Why it's different

Most "AI diff review" tools give you a wall of model prose you can't verify.
GemmaCommit Pulse splits the output into two visually distinct layers:

| Layer | Source | Trust badge |
|---|---|---|
| **Diff facts** — files, statuses, +/− counts, hunks | Parsed deterministically from the diff | ✅ Verified from diff |
| **Analysis** — summary, risk, categories, security, bugs | Gemma reasoning | 🤖 AI-inferred |

Every file the model mentions is cross-checked against the parsed diff and flagged
if the path doesn't exist. The model can't hallucinate a file list past you.

---

## Quick start

```bash
git clone <your-repo> && cd gemma-commit-pulse

python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate

pip install -r requirements.txt

cp .env.example .env
# edit .env and set GEMMA_API_KEY

uvicorn backend.main:app --reload --port 8000
/* GemmaCommit Pulse — frontend controller. Vanilla JS, no build step. */
(function () {
  "use strict";

  const SAMPLE_DIFF = `diff --git a/backend/auth.py b/backend/auth.py
index 83f2a1e..b7c9d02 100644
--- a/backend/auth.py
+++ b/backend/auth.py
@@ -1,18 +1,34 @@
-import hashlib
+import hmac
+import os
+import time
+from datetime import datetime, timedelta, timezone
+
+import jwt
+
+JWT_SECRET = "dev-secret-please-change"
+TOKEN_TTL_MINUTES = int(os.getenv("TOKEN_TTL_MINUTES", "60"))
 
-def hash_password(password):
-    return hashlib.md5(password.encode()).hexdigest()
 
+def hash_password(password: str) -> str:
+    return hmac.new(JWT_SECRET.encode(), password.encode(), "sha256").hexdigest()
 
-def verify(user, password):
-    return user.password == hash_password(password)
+
+def verify(user, password: str) -> bool:
+    return hmac.compare_digest(user.password, hash_password(password))
+
+
+def issue_token(user) -> str:
+    payload = {
+        "sub": user.id,
+        "exp": datetime.now(timezone.utc) + timedelta(minutes=TOKEN_TTL_MINUTES),
+    }
+    return jwt.encode(payload, JWT_SECRET, algorithm="HS256")
 
 
 def decode(token: str) -> dict:
-    return jwt.decode(token, options={"verify_signature": False})
+    return jwt.decode(token, JWT_SECRET, algorithms=["HS256"])
diff --git a/backend/reporting.py b/backend/reporting.py
deleted file mode 100644
--- a/backend/reporting.py
+++ /dev/null
@@ -1,12 +0,0 @@
-import subprocess
-
-
-def render(report_id):
-    cmd = f"python scripts/render.py {report_id}"
-    return subprocess.call(cmd, shell=True)
-
-
-def fetch(url):
-    import requests
-    return requests.get(url, verify=False).text
diff --git a/scripts/deploy.sh b/scripts/deploy.sh
new file mode 100755
--- /dev/null
+++ b/scripts/deploy.sh
@@ -0,0 +1,9 @@
+#!/usr/bin/env bash
+set -euo pipefail
+
+export AWS_ACCESS_KEY_ID="AKIAIOSFODNN7EXAMPLE"
+export AWS_SECRET_ACCESS_KEY="wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"
+
+echo "Deploying $1"
+docker build -t app:"$1" .
+docker push app:"$1"
diff --git a/README.md b/README.md
index 1122334..5566778 100644
--- a/README.md
+++ b/README.md
@@ -10,6 +10,9 @@
 ## Authentication
 
 Tokens are issued by \`backend/auth.py\`.
+
+Set \`TOKEN_TTL_MINUTES\` to control token lifetime. Defaults to 60.
+Rotate \`JWT_SECRET\` before deploying to production.
 
 ## License
`;

  const $ = (id) => document.getElementById(id);

  const state = {
    config: null,
    result: null,
    busy: false,
  };

  /* ------------------------------ utilities ------------------------------ */

  function escapeHtml(value) {
    return String(value == null ? "" : value)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  function detailOf(payload, fallback) {
    if (!payload) return fallback;
    if (typeof payload === "string") return payload;
    if (Array.isArray(payload)) {
      return payload.map((e) => (e && e.msg ? e.msg : JSON.stringify(e))).join("; ");
    }
    if (payload.detail) return detailOf(payload.detail, fallback);
    return JSON.stringify(payload);
  }

  async function api(path, options) {
    const response = await fetch(path, options);
    let data = null;
    try { data = await response.json(); } catch (_) { data = null; }
    if (!response.ok) {
      throw new Error(detailOf(data, `Request failed with status ${response.status}`));
    }
    return data;
  }

  function showError(message) {
    const banner = $("error-banner");
    banner.textContent = message;
    banner.classList.remove("hidden");
  }

  function clearError() {
    $("error-banner").classList.add("hidden");
    $("error-banner").textContent = "";
  }

  const RISK_STYLES = {
    low:      { bar: "bg-emerald-500", chip: "bg-emerald-500/15 text-emerald-300 border border-emerald-500/30", text: "text-emerald-400" },
    medium:   { bar: "bg-amber-500",   chip: "bg-amber-500/15 text-amber-300 border border-amber-500/30",       text: "text-amber-400" },
    high:     { bar: "bg-orange-500",  chip: "bg-orange-500/15 text-orange-300 border border-orange-500/30",    text: "text-orange-400" },
    critical: { bar: "bg-rose-500",    chip: "bg-rose-500/15 text-rose-300 border border-rose-500/30",          text: "text-rose-400" },
  };

  const SEVERITY_STYLES = {
    info:     "bg-slate-500/15 text-slate-300 border border-slate-500/30",
    low:      "bg-emerald-500/15 text-emerald-300 border border-emerald-500/30",
    medium:   "bg-amber-500/15 text-amber-300 border border-amber-500/30",
    high:     "bg-orange-500/15 text-orange-300 border border-orange-500/30",
    critical: "bg-rose-500/15 text-rose-300 border border-rose-500/30",
  };

  const STATUS_STYLES = {
    added:    "bg-emerald-500/15 text-emerald-300",
    modified: "bg-sky-500/15 text-sky-300",
    deleted:  "bg-rose-500/15 text-rose-300",
    renamed:  "bg-violet-500/15 text-violet-300",
  };

  const CATEGORY_ICONS = {
    feature: "🚀", fix: "🐛", refactor: "♻️", performance: "⚡", security: "🔒",
    docs: "📝", test: "✅", chore: "🧹", dependency: "📦", breaking: "💥",
  };

  function severityBadge(severity) {
    const key = (severity || "info").toLowerCase();
    const cls = SEVERITY_STYLES[key] || SEVERITY_STYLES.info;
    return `<span class="rounded px-1.5 py-0.5 text-[10px] font-bold uppercase tracking-wide ${cls}">${escapeHtml(key)}</span>`;
  }

  /* ------------------------------- config -------------------------------- */

  async function loadConfig() {
    const pill = $("mode-pill");
    try {
      state.config = await api("/api/config");
      if (state.config.ai_enabled) {
        pill.className = "rounded-full border border-emerald-500/40 bg-emerald-500/10 px-3 py-1 text-[11px] font-semibold text-emerald-300";
        pill.innerHTML = `<span class="hidden sm:inline">AI ready · </span><span class="sm:hidden">AI Ready</span><span class="hidden sm:inline">${escapeHtml(state.config.model)}</span>`;
        pill.title = `Models: ${state.config.models.join(", ")}`;
      } else {
        pill.className = "rounded-full border border-amber-500/40 bg-amber-500/10 px-3 py-1 text-[11px] font-semibold text-amber-300";
        pill.textContent = "Heuristic mode (no API key)";
        pill.title = "Set GEMMA_API_KEY to enable Gemma analysis.";
      }
    } catch (_) {
      pill.className = "rounded-full border border-rose-500/40 bg-rose-500/10 px-3 py-1 text-[11px] font-semibold text-rose-300";
      pill.textContent = "backend offline";
    }
  }

  /* ------------------------------ rendering ------------------------------ */

  function renderMeta(result) {
    const badges = [];
    if (result.mode === "ai") {
      badges.push(`<span class="max-w-full truncate rounded-full bg-indigo-500/15 px-2 py-0.5 text-[10px] font-bold uppercase tracking-wide text-indigo-300">Gemma · ${escapeHtml(result.model)}</span>`);
    } else {
      badges.push(`<span class="rounded-full bg-amber-500/15 px-2 py-0.5 text-[10px] font-bold uppercase tracking-wide text-amber-300">Heuristic engine</span>`);
    }
    if (result.id) {
      badges.push(`<span class="shrink-0 rounded-full bg-slate-800 px-2 py-0.5 text-[10px] font-semibold text-slate-400">#${result.id}</span>`);
    }
    badges.push(`<span class="w-full text-sm font-semibold text-slate-200 sm:w-auto">${escapeHtml(result.title)}</span>`);
    badges.push(`<span class="hidden text-slate-600 sm:inline">·</span>`);
    badges.push(`<span class="text-[11px] text-slate-500">${escapeHtml(result.created_at)}</span>`);
    
    $("meta-line").innerHTML = badges.join(" ");
    // Force flex layout to allow wrapping cleanly on mobile
    $("meta-line").className = "flex w-full flex-wrap items-center gap-2 text-xs text-slate-400";
    
    // ... (keep the notes part exactly as it was)
    if (Array.isArray(result.notes) && result.notes.length) {
      const note = document.createElement("div");
      note.className = "w-full rounded-lg border border-amber-500/30 bg-amber-500/5 px-3 py-2 text-[11px] text-amber-200";
      note.textContent = result.notes.join(" · ");
      $("meta-line").appendChild(note);
    }
  }

  function renderRisk(analysis) {
    const level = (analysis.risk_level || "low").toLowerCase();
    const style = RISK_STYLES[level] || RISK_STYLES.low;
    const score = Number(analysis.risk_score) || 0;

    $("risk-score").textContent = String(score);
    $("risk-score").className = `text-5xl font-black leading-none ${style.text}`;
    $("risk-level").textContent = level;
    $("risk-level").className = `mb-1 rounded-full px-2.5 py-1 text-[11px] font-bold uppercase tracking-wide ${style.chip}`;
    $("confidence").textContent = analysis.confidence || "—";

    const bar = $("risk-bar");
    bar.className = `h-full rounded-full transition-all duration-700 ${style.bar}`;
    requestAnimationFrame(() => { bar.style.width = `${score}%`; });
  }

  function renderFacts(facts) {
    const stats = facts.stats || {};
    const cells = [
      ["Files changed", stats.files_changed ?? 0],
      ["Additions", `+${stats.additions ?? 0}`],
      ["Deletions", `−${stats.deletions ?? 0}`],
      ["Hunks", stats.hunks ?? 0],
    ];
    $("facts-grid").innerHTML = cells.map(([label, value]) => `
      <div class="rounded-xl border border-slate-800 bg-slate-950/50 px-3 py-2">
        <p class="text-[10px] font-semibold uppercase tracking-wider text-slate-500">${escapeHtml(label)}</p>
        <p class="mt-1 text-xl font-bold text-slate-100">${escapeHtml(value)}</p>
      </div>`).join("");

    if (stats.truncated) {
      $("facts-grid").insertAdjacentHTML("beforeend", `
        <div class="col-span-full rounded-lg border border-amber-500/30 bg-amber-500/5 px-3 py-2 text-[11px] text-amber-200">
          Diff was truncated to ${Number(stats.chars).toLocaleString()} characters before analysis.
        </div>`);
    }

    const files = facts.files || [];
    $("files-body").innerHTML = files.map((file) => {
      const badge = STATUS_STYLES[file.status] || STATUS_STYLES.modified;
      return `
        <tr class="hover:bg-slate-950/40">
          <td class="px-3 py-2 font-mono text-xs text-slate-300">${escapeHtml(file.path)}</td>
          <td class="px-3 py-2"><span class="rounded px-2 py-0.5 text-[10px] font-bold uppercase tracking-wide ${badge}">${escapeHtml(file.status)}</span></td>
          <td class="px-3 py-2 text-right font-mono text-xs text-emerald-400">+${escapeHtml(file.additions)}</td>
          <td class="px-3 py-2 text-right font-mono text-xs text-rose-400">−${escapeHtml(file.deletions)}</td>
        </tr>`;
    }).join("") || `<tr><td colspan="4" class="px-3 py-3 text-center text-xs text-slate-500">No files parsed.</td></tr>`;
  }

  function renderCategories(categories) {
    const container = $("categories");
    const section = $("categories-section");

    if (!categories || !categories.length) {
      section.classList.add("hidden");
      return;
    }
    section.classList.remove("hidden");

    container.innerHTML = categories.map((category) => {
      const icon = CATEGORY_ICONS[category.name] || "•";
      const label = category.name.charAt(0).toUpperCase() + category.name.slice(1);
      const items = (category.items || []).map(
        (item) => `<li class="flex gap-2 text-sm text-slate-300"><span class="mt-1.5 h-1 w-1 shrink-0 rounded-full bg-slate-600"></span><span>${escapeHtml(item)}</span></li>`
      ).join("");
      return `
        <div class="rounded-xl border border-slate-800 bg-slate-950/50 p-4">
          <p class="text-sm font-semibold text-slate-200">${icon} ${escapeHtml(label)}</p>
          <ul class="mt-2 space-y-1.5">${items}</ul>
        </div>`;
    }).join("");
  }

  function renderFindings(targetId, entries, emptyMessage) {
    const list = $(targetId);
    if (!entries || !entries.length) {
      list.innerHTML = `<li class="text-xs text-slate-500">${escapeHtml(emptyMessage)}</li>`;
      return;
    }
    list.innerHTML = entries.map((entry) => `
      <li class="rounded-lg border border-slate-800 bg-slate-950/50 px-3 py-2">
        <div class="flex flex-wrap items-center gap-2">
          ${severityBadge(entry.severity)}
          ${entry.file ? `<span class="font-mono text-[11px] text-slate-500">${escapeHtml(entry.file)}</span>` : ""}
        </div>
        <p class="mt-1 text-sm text-slate-300">${escapeHtml(entry.detail)}</p>
      </li>`).join("");
  }

  function renderBreaking(entries) {
    const section = $("breaking-section");
    if (!entries || !entries.length) {
      section.classList.add("hidden");
      return;
    }
    section.classList.remove("hidden");
    $("breaking-list").innerHTML = entries.map((entry) => `
      <li class="rounded-lg border border-amber-500/25 bg-amber-500/5 px-3 py-2">
        <p class="text-sm font-semibold text-amber-200">${escapeHtml(entry.detail)}</p>
        ${entry.migration ? `<p class="mt-1 text-xs text-amber-200/70"><span class="font-semibold">Migration:</span> ${escapeHtml(entry.migration)}</p>` : ""}
      </li>`).join("");
  }

  function render(result) {
    state.result = result;
    const analysis = result.analysis || {};

    renderMeta(result);
    renderRisk(analysis);
    $("summary-text").textContent = analysis.executive_summary || "—";
    $("intent-text").textContent = analysis.inferred_intent || "—";
    renderFacts(result.facts || {});
    renderCategories(analysis.categories);
    renderFindings("security-list", analysis.security_observations, "No security observations.");
    renderFindings("bugs-list", analysis.potential_bugs, "No potential bugs flagged.");
    renderBreaking(analysis.breaking_changes);
    $("changelog-box").textContent = result.changelog || "";

    const panel = $("results");
    panel.classList.remove("hidden");
    panel.classList.remove("fade-in");
    void panel.offsetWidth;
    panel.classList.add("fade-in");
  }

  /* ------------------------------- actions ------------------------------- */

  async function analyze() {
    if (state.busy) return;
    clearError();

    const diff = $("diff-input").value.trim();
    if (!diff) {
      showError("Paste a git diff or load the sample first.");
      return;
    }

    state.busy = true;
    $("btn-analyze").disabled = true;
    $("btn-analyze").querySelector("span").textContent = "Analyzing…";
    $("loading").classList.remove("hidden");
    $("results").classList.add("hidden");

    try {
      const result = await api("/api/analyze", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ diff, save: true }),
      });
      render(result);
      await loadHistory();
    } catch (error) {
      showError(error.message || "Analysis failed.");
    } finally {
      state.busy = false;
      $("btn-analyze").disabled = false;
      $("btn-analyze").querySelector("span").textContent = "Analyze with Gemma";
      $("loading").classList.add("hidden");
    }
  }

  async function loadHistory() {
    const list = $("history-list");
    try {
      const data = await api("/api/history?limit=25");
      const items = data.items || [];
      if (!items.length) {
        list.innerHTML = `<li class="text-xs text-slate-500">No saved analyses yet.</li>`;
        return;
      }
      list.innerHTML = items.map((item) => {
        const style = RISK_STYLES[(item.risk_level || "low").toLowerCase()] || RISK_STYLES.low;
        return `
          <li class="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-slate-800 bg-slate-950/50 px-3 py-2">
            <button data-load="${item.id}" class="flex-1 text-left">
              <span class="text-sm font-medium text-slate-200">${escapeHtml(item.title)}</span>
              <span class="ml-2 rounded px-1.5 py-0.5 text-[10px] font-bold uppercase ${style.chip}">${escapeHtml(item.risk_level || "low")}</span>
              <span class="ml-2 text-[11px] text-slate-500">${escapeHtml(item.created_at || "")}</span>
              <p class="mt-0.5 line-clamp-1 text-xs text-slate-500">${escapeHtml(item.summary || "")}</p>
            </button>
            <button data-delete="${item.id}" class="rounded border border-slate-700 px-2 py-1 text-[11px] text-slate-400 transition hover:border-rose-500/60 hover:text-rose-300">Delete</button>
          </li>`;
      }).join("");
    } catch (error) {
      list.innerHTML = `<li class="text-xs text-rose-300">Could not load history: ${escapeHtml(error.message)}</li>`;
    }
  }

  async function loadRecord(id) {
    clearError();
    try {
      const record = await api(`/api/history/${id}`);
      render(record);
      $("results").scrollIntoView({ behavior: "smooth", block: "start" });
    } catch (error) {
      showError(error.message);
    }
  }

  async function deleteRecord(id) {
    try {
      await api(`/api/history/${id}`, { method: "DELETE" });
      await loadHistory();
    } catch (error) {
      showError(error.message);
    }
  }

  async function clearHistory() {
    if (!window.confirm("Delete all saved analyses?")) return;
    try {
      await api("/api/history", { method: "DELETE" });
      await loadHistory();
    } catch (error) {
      showError(error.message);
    }
  }

  async function copyChangelog() {
    const text = $("changelog-box").textContent || "";
    if (!text) return;
    const button = $("btn-copy");
    const original = button.textContent;
    try {
      await navigator.clipboard.writeText(text);
      button.textContent = "Copied ✓";
    } catch (_) {
      // clipboard API can be blocked — fall back to a selection-based copy
      const range = document.createRange();
      range.selectNodeContents($("changelog-box"));
      const selection = window.getSelection();
      selection.removeAllRanges();
      selection.addRange(range);
      button.textContent = "Press Ctrl+C";
    }
    setTimeout(() => { button.textContent = original; }, 1800);
  }

  /* ------------------------------- wiring -------------------------------- */

  function updateCharCount() {
    const value = $("diff-input").value;
    $("char-count").textContent = `${value.length.toLocaleString()} chars`;
  }

  function readFile(file) {
    if (!file) return;
    if (file.size > 5 * 1024 * 1024) {
      showError("File is larger than 5 MB.");
      return;
    }
    const reader = new FileReader();
    reader.onload = () => {
      $("diff-input").value = String(reader.result || "");
      updateCharCount();
      const badge = $("file-name");
      badge.textContent = file.name;
      badge.classList.remove("hidden");
      clearError();
    };
    reader.onerror = () => showError("Could not read that file.");
    reader.readAsText(file);
  }

  function bind() {
    $("diff-input").addEventListener("input", updateCharCount);

    $("btn-analyze").addEventListener("click", analyze);
    $("btn-copy").addEventListener("click", copyChangelog);
    $("btn-refresh-history").addEventListener("click", loadHistory);
    $("btn-clear-history").addEventListener("click", clearHistory);

    $("btn-sample").addEventListener("click", () => {
      $("diff-input").value = SAMPLE_DIFF;
      updateCharCount();
      $("file-name").classList.add("hidden");
      clearError();
    });

    $("btn-clear").addEventListener("click", () => {
      $("diff-input").value = "";
      updateCharCount();
      $("file-name").classList.add("hidden");
      $("results").classList.add("hidden");
      clearError();
    });

    $("file-input").addEventListener("change", (event) => {
      readFile(event.target.files && event.target.files[0]);
      event.target.value = "";
    });

    const dropzone = $("dropzone");
    ["dragenter", "dragover"].forEach((name) => {
      dropzone.addEventListener(name, (event) => {
        event.preventDefault();
        dropzone.classList.add("dragover");
      });
    });
    ["dragleave", "drop"].forEach((name) => {
      dropzone.addEventListener(name, (event) => {
        event.preventDefault();
        dropzone.classList.remove("dragover");
      });
    });
    dropzone.addEventListener("drop", (event) => {
      const file = event.dataTransfer && event.dataTransfer.files && event.dataTransfer.files[0];
      readFile(file);
    });

    document.addEventListener("keydown", (event) => {
      if ((event.ctrlKey || event.metaKey) && event.key === "Enter") {
        event.preventDefault();
        analyze();
      }
    });

    $("history-list").addEventListener("click", (event) => {
      const loadBtn = event.target.closest("[data-load]");
      if (loadBtn) { loadRecord(loadBtn.dataset.load); return; }
      const deleteBtn = event.target.closest("[data-delete]");
      if (deleteBtn) { deleteRecord(deleteBtn.dataset.delete); }
    });
  }

  document.addEventListener("DOMContentLoaded", () => {
    bind();
    updateCharCount();
    loadConfig();
    loadHistory();
  });
})();
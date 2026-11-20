"use strict";

const $ = (s) => document.querySelector(s);
const esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const STATUS_TEXT = { met: "met", partial: "partial", not_met: "not met", no_evidence: "no evidence" };

const state = { criteria: [], results: [], runId: null, tier: "ALL", open: new Set() };

async function api(path, body) {
  const res = await fetch(path, body === undefined ? {} : {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.detail || res.statusText);
  return data;
}

function seeds() {
  return [0, 1, 2].map((i) => $("#seed" + i).value.trim()).filter(Boolean);
}

async function init() {
  const cfg = await api("/api/config");
  $("#mode").textContent = cfg.llm ? `LLM: ${cfg.scorer.slice(4)}` : "Scorer: rule-based (no LLM key set)";
  $("#mode").classList.add(cfg.llm ? "llm" : "rules");
  $("#offer").textContent = cfg.seller.offer;
  $("#customers").innerHTML = cfg.existing_customers.map((c) => `<option value="${esc(c.domain)}">${esc(c.name)}</option>`).join("");
  cfg.default_best_customers.forEach((d, i) => { $("#seed" + i).value = d; });
}

// ---------- step 1: ICP
async function deriveIcp() {
  try {
    const data = await api("/api/icp", { best_customers: seeds() });
    state.criteria = data.criteria;
    renderCriteria();
    $("#icp-wrap").hidden = false;
    $("#btn-cand").disabled = false;
    $("#btn-run").disabled = false;
  } catch (e) { alert(e.message); }
}

function renderCriteria() {
  $("#criteria-body").innerHTML = state.criteria.map((c, i) => {
    const terms = c.kind === "size"
      ? `<input type="number" data-i="${i}" data-f="min" value="${c.min}" class="num-in"> to
         <input type="number" data-i="${i}" data-f="max" value="${c.max}" class="num-in"> employees`
      : `<input data-i="${i}" data-f="keywords" value="${esc(c.keywords.join(", "))}" class="terms-in">`;
    const weights = [1, 2, 3].map((w) => `<option ${w === c.weight ? "selected" : ""}>${w}</option>`).join("");
    return `<tr class="${c.enabled ? "" : "off"}">
      <td><input type="checkbox" data-i="${i}" data-f="enabled" ${c.enabled ? "checked" : ""} aria-label="enable"></td>
      <td><strong>${esc(c.label)}</strong><div class="rationale">${esc(c.rationale)}</div></td>
      <td>${terms}</td>
      <td><select data-i="${i}" data-f="weight">${weights}</select></td>
      <td><input type="checkbox" data-i="${i}" data-f="must_have" ${c.must_have ? "checked" : ""} aria-label="must-have"></td>
    </tr>`;
  }).join("");
}

$("#criteria-body").addEventListener("change", (ev) => {
  const el = ev.target, i = +el.dataset.i, f = el.dataset.f;
  if (Number.isNaN(i) || !f) return;
  const c = state.criteria[i];
  if (f === "enabled" || f === "must_have") c[f] = el.checked;
  else if (f === "weight" || f === "min" || f === "max") c[f] = parseInt(el.value, 10) || 0;
  else if (f === "keywords") c.keywords = el.value.split(",").map((s) => s.trim()).filter(Boolean);
  if (c.kind === "size") c.label = `Size: ${c.min}-${c.max} employees`;
  renderCriteria();
});

// ---------- step 2: candidates
async function fetchCandidates() {
  try {
    const data = await api("/api/candidates", { best_customers: seeds() });
    const removed = data.removed_existing_customers.map((c) => esc(c.name)).join(", ") || "none";
    $("#cand-meta").innerHTML = `<strong>${data.candidates.length}</strong> candidates from ${esc(data.source)}.
      Existing customers removed: <strong>${data.removed_existing_customers.length}</strong> (${removed}).`;
    $("#cand-list").innerHTML = data.candidates.map((c) =>
      `<span class="chip">${esc(c.name)} <small>${esc(c.domain)} · ${(c.similarity * 100).toFixed(0)}%</small></span>`).join("");
    $("#cand-wrap").hidden = false;
  } catch (e) { alert(e.message); }
}

// ---------- step 3: qualify
async function run() {
  $("#btn-run").disabled = true;
  $("#run-status").textContent = "Agent is reading websites and checking every criterion...";
  try {
    if (!state.criteria.length) await deriveIcp();
    const data = await api("/api/qualify", { best_customers: seeds(), criteria: state.criteria });
    state.results = data.results;
    state.runId = data.run_id;
    state.open.clear();
    const counts = ["A", "B", "C"].map((t) => `${t}: ${data.tier_counts[t] || 0}`).join(" · ");
    $("#run-status").textContent = `${data.results.length} companies qualified (${counts}). Scorer: ${data.scorer}. Existing customers excluded: ${data.removed_existing_customers.length}.`;
    $("#results-wrap").hidden = false;
    renderResults();
  } catch (e) {
    $("#run-status").textContent = "Error: " + e.message;
  } finally {
    $("#btn-run").disabled = false;
  }
}

function sorted(list) {
  const by = $("#sort").value;
  const arr = [...list];
  const cmp = {
    tier: (a, b) => a.tier.localeCompare(b.tier) || b.score - a.score || a.name.localeCompare(b.name),
    score: (a, b) => b.score - a.score || a.name.localeCompare(b.name),
    name: (a, b) => a.name.localeCompare(b.name),
    similarity: (a, b) => (b.similarity || 0) - (a.similarity || 0),
  }[by];
  return arr.sort(cmp);
}

function renderResults() {
  const q = $("#search").value.trim().toLowerCase();
  const rows = sorted(state.results).filter((r) =>
    (state.tier === "ALL" || r.tier === state.tier) &&
    (!q || r.name.toLowerCase().includes(q) || r.domain.includes(q)));
  $("#results-body").innerHTML = rows.map((r) => {
    const open = state.open.has(r.id);
    const main = `<tr class="row ${open ? "open" : ""}" data-id="${esc(r.id)}">
      <td><span class="tier tier-${r.tier}">${r.tier}</span></td>
      <td><strong>${esc(r.name)}</strong><div class="domain">${esc(r.domain)}</div></td>
      <td><div class="bar"><span style="width:${Math.round(r.score * 100)}%"></span></div>${Math.round(r.score * 100)}</td>
      <td>${esc(r.summary)}</td>
      <td class="chev">${open ? "&#9650;" : "&#9660;"}</td></tr>`;
    if (!open) return main;
    const findings = r.findings.map((f) => `<tr>
        <td><span class="status st-${f.status}">${STATUS_TEXT[f.status]}</span></td>
        <td><strong>${esc(f.label)}</strong><div class="reason">${esc(f.reason)}</div></td>
        <td>${f.quote
          ? `<blockquote>"${esc(f.quote)}"</blockquote>
             <a href="${esc(f.source_link)}" target="_blank" rel="noopener">View source</a>
             <span class="url" title="Public URL with text fragment (fictional domain)">${esc(f.url)}</span>`
          : `<span class="noev">No quote found - no verdict.</span>`}</td></tr>`).join("");
    const hook = r.draft_hook ? `<div class="hook"><strong>Draft opener (not sent):</strong> ${esc(r.draft_hook)}</div>` : "";
    return main + `<tr class="detail"><td colspan="5">${hook}<table class="findings">${findings}</table></td></tr>`;
  }).join("") || `<tr><td colspan="5" class="hint">No companies match.</td></tr>`;
  const tiers = state.tier === "ALL" ? "A,B,C" : state.tier;
  $("#export").href = `/api/export/${state.runId}.csv?tiers=${tiers}`;
}

$("#results-body").addEventListener("click", (ev) => {
  if (ev.target.closest("a")) return;
  const row = ev.target.closest("tr.row");
  if (!row) return;
  const id = row.dataset.id;
  state.open.has(id) ? state.open.delete(id) : state.open.add(id);
  renderResults();
});
document.querySelectorAll(".filter").forEach((b) => b.addEventListener("click", () => {
  document.querySelectorAll(".filter").forEach((x) => x.classList.toggle("active", x === b));
  state.tier = b.dataset.tier;
  renderResults();
}));
$("#search").addEventListener("input", renderResults);
$("#sort").addEventListener("change", renderResults);
$("#btn-icp").addEventListener("click", deriveIcp);
$("#btn-cand").addEventListener("click", fetchCandidates);
$("#btn-run").addEventListener("click", run);

init().then(deriveIcp).catch((e) => { $("#mode").textContent = "Error: " + e.message; });

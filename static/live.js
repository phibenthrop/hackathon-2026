// Live agent page: runs research -> score -> plan -> write as four visible steps.
const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
let SOURCES = [];
const STEPS = ["research", "score", "plan", "write"];

function srcUrl(n) { const s = SOURCES.find((x) => x.n === n); return s ? s.url : null; }
// Escape text, then turn [n] into links to source n; unknown numbers are flagged.
function linkRefs(text) {
  return esc(text).replace(/\[(\d{1,2})\]/g, (m, n) => {
    const u = srcUrl(+n);
    return u ? `<a class="ref" href="${esc(u)}" target="_blank" rel="noopener" title="${esc(u)}">[${n}]</a>`
             : `<span class="flag" title="no such source">[${n}]?</span>`;
  });
}
function state(step, cls, text) { const el = $("st-" + step); el.className = "state " + cls; el.textContent = text; }
function money(x) { return x == null ? "n/a" : "$" + Number(x).toFixed(5); }
function meta(m) {
  if (m.step === "research") return `<b>Tavily</b> · ${m.calls} calls · ${m.tavily_credits} credits · ${m.seconds}s`;
  return `<b>${esc(m.model)}</b> · ${m.calls} call${m.calls > 1 ? "s" : ""} · ${m.seconds}s · ` +
    `${m.prompt_tokens} in / ${m.completion_tokens} out tokens · ${money(m.cost_usd)}`;
}

function renderResearch(r) {
  SOURCES = r.sources;
  const calls = r.calls.map((c) => `<li><code>${esc(c.call)}</code> ${esc(c.query)}${c.error ? ` <span class="flag">${esc(c.error)}</span>` : ` - ${c.results} results`}</li>`).join("");
  return `<p>Target: <strong>${esc(r.target.name)}</strong>${r.target.domain ? ` (${esc(r.target.domain)})` : ""}</p>
    <details><summary>${r.calls.length} Tavily calls</summary><ul>${calls}</ul></details>
    <ol class="sources">${r.sources.map((s) => `<li value="${s.n}"><a href="${esc(s.url)}" target="_blank" rel="noopener">${esc(s.title)}</a>
      <span class="badge">${esc(s.kind)}${s.published ? " · " + esc(s.published.slice(0, 16)) : ""}</span>
      <div class="ex">${esc(s.excerpt)}</div><span class="url">${esc(s.url)}</span></li>`).join("")}</ol>`;
}

function renderScore(r) {
  const rows = r.findings.map((f) => {
    let ev = `<span class="noev">no evidence</span>`;
    if (f.quote) ev = `<blockquote>${esc(f.quote)}</blockquote><a class="url" href="${esc(f.url)}" target="_blank" rel="noopener">[${f.source_n}] ${esc(f.url)}</a>`;
    if (f.discarded_quote) ev = `<span class="flag">quote discarded: not found verbatim in the fetched text</span><div class="discarded">${esc(f.discarded_quote)}</div>`;
    return `<tr><td>${esc(f.label)}</td><td><span class="status st-${f.status}">${f.status.replace("_", " ")}</span><div class="reason">${esc(f.reason)}</div></td><td>${ev}</td></tr>`;
  }).join("");
  return `<p><span class="tier tier-${r.tier}">${r.tier}</span> score ${r.score} - ${esc(r.summary)}
    <br><span class="hint">Tier computed by the app from the verified findings, not by the model. Quotes proposed: ${r.proposed}, discarded by the quote gate: ${r.discarded}.</span></p>
    <table class="findings"><thead><tr><th>Criterion</th><th>Verdict</th><th>Evidence</th></tr></thead><tbody>${rows}</tbody></table>`;
}

function checks(c) {
  const out = [];
  if (c.invalid_refs.length) out.push(`<span class="flag">references to missing sources: ${c.invalid_refs.join(", ")}</span>`);
  c.unsourced_numbers.forEach((s) => out.push(`<span class="flag">no source: ${esc(s)}</span>`));
  return out.length ? `<p>${out.join(" ")}</p>` : `<p class="hint">Every numbered reference points to a fetched source.</p>`;
}

function renderPlan(r) {
  const p = r.plan, list = (a) => (Array.isArray(a) ? a : [a]).filter(Boolean);
  const who = list(p.who).map((w) => typeof w === "object" ? `<li><strong>${esc(w.role)}</strong> - ${linkRefs(w.why)}</li>` : `<li>${linkRefs(w)}</li>`).join("");
  return `<dl class="plan">
    <dt>Fit</dt><dd>${linkRefs(p.fit_summary)}</dd>
    <dt>Whom to address (roles, no names)</dt><dd><ul>${who}</ul></dd>
    <dt>Hook</dt><dd>${linkRefs(p.hook)}</dd>
    <dt>Channel</dt><dd>${linkRefs(p.channel)}</dd>
    <dt>Do not say</dt><dd><ul>${list(p.avoid).map((a) => `<li>${linkRefs(typeof a === "object" ? JSON.stringify(a) : a)}</li>`).join("")}</ul></dd>
    <dt>Discovery question</dt><dd>${list(p.questions).map(linkRefs).join("<br>")}</dd>
    <dt>Email language</dt><dd>${esc(r.language)}</dd></dl>${checks(r.checks)}`;
}

function renderWrite(r) {
  const d = r.draft;
  return `<p><strong>Subject:</strong> ${linkRefs(d.subject)}</p>
    <textarea class="draftbox" id="draft">${esc(d.body)}</textarea>
    <p class="draftonly">Draft only — a person decides what goes out.</p>
    <button id="copy">Copy</button> <button id="discard">Discard</button>
    <p><strong>Sources in this draft:</strong> ${linkRefs(d.body).match(/<a class="ref"[^>]*>\[\d+\]<\/a>/g)?.filter((v, i, a) => a.indexOf(v) === i).join(" ") || "none"}</p>
    ${checks(r.checks)}<p class="hint">Language: ${esc(r.language)}. The app sends nothing.</p>`;
}

const RENDER = { research: renderResearch, score: renderScore, plan: renderPlan, write: renderWrite };

function show(m) {
  $("m-" + m.step).innerHTML = meta(m);
  $("o-" + m.step).innerHTML = RENDER[m.step](m.result);
  state(m.step, "ok", "done");
  if (m.step === "write") wireDraft();
}
function wireDraft() {
  $("copy").onclick = async () => { await navigator.clipboard.writeText($("draft").value); $("copy").textContent = "Copied"; };
  $("discard").onclick = () => { $("draft").value = ""; $("o-write").insertAdjacentHTML("beforeend", "<p class='hint'>Draft discarded.</p>"); };
}
function totals(t, recorded) {
  if (!t) return;
  $("totals").textContent = `${recorded ? "Recorded run: " : "This run: "}${t.seconds}s, ${t.tokens} tokens, model cost $${t.model_cost_usd.toFixed(4)} + ${t.tavily_credits} Tavily credits (${t.model_calls} model calls, ${t.tavily_calls} Tavily calls).`;
}
function reset() {
  STEPS.forEach((s) => { state(s, "", "waiting"); $("m-" + s).innerHTML = ""; $("o-" + s).innerHTML = ""; });
  $("totals").textContent = ""; document.querySelectorAll(".recorded").forEach((e) => e.remove());
}

async function post(url, body) {
  const r = await fetch(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: body ? JSON.stringify(body) : undefined });
  let data = null; try { data = await r.json(); } catch { /* not json */ }
  if (!r.ok) throw new Error((data && data.detail) || `HTTP ${r.status}`);
  return data;
}

async function run() {
  reset();
  $("go").disabled = true;
  try {
    state("research", "run", "running...");
    const icp = $("icp").value.split("\n").map((s) => s.trim()).filter(Boolean);
    const first = await post("/api/live/start", { company: $("company").value, product: $("product").value, icp });
    show(first);
    for (const step of ["score", "plan", "write"]) {
      state(step, "run", "running...");
      const m = await post(`/api/live/${first.run_id}/${step}`);
      show(m); totals(m.totals);
    }
  } catch (e) {
    const cur = STEPS.find((s) => $("st-" + s).classList.contains("run")) || "research";
    state(cur, "err", "failed");
    $("o-" + cur).innerHTML = `<p class="flag">${esc(e.message)}</p><p class="hint">Use "Show recorded example" to see a complete run.</p>`;
  } finally { $("go").disabled = false; }
}

async function example() {
  reset();
  const r = await fetch("/api/live/example");
  if (!r.ok) { $("o-research").innerHTML = `<p class="flag">No recorded example available.</p>`; return; }
  const ex = await r.json();
  document.querySelector("main").insertAdjacentHTML("afterbegin",
    `<p class="recorded"><strong>Recorded example</strong> - a real run saved on ${esc(ex.recorded_at || "2026-11-20")} for ${esc(ex.input.company)}. Replayed, no live calls.</p>`);
  ex.steps.forEach(show); totals(ex.totals, true);
}

function renderCmp(c) {
  if (!c || !c.models) return;
  const cols = [["model", "Model"], ["companies", "Firms"], ["valid_json_rate", "Valid JSON"], ["discarded_quote_rate", "Quotes discarded"],
    ["avg_seconds", "Avg s / firm"], ["avg_tokens", "Avg tokens / firm"], ["cost_per_company_usd", "USD / firm"], ["tier_agreement", "Tier agreement"]];
  const fmt = (k, v) => v == null ? "-" : (k.endsWith("rate") || k === "tier_agreement") ? Math.round(v * 100) + "%" : k.startsWith("cost") ? "$" + v.toFixed(5) : v;
  $("cmp").innerHTML = `<table class="cmp"><thead><tr>${cols.map((c) => `<th>${c[1]}</th>`).join("")}</tr></thead><tbody>${
    c.models.map((m) => `<tr>${cols.map(([k]) => `<td>${k === "model" ? esc(m[k]) : fmt(k, m[k])}</td>`).join("")}</tr>`).join("")}</tbody></table>
    <p class="hint">${esc(c.note || "")}</p>`;
}

async function init() {
  const cfg = await (await fetch("/api/live/config")).json();
  $("product").value = cfg.default_product;
  $("icp").value = cfg.default_icp.join("\n");
  $("mode").textContent = cfg.live ? `live · ${cfg.runs_left} runs left today` : "recorded only";
  $("mode").className = "badge " + (cfg.live ? "llm" : "rules");
  $("reg").innerHTML = cfg.registry.map((r) => `<tr><td>${esc(r.title)}</td><td><code>${esc(r.model)}</code></td><td>${esc(r.why)}</td><td class="hint">${esc(r.price)}</td></tr>`).join("");
  renderCmp(cfg.comparison);
  $("go").onclick = run;
  $("example").onclick = example;
  $("company").addEventListener("keydown", (e) => { if (e.key === "Enter") run(); });
  if (!cfg.live) {
    $("notice").textContent = cfg.message + ". Showing the recorded example.";
    $("go").disabled = true;
    if (cfg.example) example();
  }
}
init();

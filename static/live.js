// Live agent page: runs research -> score -> plan -> write as four visible steps.
const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
let SOURCES = [];
let CFG = null;
let RUN_ID = null;           // id of the current live run (kept for "Retry this step")
let RECORDED = false;        // true while a recorded run is shown
let TIMER = null;            // seconds counter of the running step
const STEPS = ["research", "score", "plan", "write"];
const STEP_TIMEOUT_MS = 75000;
const WHY = {};              // step -> short reason from the registry
let CREDIT_USD = 0.008;      // Tavily price per credit, read from the registry when possible
const RESULTS = {};          // step -> result of the shown run

function srcUrl(n) { const s = SOURCES.find((x) => x.n === n); return s ? s.url : null; }
// Escape text, then turn [n] into links to source n; unknown numbers are flagged.
function linkRefs(text) {
  return esc(text).replace(/\[(\d{1,2})\]/g, (m, n) => {
    const u = srcUrl(+n);
    return u ? `<a class="ref" href="${esc(u)}" target="_blank" rel="noopener" title="${esc(u)}">[${n}]</a>`
             : `<span class="flag" title="no such source">[${n}]?</span>`;
  });
}

function state(step, cls, text) {
  const el = $("st-" + step); el.className = "state " + cls; el.textContent = text;
  const sec = $("s-" + step);
  sec.classList.remove("is-wait", "is-run", "is-ok", "is-err");
  sec.classList.add({ run: "is-run", ok: "is-ok", err: "is-err" }[cls] || "is-wait");
  if (cls === "run") sec.scrollIntoView({ block: "nearest", behavior: "smooth" });
}
function startTimer(step) {
  stopTimer();
  const t0 = Date.now();
  state(step, "run", "running 0 s");
  TIMER = setInterval(() => { $("st-" + step).textContent = `running ${Math.round((Date.now() - t0) / 1000)} s`; }, 1000);
}
function stopTimer() { if (TIMER) clearInterval(TIMER); TIMER = null; }

function money(x) { return x == null ? "n/a" : "$" + Number(x).toFixed(4); }
const num = (x) => Number(x || 0).toLocaleString("en-US");
const mark = (html, cls = "") => `<span class="live-mark ${cls}">${html}</span>`;
function meta(m) {
  if (m.step === "research") {
    return mark(`<b>Tavily</b>`) + (WHY.research ? `<span class="live-why">${esc(WHY.research)}</span>` : "") +
      mark(`${m.calls} calls`) + mark(`${m.tavily_credits} credits`) + mark(`${m.seconds} s`);
  }
  return mark(`<b>${esc(m.model)}</b>`) + (WHY[m.step] ? `<span class="live-why">${esc(WHY[m.step])}</span>` : "") +
    mark(`${m.seconds} s`) + mark(`${num((m.prompt_tokens || 0) + (m.completion_tokens || 0))} tokens`) + mark(money(m.cost_usd));
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

// Check line over the draft, computed in the browser: words, questions, sourced facts.
function draftCheck(body, cited) {
  const plain = body.replace(/\[\d{1,2}\]/g, " ");
  const words = (plain.match(/[\p{L}\p{N}][\p{L}\p{N}'’.-]*/gu) || []).length;
  const q = (plain.match(/\?/g) || []).length;
  const facts = cited.length;
  const issues = [];
  if (words > 100) issues.push(`${words} words - cut to 100`);
  if (words < 50) issues.push(`${words} words - too short`);
  if (q === 0) issues.push("no question - add one");
  if (q > 1) issues.push(`${q} questions - cut ${q === 2 ? "one" : q - 1}`);
  if (facts < 1) issues.push("no sourced fact - add one with [n]");
  const line = `${words} words · ${q} question${q === 1 ? "" : "s"} · ${facts} sourced fact${facts === 1 ? "" : "s"}`;
  return `<p class="live-check ${issues.length ? "warn" : "good"}">${line}${issues.length ? " - " + esc(issues.join(", ")) : ""}</p>`;
}

function renderWrite(r) {
  const d = r.draft;
  if (!String(d.body || "").trim()) {
    return `<p class="live-empty">No draft: the agent found no sourced fact to open with. That is the intended behaviour.</p>`;
  }
  const cited = (r.checks && r.checks.cited) || [];
  const srcList = cited.map((n) => SOURCES.find((s) => s.n === n)).filter(Boolean)
    .map((s) => `<li value="${s.n}"><a href="${esc(s.url)}" target="_blank" rel="noopener">${esc(s.title)}</a> <span class="url">${esc(s.url)}</span></li>`).join("");
  return `<p><strong>Subject:</strong> ${linkRefs(d.subject)}</p>
    <div id="draftcheck">${draftCheck(d.body, cited)}</div>
    <textarea class="draftbox" id="draft">${esc(d.body)}</textarea>
    <p class="draftonly">Draft only — a person decides what goes out.</p>
    <p class="live-legal">Germany/EU: cold email needs prior consent, also in B2B. Check before you send. Not legal advice.</p>
    <button id="copy">Copy without source marks</button> <button id="discard">Discard</button>
    <p><strong>Sources in this draft:</strong></p>${srcList ? `<ol class="sources">${srcList}</ol>` : "<p class='hint'>none</p>"}
    ${checks(r.checks)}<p class="hint">Language: ${esc(r.language)}. The app sends nothing.</p>`;
}

const RENDER = { research: renderResearch, score: renderScore, plan: renderPlan, write: renderWrite };

function tierBanner() {
  const c = RESULTS.score && RESULTS.score.tier === "C";
  ["ban-plan", "ban-write"].forEach((id) => {
    $(id).hidden = !c;
    $(id).textContent = c ? "Tier C - the agent recommends not to contact this company. Plan and draft are shown so you can see why." : "";
  });
}

function show(m) {
  RESULTS[m.step] = m.result;
  $("m-" + m.step).innerHTML = meta(m);
  $("o-" + m.step).innerHTML = RENDER[m.step](m.result);
  state(m.step, "ok", "done");
  tierBanner();
  if (m.step === "write") wireDraft();
}
function wireDraft() {
  if (!$("copy")) return;
  const cited = (RESULTS.write.checks && RESULTS.write.checks.cited) || [];
  $("draft").addEventListener("input", () => { $("draftcheck").innerHTML = draftCheck($("draft").value, cited); });
  $("copy").onclick = async () => {
    const text = $("draft").value.replace(/[ \t]*\[\d{1,2}\]/g, "");
    await navigator.clipboard.writeText(text); $("copy").textContent = "Copied";
  };
  $("discard").onclick = () => { $("draft").value = ""; $("o-write").insertAdjacentHTML("beforeend", "<p class='hint'>Draft discarded.</p>"); };
}

// Totals box: seconds, model cost plus web research in dollars, sources, rejected quotes.
function totals(t, researchMeta) {
  let seconds, usd, credits;
  if (t) { seconds = t.seconds; credits = t.tavily_credits; usd = t.model_cost_usd + credits * CREDIT_USD; }
  else if (researchMeta) { seconds = researchMeta.seconds; credits = researchMeta.tavily_credits; usd = credits * CREDIT_USD; }
  else return;
  const parts = [`${Math.round(seconds)} s`, "$" + usd.toFixed(2), `${SOURCES.length} sources`];
  if (RESULTS.score) parts.push(`${RESULTS.score.discarded} quote${RESULTS.score.discarded === 1 ? "" : "s"} rejected`);
  $("totals").hidden = false;
  $("totals").innerHTML = `<div class="live-big">${esc(parts.join(" · "))}</div>
    <div class="hint">model cost + web research, ${RECORDED ? "recorded run" : "this run"}</div>`;
}
function reset() {
  stopTimer();
  STEPS.forEach((s) => { state(s, "", "waiting"); $("m-" + s).innerHTML = ""; $("o-" + s).innerHTML = ""; delete RESULTS[s]; });
  SOURCES = []; tierBanner();
  $("totals").hidden = true; $("totals").innerHTML = "";
  document.querySelectorAll(".recorded").forEach((e) => e.remove());
}

class HttpError extends Error { constructor(status, msg) { super(msg); this.status = status; } }

async function post(url, body) {
  const ac = new AbortController();
  const t = setTimeout(() => ac.abort(), STEP_TIMEOUT_MS);
  try {
    const r = await fetch(url, { method: "POST", headers: { "Content-Type": "application/json" },
      body: body ? JSON.stringify(body) : undefined, signal: ac.signal });
    let data = null; try { data = await r.json(); } catch { /* not json */ }
    if (!r.ok) throw new HttpError(r.status, (data && data.detail) || `HTTP ${r.status}`);
    return data;
  } catch (e) {
    if (e.name === "AbortError") throw new HttpError("timeout", "timeout");
    throw e;
  } finally { clearTimeout(t); }
}

// Plain message per failure case instead of the raw error text.
function failText(step, e) {
  const msg = String(e.message || "");
  if (msg.includes("402")) return "The model provider refused the call: account out of credit (HTTP 402). Your input is fine. Research above is real; replay a recorded run for the remaining steps.";
  if (e.status === "timeout") return "This step took longer than 75 seconds and was stopped. The research is kept. Retry the step.";
  if (e.status === 404 && step === "research") return "No usable sources found under this name. Try the company's domain, for example stef.com.";
  if (e.status === 429) return "Today's live-run limit is used up. The recorded run still works.";
  if (e.status === 400 || e.status === 503 || (e.status === 404 && step !== "research")) return msg;
  if (step === "research") return "The web research call failed. Retry, or replay a recorded run.";
  return "The model call failed. Retry, or replay a recorded run.";
}
function fail(step, e) {
  stopTimer();
  state(step, "err", "failed");
  $("o-" + step).innerHTML = `<p class="live-error" title="${esc(e.message)}">${esc(failText(step, e))}</p>
    <button class="retry">Retry this step</button> <button class="replay">Replay a recorded run</button>`;
  $("o-" + step).querySelector(".retry").onclick = () => (step === "research" || !RUN_ID ? run() : runFrom(step));
  $("o-" + step).querySelector(".replay").onclick = example;
}

async function stepCall(step) {
  try { return await post(`/api/live/${RUN_ID}/${step}`); }
  catch (e) {
    // A step stopped by the browser timeout may still have finished on the server: fetch its result.
    if (e.status === 409 && /already done/i.test(e.message)) {
      const rec = await (await fetch(`/api/live/${RUN_ID}/record`)).json();
      const m = rec.steps.find((s) => s.step === step);
      if (m) return { ...m, totals: rec.totals };
    }
    throw e;
  }
}

async function runFrom(start) {
  $("go").disabled = true;
  let cur = start;
  try {
    for (const step of ["score", "plan", "write"].slice(["score", "plan", "write"].indexOf(start))) {
      cur = step;
      $("o-" + step).innerHTML = "";
      startTimer(step);
      const m = await stepCall(step);
      stopTimer(); show(m); totals(m.totals);
    }
  } catch (e) { fail(cur, e); }
  finally { $("go").disabled = false; }
}

async function run() {
  reset();
  RECORDED = false; RUN_ID = null;
  $("go").disabled = true;
  try {
    startTimer("research");
    const icp = $("icp").value.split("\n").map((s) => s.trim()).filter(Boolean);
    const first = await post("/api/live/start", { company: $("company").value, product: $("product").value, icp });
    RUN_ID = first.run_id;
    stopTimer(); show(first); totals(null, first);
  } catch (e) { fail("research", e); $("go").disabled = false; return; }
  await runFrom("score");
}

async function example() {
  reset();
  RUN_ID = null;
  const r = await fetch("/api/live/example");
  if (!r.ok) { $("o-research").innerHTML = `<p class="live-error">No recorded run stored yet.</p>`; return; }
  const ex = await r.json();
  RECORDED = true;
  document.querySelector("main").insertAdjacentHTML("afterbegin",
    `<p class="recorded"><strong>Recorded run</strong> - a real run saved on ${esc(ex.recorded_at || "2026-11-20")} for ${esc(ex.input.company)}. Replayed, no live calls.</p>`);
  ex.steps.forEach(show);
  const rm = ex.steps.find((s) => s.step === "research");
  totals(ex.totals, rm);
}

function renderCmp(c) {
  if (!c || !c.models) { $("cmp-wrap").hidden = true; return; }
  $("cmp-wrap").hidden = false;
  const cols = [["model", "Model"], ["companies", "Firms"], ["valid_json_rate", "Valid JSON"], ["discarded_quote_rate", "Quotes discarded"],
    ["avg_seconds", "Avg s / firm"], ["avg_tokens", "Avg tokens / firm"], ["cost_per_company_usd", "USD / firm"], ["tier_agreement", "Tier agreement"]];
  const fmt = (k, v) => v == null ? "-" : (k.endsWith("rate") || k === "tier_agreement") ? Math.round(v * 100) + "%" : k.startsWith("cost") ? "$" + v.toFixed(4) : v;
  $("cmp").innerHTML = `<table class="cmp"><thead><tr>${cols.map((c) => `<th>${c[1]}</th>`).join("")}</tr></thead><tbody>${
    c.models.map((m) => `<tr>${cols.map(([k]) => `<td>${k === "model" ? esc(m[k]) : fmt(k, m[k])}</td>`).join("")}</tr>`).join("")}</tbody></table>
    <p class="hint">${esc(c.note || "")}</p>`;
}

async function init() {
  reset();
  const cfg = CFG = await (await fetch("/api/live/config")).json();
  $("product").value = cfg.default_product;
  $("icp").value = cfg.default_icp.join("\n");
  $("mode").textContent = cfg.live ? `live · ${cfg.runs_left} runs left today` : "recorded only";
  $("mode").className = "badge " + (cfg.live ? "llm" : "rules");
  cfg.registry.forEach((r) => {
    WHY[r.step] = String(r.why || "").split(/[:,]/)[0].trim();
    if (r.step === "research") { const p = /\$([\d.]+)/.exec(r.price || ""); if (p) CREDIT_USD = +p[1]; }
  });
  $("reg").innerHTML = cfg.registry.map((r) => `<tr><td>${esc(r.title)}</td><td><code>${esc(r.model)}</code></td><td>${esc(r.why)}</td><td class="hint">${esc(r.price)}</td></tr>`).join("");
  renderCmp(cfg.comparison);
  $("example").hidden = !cfg.example;
  $("go").onclick = run;
  $("example").onclick = example;
  document.querySelectorAll(".live-chip").forEach((b) => { b.onclick = () => { $("company").value = b.textContent; $("company").focus(); }; });
  $("company").addEventListener("keydown", (e) => { if (e.key === "Enter") run(); });
  if (!cfg.live) {
    $("notice").textContent = cfg.message + ". Showing the recorded example.";
    $("go").disabled = true;
    if (cfg.example) example();
  }
}
init();

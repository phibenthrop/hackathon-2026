// Branch otari: the Otari panel. Shown only when the server runs with LLM_GATEWAY=otari.
(function () {
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s == null ? "" : s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const usd = (x) => (x == null ? "n/a" : "$" + Number(x).toFixed(4));
  const short = (m) => String(m || "").replace(/^nebius:/, "");
  let ON = false;

  window.otariOpts = () => (ON ? { user: $("ot-user").value, outage: $("ot-outage").checked } : {});

  window.otariMeta = (m) => {
    const o = m.otari;
    if (!o) return "";
    const mk = (h, c = "live-ot") => `<span class="live-mark ${c}">${h}</span>`;
    if (m.step === "research") return mk(`via Otari: search + fetch as <b>${esc(o.user)}</b>`);
    let h = mk(`Otari route <code>${esc(o.policy)}</code> → ${esc(short(o.model))} · ${o.attempts} attempt${o.attempts === 1 ? "" : "s"} · user ${esc(o.user)}`);
    if (o.fallback) h += mk(`fallback via Otari: ${esc(short(m.fallback_from || "primary"))} → ${esc(short(o.model))}`, "live-ot-fb");
    (Array.isArray(o.guardrails) ? o.guardrails : []).forEach((g) => {
      h += mk(`Otari guardrail <code>${esc(g.profile)}</code> (${esc(g.mode)}): ${g.valid ? "passed" : "flagged"}, ${esc(g.status)}`);
    });
    return h;
  };

  function row(feature, value) { return `<tr><td>${esc(feature)}</td><td>${value}</td></tr>`; }

  async function refresh() {
    if (!ON) return;
    let s;
    try { s = await (await fetch("/api/otari/status")).json(); } catch (e) { return; }
    const users = (s.users || []).map((u) => `<b>${esc(u.user)}</b> on <code>${esc(u.budget)}</code>: spent ${usd(u.spent_usd)} of ${usd(u.limit_usd)}, left ${usd(u.left_usd)}${u.left_usd != null && u.left_usd <= 0.008 ? ' <span class="no">(blocked for paid calls)</span>' : ""}`).join("<br>");
    const k = s.key_ceiling;
    const pol = s.policies || {};
    $("ot-status").innerHTML =
      row("Routing + fallback", `policies ${Object.values(pol).map((p) => `<code>${esc(p)}</code>`).join(", ")}; outage policy <code>${esc(s.outage_policy)}</code> (primary <code>${esc(s.outage_model)}</code> is not served)`) +
      row("Per-user budgets", users || "n/a") +
      row("Spending limit (key)", k ? `${usd(k.used_usd)} used of ${usd(k.limit_usd)} per day, ${usd(k.remaining_usd)} left` : "n/a") +
      row("Web search + fetch", "research step: <code>POST /api/v1/search/tavily</code> and <code>POST /api/v1/fetch/builtin_fetch</code>, billed to the end user") +
      row("Guardrail", `profile <code>${esc(s.guard_profile)}</code>, block mode on input and output of the write step`) +
      row("Demo buttons left", esc(s.probes_left));
  }
  window.otariRefresh = refresh;

  async function call(url, label) {
    const out = $("ot-result");
    out.innerHTML = `${esc(label)}: running...`;
    document.querySelectorAll(".otari-buttons button").forEach((b) => { b.disabled = true; });
    try {
      const r = await fetch(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ user: $("ot-user").value }) });
      const d = await r.json();
      if (!r.ok) { out.innerHTML = `<span class="no">${esc(label)}: HTTP ${r.status}</span> ${esc(d.detail || "")}`; return; }
      return d;
    } catch (e) { out.innerHTML = `<span class="no">${esc(label)} failed</span>`; }
    finally { document.querySelectorAll(".otari-buttons button").forEach((b) => { b.disabled = false; }); refresh(); }
  }

  async function budget() {
    const d = await call("/api/otari/probe", "Budget check");
    if (!d) return;
    const a = d.status_after || {};
    $("ot-result").innerHTML = d.ok
      ? `<span class="ok">200 allowed</span> for <b>${esc(d.user)}</b>: one web search through Otari (${usd(d.cost_usd)}) billed to its own budget, left ${usd(a.left_usd)} of ${usd(a.limit_usd)} (${d.seconds} s)`
      : `<span class="no">${d.status} ${esc(d.code || "")}: blocked by Otari</span> for <b>${esc(d.user)}</b>, budget ${esc(a.budget)} spent ${usd(a.spent_usd)} of ${usd(a.limit_usd)}. Switch to the other user and it still works.`;
  }

  async function fallback() {
    const d = await call("/api/otari/fallback-test", "Fallback");
    if (!d) return;
    $("ot-result").innerHTML = d.ok
      ? `<span class="ok">fallback via Otari: ${esc(short(d.primary))} → ${esc(short(d.model))}</span>, ${d.attempts} attempts, Otari-Fallback: ${d.fallback}, ${usd(d.cost_usd)}, ${d.seconds} s`
      : `<span class="no">HTTP ${d.status}</span> ${esc(d.error || "")}`;
  }

  async function guard() {
    const d = await call("/api/otari/guard-test", "Guardrail");
    if (!d) return;
    $("ot-result").innerHTML = !d.ok && d.status === 403
      ? `<span class="ok">blocked by Otari guardrail (403 guardrail_violation)</span> before any model ran. Input: "${esc(d.sample)}"`
      : `<span class="no">not blocked</span> (HTTP ${d.status}) ${esc(d.error || "")}`;
  }

  async function init() {
    let cfg;
    try { cfg = await (await fetch("/api/live/config")).json(); } catch (e) { return; }
    if (!cfg.gateway || !cfg.gateway.active) return;
    ON = true;
    $("otari").hidden = false;
    $("ot-budget").onclick = budget;
    $("ot-fallback").onclick = fallback;
    $("ot-guard").onclick = guard;
    $("ot-user").onchange = () => { $("ot-result").innerHTML = ""; };
    refresh();
  }
  init();
})();

const params = new URLSearchParams(location.search);
const THUMB = params.get("mode") === "thumbnail";
const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const pct = (v) => (v === null || v === undefined ? "–" : Math.round(v * 100) + "%");
const secs = (ms) => (ms === null || ms === undefined ? "–" : (ms / 1000).toFixed(1) + " s");
const money = (v, d = 4) => (v === null || v === undefined ? "–" : "$" + Number(v).toFixed(d));

if (window.mermaid) {
  mermaid.initialize({
    startOnLoad: false, theme: "base", securityLevel: "loose",
    themeVariables: { fontFamily: "Manrope, Segoe UI, sans-serif", fontSize: "14px", lineColor: "#5B6B7B",
                      primaryColor: "#E6EEF4", primaryTextColor: "#13263A", primaryBorderColor: "#1D3D5C" },
    flowchart: { curve: "basis", htmlLabels: true },
  });
}

function bars(el, rows, opts = {}) {
  // rows: [{label, value (0..1 or count), display}]
  const max = opts.max ?? Math.max(1, ...rows.map((r) => r.value));
  el.innerHTML = rows.length ? rows.map((r) => {
    const w = Math.max(2, (r.value / max) * 100);
    const cls = opts.rate ? (r.value >= 0.999 ? "full" : r.value < 0.7 ? "low" : "") : "";
    return `<div class="bar"><span>${esc(r.label)}</span><div class="track"><div class="fill ${cls}" style="width:${w}%"></div></div><span class="val num">${esc(r.display)}</span></div>`;
  }).join("") : `<p class="muted small">No data yet.</p>`;
}

function strip(el, scenarios, grouped) {
  if (!scenarios || !scenarios.length) {
    el.innerHTML = Array.from({ length: 24 }, () => `<span class="cell"></span>`).join("");
    if (grouped) el.innerHTML = `<p class="muted small">Scenarios appear here after the evaluation runs.</p>`;
    return;
  }
  if (!grouped) {
    el.innerHTML = scenarios.map((s) => `<span class="cell ${s.passed ? "pass" : "fail"}" title="${esc(s.id)}"></span>`).join("");
    return;
  }
  const cats = {};
  scenarios.forEach((s) => (cats[s.category] ||= []).push(s));
  el.innerHTML = Object.entries(cats).map(([c, list]) => `
    <div class="strip-row"><span>${esc(c)}</span><div class="cells">
      ${list.map((s) => `<span class="cell ${s.passed ? "pass" : "fail"}" title="${esc(s.id)}: ${s.passed ? "passed" : "failed"}"></span>`).join("")}
    </div></div>`).join("");
}

function latencyChart(el, values) {
  if (!values.length) { el.innerHTML = `<p class="muted small" style="margin-top:14px">No data yet.</p>`; return; }
  const maxS = Math.max(...values) / 1000;
  const binW = maxS <= 4 ? 0.5 : maxS <= 10 ? 1 : 2;
  const nBins = Math.max(4, Math.ceil(maxS / binW));
  const counts = Array(nBins).fill(0);
  values.forEach((ms) => counts[Math.min(nBins - 1, Math.floor(ms / 1000 / binW))]++);
  const W = 560, H = 210, pad = 30, bw = (W - pad * 2) / nBins, top = Math.max(...counts);
  const rects = counts.map((c, i) => {
    const h = (c / top) * (H - 60);
    return `<rect x="${pad + i * bw + 3}" y="${H - 30 - h}" width="${bw - 6}" height="${h}" rx="4" fill="#1D3D5C"></rect>
            ${c ? `<text x="${pad + i * bw + bw / 2}" y="${H - 36 - h}" text-anchor="middle" font-size="12" fill="#5B6B7B">${c}</text>` : ""}
            <text x="${pad + i * bw + bw / 2}" y="${H - 12}" text-anchor="middle" font-size="12" fill="#5B6B7B">${(i * binW).toFixed(binW < 1 ? 1 : 0)}s</text>`;
  }).join("");
  el.innerHTML = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Histogram of response times" style="width:100%;margin-top:12px">${rects}
    <line x1="${pad}" x2="${W - pad}" y1="${H - 30}" y2="${H - 30}" stroke="#D5DEE6"></line></svg>`;
}

function ledger(el, items) {
  el.innerHTML = items.map((m) => `<div class="m"><div class="v ${m.cls || ""}">${m.v}</div><div class="l">${esc(m.l)}</div></div>`).join("");
}

async function renderFlow(view) {
  const box = $("flow");
  document.querySelectorAll(".tab").forEach((t) => t.setAttribute("aria-selected", String(t.dataset.view === view)));
  box.innerHTML = `<p class="muted small">Drawing…</p>`;
  try {
    const data = await (await fetch(`/api/flowchart?view=${view}`)).json();
    if (!data.mermaid) { box.innerHTML = `<p class="muted small">No conversations yet. Send a message in the chat to see its flow here.</p>`; return; }
    const { svg } = await mermaid.render("m" + Date.now(), data.mermaid);
    box.innerHTML = svg;
  } catch (e) {
    box.innerHTML = `<p class="chip bad">Could not draw the flowchart (${esc(e.message || e)}). Check your internet connection for the diagram library.</p>`;
  }
}

function renderDashboard(d) {
  const ev = d.eval, s = ev?.summary, live = d.live;
  $("app").hidden = false;
  $("banner").hidden = !!ev;
  $("subtitle").textContent = `AI support agent: ${ev ? ev.meta.provider + " / " + ev.meta.model : d.provider + " / " + d.model}`;

  $("heroRate").textContent = s ? pct(s.task_success_rate) : "–";
  $("heroSub").textContent = s ? `${s.passed} of ${s.total} scenarios passed every check` : "Run the evaluation to measure";
  strip($("strip"), ev?.scenarios, true);

  ledger($("ledger"), [
    { v: s ? s.policy_violations : "–", l: `Policy violations (${s ? s.policy_checks : "–"} checks)`, cls: s ? (s.policy_violations === 0 ? "good" : "bad") : "" },
    { v: s ? pct(s.tool_selection_accuracy) : "–", l: "Right tool chosen" },
    { v: s ? pct(s.escalation_accuracy) : "–", l: "Correct human handoffs" },
    { v: s ? pct(s.ai_resolution_rate) : "–", l: "Resolved without a human" },
    { v: s ? secs(s.latency_p95_ms) : "–", l: "p95 response time" },
    { v: s ? money(s.cost_per_conversation_usd) : "–", l: "Cost per conversation" },
    ...(s && s.judge_avg ? [{ v: s.judge_avg + " / 5", l: "Reply quality (LLM judge)" }] : []),
  ]);

  bars($("catBars"), (ev?.by_category || []).map((c) => ({ label: c.category, value: c.success_rate, display: `${c.passed}/${c.n}` })), { max: 1, rate: true });
  latencyChart($("latency"), (ev?.scenarios || []).flatMap((x) => x.turn_latency_ms));

  $("runMeta").textContent = ev ? `${ev.meta.scenarios} scenarios, run ${ev.meta.run_at.replace("T", " ").slice(0, 16)} UTC` : "";
  $("scnTable").innerHTML = ev ? `
    <thead><tr><th>Scenario</th><th>Category</th><th>Result</th><th>Tools used</th><th>Time</th><th>Cost</th><th>Failed checks</th></tr></thead>
    <tbody>${ev.scenarios.map((r) => `<tr>
      <td>${esc(r.id)}</td><td>${esc(r.category)}</td>
      <td><span class="chip ${r.passed ? "ok" : "bad"}">${r.passed ? "passed" : "failed"}</span></td>
      <td class="tools">${esc([...new Set(r.tools)].join(", ") || "none")}</td>
      <td class="num">${secs(r.turn_latency_ms.reduce((a, b) => a + b, 0))}</td>
      <td class="num">${money(r.cost_usd)}</td>
      <td class="small">${esc(r.checks.filter((c) => !c.passed).map((c) => c.name).join("; "))}</td></tr>`).join("")}</tbody>`
    : `<tbody><tr><td class="muted">No evaluation results yet.</td></tr></tbody>`;

  ledger($("liveLedger"), [
    { v: live.conversations, l: "Conversations" },
    { v: live.handled_by_ai, l: "Handled by AI" },
    { v: pct(live.escalation_rate), l: "Handed to a specialist" },
    { v: secs(live.latency_avg_ms), l: "Average response time" },
    { v: money(live.cost_per_conversation_usd), l: "Cost per conversation" },
    { v: `${live.refunds_issued} / ${money(live.refund_value_usd, 0)}`, l: "Refunds issued by AI" },
  ]);
  bars($("toolBars"), Object.entries(live.tool_usage).map(([k, v]) => ({ label: k.replace(/_/g, " "), value: v, display: v })));
  $("tickets").innerHTML = live.tickets.length ? `
    <thead><tr><th>Ticket</th><th>Priority</th><th>Reason</th><th>Order</th></tr></thead>
    <tbody>${live.tickets.map((t) => `<tr><td class="num">${t.ticket_id}</td>
      <td><span class="chip ${t.priority === "high" || t.priority === "urgent" ? "bad" : "warn"}">${esc(t.priority)}</span></td>
      <td>${esc(t.reason)}</td><td>${esc(t.order_id || "–")}</td></tr>`).join("")}</tbody>`
    : `<tbody><tr><td class="muted">No tickets yet.</td></tr></tbody>`;

  document.querySelectorAll(".tab").forEach((t) => t.addEventListener("click", () => renderFlow(t.dataset.view)));
  return renderFlow("architecture");
}

function renderThumbnail(d) {
  document.body.classList.add("thumb-mode");
  $("thumb").hidden = false;
  const ev = d.eval, s = ev?.summary;
  $("tModel").innerHTML = ev ? `Evaluated on ${ev.meta.scenarios} scenarios<br>${esc(ev.meta.model)}` : esc(d.model);
  $("tRate").textContent = s ? pct(s.task_success_rate) : "–";
  $("tCaption").textContent = s ? `of test conversations solved end to end (${s.passed}/${s.total})` : "of test conversations solved end to end";
  $("tKpis").innerHTML = [
    { v: s ? s.policy_violations : "–", l: "policy violations", cls: s && s.policy_violations === 0 ? "good" : "" },
    { v: s ? pct(s.ai_resolution_rate) : "–", l: "resolved without a human" },
    { v: s ? secs(s.latency_p95_ms) : "–", l: "p95 response time" },
    { v: s ? money(s.cost_per_conversation_usd, 3) : "–", l: "cost per conversation" },
  ].map((m) => `<div><div class="v ${m.cls || ""}">${m.v}</div><div class="l">${m.l}</div></div>`).join("");
  bars($("tBars"), (ev?.by_category || []).map((c) => ({ label: c.category, value: c.success_rate, display: pct(c.success_rate) })), { max: 1, rate: true });
  if (!ev) $("tBars").innerHTML = ["Refunds", "Tracking", "Security", "Policy questions", "Escalation"].map((c) =>
    `<div class="bar"><span>${c}</span><div class="track"></div><span class="val">–</span></div>`).join("");
  strip($("tStrip"), ev?.scenarios, false);
  $("tGuards").innerHTML = d.guardrails.map((g) => `<li>${esc(g)}</li>`).join("");
  const model = ev ? ev.meta.model : d.model;
  $("tFlow").innerHTML = [
    ["Web chat + WhatsApp", "Customer channels", ""],
    ["Agent loop", esc(model), "core"],
    [`${d.tool_count} tools`, "Guardrails enforced in code", ""],
    ["Orders + policy docs", "Verified data only", ""],
    ["Reply or handoff", "Specialist ticket with summary", ""],
  ].map(([k, sub, cls]) => `<div class="t-step ${cls}"><div class="t-k">${k}</div><div class="t-d">${sub}</div></div>`).join("");
  $("tWatermark").hidden = !!ev;
}

(async () => {
  try {
    const d = await (await fetch("/api/dashboard")).json();
    if (THUMB) renderThumbnail(d); else await renderDashboard(d);
  } catch (e) {
    document.body.innerHTML = `<p style="padding:40px">Could not load dashboard data: ${esc(e.message)}. Is the server running?</p>`;
  } finally {
    await document.fonts?.ready;
    window.__ready = true;
  }
})();

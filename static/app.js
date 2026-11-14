// Questathon CRM: vanilla JS, no build step. Every id shown is a chip that opens its source record.
"use strict";

const state = { portfolio: null, accounts: {}, thresholds: null, rebuiltAt: null };
const TABS = [
  ["brief", "Brief"], ["deals", "Deals"], ["people", "People"], ["claims", "Claims"],
  ["activity", "Activity"], ["evidence", "Evidence"], ["metrics", "Metrics"],
];
const STAGES = ["identified", "qualified", "cleared", "agreed"];

// ---------- helpers ----------
const $ = (sel, root = document) => root.querySelector(sel);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const encRef = (ref) => ref.split("/").map(encodeURIComponent).join("/");
const SYMBOL = { GBP: "£", EUR: "€", USD: "$" };
function money(v, cur) {
  if (v === null || v === undefined) return '<span class="muted">—</span>';
  return `<span class="money">${SYMBOL[cur] || ""}${Number(v).toLocaleString("en-GB")}</span>`;
}
function chip(ref, label) { return ref ? `<button class="chip" data-ref="${esc(ref)}" title="${esc(ref)}">${esc(label || ref)}</button>` : ""; }
function chips(refs, max = 12) {
  const list = [...new Set((refs || []).filter(Boolean))];
  const more = list.length > max ? `<span class="muted"> +${list.length - max} more</span>` : "";
  return list.length ? `<div class="chips">${list.slice(0, max).map((r) => chip(r)).join("")}${more}</div>` : "";
}
const badge = (text, cls) => `<span class="badge ${cls}">${esc(text)}</span>`;
const verdict = (v) => badge(v, "v-" + v);
const stance = (s) => badge(s || "unknown", "s-" + (s || "unknown"));
function waitCls(days) {
  const t = state.thresholds?.waiting_on_us_days || { amber: 7, red: 14 };
  return days >= t.red ? "b-red" : days >= t.amber ? "b-amber" : "b-green";
}
function coldCls(days) {
  const t = state.thresholds?.cold_contact_days || { amber: 30, red: 60 };
  if (days === null || days === undefined) return "b-grey";
  return days >= t.red ? "b-red" : days >= t.amber ? "b-amber" : "b-green";
}
function renewalCls(days) { return days === null || days === undefined ? "b-grey" : days <= 60 ? "b-red" : days <= 120 ? "b-amber" : "b-green"; }
const plural = (n, word) => `${n} ${word}${n === 1 ? "" : "s"}`;
function toast(msg) { const t = $("#toast"); t.textContent = msg; t.classList.add("show"); setTimeout(() => t.classList.remove("show"), 2600); }
function ago(iso) {
  if (!iso) return "never";
  const mins = Math.round((Date.now() - new Date(iso).getTime()) / 60000);
  return mins < 1 ? "just now" : mins < 60 ? `${mins} min ago` : mins < 1440 ? `${Math.round(mins / 60)} h ago` : `${Math.round(mins / 1440)} d ago`;
}
async function api(path, opts) {
  const r = await fetch(path, opts);
  if (!r.ok) throw Object.assign(new Error(`${r.status} ${path}`), { status: r.status, body: await r.json().catch(() => ({})) });
  return r.json();
}

// ---------- data ----------
async function loadPortfolio(force) {
  if (!state.portfolio || force) {
    state.portfolio = await api("/api/portfolio");
    state.thresholds = state.portfolio.thresholds;
    state.rebuiltAt = state.portfolio.rebuilt_at;
  }
  return state.portfolio;
}
async function loadAccount(id, force) {
  if (!state.accounts[id] || force) state.accounts[id] = await api(`/api/account/${encodeURIComponent(id)}`);
  return state.accounts[id];
}

// ---------- shell ----------
function renderShell(p) {
  $("#asof").textContent = `In-game ${p.as_of}`;
  $("#sync").textContent = `Synced ${ago(p.last_sync)}${p.live_mode ? " · live" : " · snapshots"}`;
  $("#sync-btn").disabled = p.live_mode;
  $("#sync-btn").title = p.live_mode ? "Live mode: the scheduler keeps the data current" : "Re-read the local snapshots and rebuild";
  const route = parseRoute();
  $("#account-nav").innerHTML = p.accounts.map((a) => {
    const flags = (a.longest_wait_days >= (state.thresholds?.waiting_on_us_days?.red || 14) ? 1 : 0) + (a.stopped_flying ? 1 : 0) + (a.claims.contradicted ? 1 : 0);
    return `<a class="nav-item ${route.account === a.id ? "active" : ""}" href="#/a/${a.id}">
      <span>${esc(a.name || a.id)}<span class="sub">${a.id}${a.days_to_renewal !== null ? ` · renews in ${a.days_to_renewal}d` : ""}</span></span>
      ${flags ? badge(flags, "b-red") : ""}</a>`;
  }).join("");
  document.querySelector('[data-nav="portfolio"]').classList.toggle("active", !route.account);
}

function parseRoute() {
  const parts = location.hash.replace(/^#\/?/, "").split("/").filter(Boolean);
  if (parts[0] === "a" && parts[1]) return { account: decodeURIComponent(parts[1]), tab: parts[2] || "brief" };
  return { account: null };
}

async function render() {
  const main = $("#main");
  try {
    const p = await loadPortfolio();
    renderShell(p);
    const route = parseRoute();
    if (!route.account) { main.innerHTML = portfolioView(p); return; }
    const acct = await loadAccount(route.account);
    main.innerHTML = accountView(acct, route.tab);
    bindTab(route.tab, acct);
  } catch (e) {
    main.innerHTML = `<div class="panel"><h2>Couldn't load</h2><p class="muted">${esc(e.message)}</p></div>`;
  }
}

// ---------- portfolio ----------
function portfolioView(p) {
  const red = state.thresholds?.waiting_on_us_days?.red || 14;
  const rows = p.accounts.map((a) => `
    <tr class="clickable" onclick="location.hash='#/a/${a.id}'">
      <td style="min-width:190px"><strong>${esc(a.name || a.id)}</strong><div class="muted small">${chip(a.id + "/" + a.id, a.id)} ${esc(a.sector || "")}</div></td>
      <td class="num">${money(a.annual_value, a.currency)}</td>
      <td>${a.days_to_renewal !== null ? badge(`${a.days_to_renewal} days`, renewalCls(a.days_to_renewal)) : '<span class="muted">—</span>'}<div class="muted small nowrap">${esc(a.term_end || "")}</div></td>
      <td class="num">${money(a.pipeline, a.currency)}<div class="muted">${plural(a.deals, "deal")}</div></td>
      <td class="num">${a.utilisation_30d !== null && a.utilisation_30d !== undefined ? Math.round(a.utilisation_30d * 100) + "%" : "—"}
        <div class="muted">${a.docks ?? "?"} docks${a.idle_docks ? `, ${a.idle_docks} idle` : ""}${a.stopped_flying ? `, ${a.stopped_flying} stopped` : ""}</div></td>
      <td>${a.unanswered ? badge(`${a.unanswered} waiting · ${a.longest_wait_days}d`, waitCls(a.longest_wait_days)) : badge("none", "b-green")}</td>
      <td><div class="stack-tight">${a.claims.contradicted ? badge(`${a.claims.contradicted} contradicted`, "b-red") : ""}${a.claims.unverifiable ? badge(`${a.claims.unverifiable} open`, "b-grey") : ""}</div></td>
    </tr>`).join("");
  const urgent = p.accounts.filter((a) => a.longest_wait_days >= red).length;
  return `
    <div class="spread"><div><h1>Portfolio</h1><div class="muted">${plural(p.accounts.length, "account")} · in-game ${p.as_of}</div></div></div>
    <div class="kpis" style="margin:16px 0">
      <div class="kpi"><div class="label">Accounts</div><div class="value">${p.accounts.length}</div></div>
      <div class="kpi ${urgent ? "red" : "green"}"><div class="label">Customers waiting ≥ ${red} days</div><div class="value">${urgent}</div></div>
      <div class="kpi"><div class="label">Renewals within 120 days</div><div class="value">${p.accounts.filter((a) => a.days_to_renewal !== null && a.days_to_renewal <= 120).length}</div></div>
      <div class="kpi ${p.accounts.some((a) => a.claims.contradicted) ? "amber" : ""}"><div class="label">Contradicted claims</div><div class="value">${p.accounts.reduce((n, a) => n + (a.claims.contradicted || 0), 0)}</div></div>
      <div class="kpi"><div class="label">Live events</div><div class="value">${p.signals.length}</div></div>
    </div>
    <div class="grid portfolio-grid">
      <div class="panel" style="padding:0; overflow-x:auto"><table>
        <thead><tr><th>Account</th><th class="num">Contract / yr</th><th>Renewal</th><th class="num">Pipeline</th><th class="num">Utilisation 30d</th><th>Waiting on us</th><th>Claims</th></tr></thead>
        <tbody>${rows}</tbody></table></div>
      <div class="panel"><h2>Live events</h2>${feed(p.signals)}</div>
    </div>`;
}

function feed(signals) {
  if (!signals.length) return '<p class="empty">No signals yet.</p>';
  return signals.map((s) => `<div class="item"><div class="row"><span class="badge k-signal">${esc(s.dated)}</span>
    <a href="#/a/${s.account}">${esc(s.account)}</a></div><div><strong>${esc(s.title)}</strong></div>
    <div class="muted">${esc(s.summary)}</div>${chips([s.account + "/" + s.id, ...(s.records || []).map((r) => r.path.startsWith(s.account + "/") ? r.path : s.account + "/" + r.path)])}</div>`).join("");
}

// ---------- account ----------
function accountView(d, tab) {
  const s = d.summary;
  const counts = { deals: d.deals.length, people: d.people.length, claims: d.claims.length, activity: d.activity.length, evidence: d.evidence.length, metrics: d.metrics.length };
  const tabs = TABS.map(([k, label]) => `<a class="tab ${k === tab ? "active" : ""}" href="#/a/${s.id}/${k}">${label}${counts[k] !== undefined ? `<span class="count">${counts[k]}</span>` : ""}</a>`).join("");
  const body = { brief: briefTab, deals: dealsTab, people: peopleTab, claims: claimsTab, activity: activityTab, evidence: evidenceTab, metrics: metricsTab }[tab] || briefTab;
  return `
    <div class="spread">
      <div><h1>${esc(s.name || s.id)}</h1>
        <div class="row muted">${chip(s.id + "/" + s.id, "account " + s.id)} ${esc(s.sector || "")} ${s.has_sheet ? "" : badge("no account sheet", "b-amber")}</div>
        ${s.official_story ? `<p class="story">“${esc(s.official_story)}” <span class="tag">official story, unverified</span></p>` : ""}
      </div>
    </div>
    <div class="kpis">
      <div class="kpi"><div class="label">Contract / year</div><div class="value">${money(s.annual_value, s.currency)}</div><div class="note">ends ${esc(s.term_end || "?")}</div></div>
      <div class="kpi ${s.days_to_renewal !== null && s.days_to_renewal <= 60 ? "red" : s.days_to_renewal <= 120 ? "amber" : ""}"><div class="label">Renewal in</div><div class="value">${s.days_to_renewal ?? "—"} days</div><div class="note">no auto-renewal</div></div>
      <div class="kpi"><div class="label">Pipeline (qualified+)</div><div class="value">${money(s.pipeline, s.currency)}</div><div class="note">${plural(s.deals, "deal")}</div></div>
      <div class="kpi ${s.utilisation_30d < 0.4 ? "red" : s.utilisation_30d < 0.8 ? "amber" : "green"}"><div class="label">Docks flying (30d)</div><div class="value">${s.utilisation_30d !== null && s.utilisation_30d !== undefined ? Math.round(s.utilisation_30d * 100) + "%" : "—"}</div><div class="note">${s.docks ?? "?"} docks · ${s.idle_docks} idle${s.stopped_flying ? ` · ${s.stopped_flying} stopped` : ""}${s.usage_data_age_days > 14 ? ` · data ${s.usage_data_age_days}d old` : ""}</div></div>
      <div class="kpi ${s.longest_wait_days >= 14 ? "red" : s.longest_wait_days >= 7 ? "amber" : "green"}"><div class="label">Waiting on us</div><div class="value">${s.unanswered}</div><div class="note">${s.unanswered ? `longest ${s.longest_wait_days} days` : "nobody"}</div></div>
      <div class="kpi ${s.claims.contradicted ? "red" : ""}"><div class="label">Claims contradicted</div><div class="value">${s.claims.contradicted || 0}</div><div class="note">${s.claims.unverifiable || 0} unverifiable</div></div>
    </div>
    <nav class="tabs">${tabs}</nav>
    <div id="tab-body">${body(d)}</div>`;
}

// ---------- brief: the five questions (preliminary until Step 3c ranks moves) ----------
function backing(deal) {
  // CLAUDE.md: a deal is unbacked when its worth IS a contradicted claim (premise_of); verified evidence backs the rest.
  const contradicted = deal.claims.filter((c) => c.verdict === "contradicted").length;
  if (!deal.worth_up_to) return { label: "no worth yet", cls: "b-grey", backed: null };
  if (deal.contradicted_premise.length) return { label: "unbacked: its worth is a contradicted claim", cls: "b-red", backed: false };
  if (deal.evidence_best === "verified") return contradicted || deal.weak_claims
    ? { label: contradicted ? `backed · ${contradicted} contradicted claim${contradicted > 1 ? "s" : ""} attached` : "backed · open questions", cls: "b-amber", backed: true }
    : { label: "backed", cls: "b-green", backed: true };
  return { label: `unbacked (${deal.evidence_best || "no evidence"})`, cls: "b-red", backed: false };
}

function briefTab(d) {
  const s = d.summary, cur = s.currency;
  const deals = d.deals.map((x) => ({ ...x, b: backing(x) }));
  const backed = deals.filter((x) => x.b.backed === true), unbacked = deals.filter((x) => x.b.backed === false);
  const sum = (list) => list.reduce((n, x) => n + (x.worth_up_to || 0), 0);
  const wonNoOrder = d.offers.filter((o) => o.status === "won");
  const contradictedLive = d.claims.filter((c) => c.verdict === "contradicted" && c.impact.opportunities.length);
  const metric = (n) => d.metrics.find((m) => m.name === n);
  const waiting = d.people.flatMap((p) => p.waiting_on_us.map((u) => ({ ...u, name: p.name })));
  const unattributed = (metric("contact.unanswered")?.value || []).filter((u) => !u.person);
  const stopped = metric("docks.stopped_flying")?.value || [], idle = metric("docks.idle_but_online")?.value || [];
  // Requests and tickets are asks from the customer (Q3/Q4), not claims for us to confirm.
  const gaps = d.claims.filter((c) => c.verdict === "unverifiable" && c.ask && !["request", "ticket"].includes(c.origin));
  const inferences = d.claims.flatMap((c) => c.possible_causes.map((p) => ({ ...p, claim: c.claim_id })));

  const q1 = `<div class="panel wide"><div class="question"><span class="qnum">Q1</span><strong>What is the book worth?</strong></div>
    <div class="answer">${money(sum(backed), cur)} backed · ${money(sum(unbacked), cur)} unbacked${s.annual_value ? ` · contract in force ${money(s.annual_value, cur)}/yr` : ""}</div>
    <table><thead><tr><th>Deal</th><th>Stage</th><th class="num">Worth</th><th>Backing</th></tr></thead><tbody>
    ${deals.map((x) => `<tr><td>${esc(x.title)} ${chip(x.ref)}</td><td>${esc(x.stage)}</td><td class="num">${money(x.worth_up_to, cur)}</td>
      <td>${badge(x.b.label, x.b.cls)}${chips(x.evidence.filter((e) => e.status === "verified" || e.status === "file").map((e) => e.ref), 4)}</td></tr>`).join("")}
    ${wonNoOrder.map((o) => `<tr><td>${esc(o.title)} ${chip(o.ref)}</td><td>offer</td><td class="num">${money(o.value, cur)}</td><td>${badge("Agreed, no order yet", "b-blue")}</td></tr>`).join("")}
    </tbody></table></div>`;

  const q2 = `<div class="panel"><div class="question"><span class="qnum">Q2</span><strong>Who holds the power?</strong></div>
    <div class="prelim">Roles per deal (decider, payer, user, blocker, champion) come with Step 3c. Below: everyone on file and where they stand.</div>
    ${d.people.map((p) => `<div class="item spread"><div><strong>${esc(p.name)}</strong> ${chip(p.ref)}<div class="muted">${esc(p.role || "")}</div></div>
      <div class="row">${stance(p.relationship)} ${p.waiting_on_us.length ? badge(`waiting ${Math.max(...p.waiting_on_us.map((u) => u.days_waiting))}d`, waitCls(Math.max(...p.waiting_on_us.map((u) => u.days_waiting)))) : ""}
      ${p.claims.some((c) => c.verdict === "contradicted") ? badge(`${p.claims.filter((c) => c.verdict === "contradicted").length} contradicted`, "b-red") : ""}</div></div>`).join("") || '<p class="empty">Nobody on file.</p>'}</div>`;

  const moves = [
    ...waiting.map((u) => ({ text: `Reply to ${u.name}: waiting ${u.days_waiting} days`, refs: u.refs, days: u.days_waiting })),
    ...unattributed.map((u) => ({ text: `Reply to ${u.from}: waiting ${u.days_waiting} days`, refs: u.refs, days: u.days_waiting })),
  ].sort((a, b) => b.days - a.days).slice(0, 3);
  const q3 = `<div class="panel"><div class="question"><span class="qnum">Q3</span><strong>What do we do first?</strong></div>
    <div class="prelim">Candidates only. Step 3c ranks moves by worth × stage × urgency, with an owner and a date.</div>
    ${moves.map((m, i) => `<div class="item"><strong>${i + 1}.</strong> ${esc(m.text)}${chips(m.refs)}</div>`).join("") || '<p class="empty">Nobody is waiting on us.</p>'}</div>`;

  const risks = [
    ...waiting.concat(unattributed).map((u) => ({ cls: waitCls(u.days_waiting), text: `${u.name || u.from} has waited ${u.days_waiting} days for a reply`, refs: u.refs })),
    ...(stopped.length ? [{ cls: "b-red", text: `${plural(stopped.length, "dock")} that patrolled regularly stopped flying`, refs: [`${s.id}/metric/docks.stopped_flying`, ...stopped] }] : []),
    ...(idle.length ? [{ cls: "b-amber", text: `${plural(idle.length, "dock")} online but idle for 4 weeks`, refs: [`${s.id}/metric/docks.idle_but_online`] }] : []),
    ...contradictedLive.map((c) => ({ cls: "b-red", text: `Contradicted: “${c.quote}”`, refs: [c.claim_id, ...c.impact.opportunities] })),
    ...(s.days_to_renewal !== null ? [{ cls: renewalCls(s.days_to_renewal), text: `Renewal in ${s.days_to_renewal} days (${s.term_end}), no new order on file`, refs: [`${s.id}/metric/contract.term`] }] : []),
    ...d.deadlines.map((x) => ({ cls: x.ref ? "b-amber" : "b-grey", text: `Deadline: ${x.text}${x.minute !== undefined ? ` (game minute ${x.minute})` : ""}${x.ref ? "" : " · matches no deal"}`, refs: [x.ref] })),
    ...(s.usage_data_age_days > 14 ? [{ cls: "b-grey", text: `Flight data is ${s.usage_data_age_days} days old`, refs: [`${s.id}/metric/usage.data_through`] }] : []),
  ];
  const order = { "b-red": 0, "b-amber": 1, "b-grey": 2, "b-green": 3 };
  const q4 = `<div class="panel"><div class="question"><span class="qnum">Q4</span><strong>What is about to go wrong?</strong></div>
    <div class="answer">${risks.filter((r) => r.cls === "b-red").length} red · ${risks.filter((r) => r.cls === "b-amber").length} amber</div>
    ${risks.sort((a, b) => order[a.cls] - order[b.cls]).map((r) => `<div class="item"><span class="badge ${r.cls}">${r.cls.slice(2)}</span> ${esc(r.text)}${chips(r.refs)}</div>`).join("")}</div>`;

  const q5 = `<div class="panel"><div class="question"><span class="qnum">Q5</span><strong>What do we still not know?</strong></div>
    <div class="answer">${plural(gaps.length, "open question")} · ${plural(inferences.length, "inference")} to confirm</div>
    ${gaps.slice(0, 12).map((c) => `<div class="item"><div>${esc(c.ask.question)}</div><div class="muted">Ask ${chip(c.ask.who)} · about ${chip(c.claim_id)}</div></div>`).join("")}
    ${inferences.map((i) => `<div class="item"><span class="tag">inference</span> ${esc(i.text)}${chips([i.claim, ...i.ids])}</div>`).join("")}</div>`;

  return `<div class="grid grid-2">${q1}${q4}${q3}${q5}${q2}${signalsPanel(d)}</div>`;
}

function signalsPanel(d) {
  return `<div class="panel"><h2>Live events on this account</h2>${feed(d.signals.slice().sort((a, b) => (b.dated || "").localeCompare(a.dated || "")))}</div>`;
}

// ---------- deals ----------
function dealsTab(d) {
  const cur = d.summary.currency;
  if (!d.deals.length) return '<p class="empty">No deals on this account.</p>';
  return `<div class="grid grid-2">${d.deals.map((x) => {
    const i = STAGES.indexOf(x.stage), b = backing(x);
    return `<div class="panel">
      <div class="spread"><div><strong>${esc(x.title)}</strong><div>${chip(x.ref)}</div></div><div class="num">${money(x.worth_up_to, cur)}<div>${badge(b.label, b.cls)}</div></div></div>
      <div class="stage-bar">${STAGES.map((_, j) => `<span class="${j <= i ? "on" : ""}"></span>`).join("")}</div>
      <div class="stage-labels">${STAGES.map((st) => `<span style="${st === x.stage ? "color:var(--fg);font-weight:600" : ""}">${st}</span>`).join("")}</div>
      ${x.blocker ? `<div class="blocker ${x.blocker.status === "cleared" ? "cleared" : ""}"><strong>Blocker (${esc(x.blocker.status)}):</strong> ${esc(x.blocker.text)}</div>` : ""}
      ${x.deadline ? `<div class="row"><strong>Deadline:</strong> ${esc(x.deadline.text)} ${chip(x.deadline_ref, "deadline")}</div>` : ""}
      <h3 style="margin-top:12px">Evidence</h3>
      ${x.evidence.map((e) => `<div class="item"><span class="badge st-${e.status}">${esc(e.status)}</span> ${esc(e.text)} ${chip(e.ref)} ${e.said_by ? chip(e.said_by) : ""}</div>`).join("")}
      <h3 style="margin-top:12px">Claims it rests on</h3>
      ${x.claims.map((c) => `<div class="item">${verdict(c.verdict)} <span class="quote">${esc(c.quote)}</span> ${chip(c.claim_id)}</div>`).join("") || '<p class="empty">None linked.</p>'}
    </div>`;
  }).join("")}</div>
  ${d.offers.length ? `<div class="panel" style="margin-top:14px"><h2>Offers</h2><table><thead><tr><th>Offer</th><th>Pitched to</th><th class="num">Value</th><th>Status</th></tr></thead><tbody>
    ${d.offers.map((o) => `<tr><td>${esc(o.title)} ${chip(o.ref)}</td><td>${esc(o.pitch_to || "")}</td><td class="num">${money(o.value, cur)}</td>
      <td>${o.status === "won" ? badge("Agreed, no order yet", "b-blue") : badge(o.status, o.status === "declined" ? "b-grey" : "b-amber")}</td></tr>`).join("")}</tbody></table></div>` : ""}`;
}

// ---------- people ----------
function peopleTab(d) {
  if (!d.people.length) return '<p class="empty">Nobody on file.</p>';
  return `<div class="grid grid-3">${d.people.map((p) => `<div class="panel">
    <div class="spread"><div><strong>${esc(p.name)}</strong><div class="muted">${esc(p.role || "")}</div></div>${stance(p.relationship)}</div>
    <div>${chip(p.ref)} ${p.origin === "contact" ? '<span class="tag">contact on file</span>' : ""}</div>
    <dl style="margin-top:10px">
      <dt>Last contact</dt><dd>${esc(p.last_contact || "none on file")}</dd>
      <dt>Last call</dt><dd>${esc(p.last_call || "—")}</dd>
      <dt>Our last email</dt><dd>${esc(p.last_outbound || "—")}</dd>
      <dt>Since we reached out</dt><dd>${p.days_since_two_way !== null && p.days_since_two_way !== undefined ? badge(`${p.days_since_two_way} days`, coldCls(p.days_since_two_way)) : badge("never", "b-grey")}</dd>
    </dl>
    ${p.waiting_on_us.map((u) => `<div class="item">${badge(`waiting ${u.days_waiting}d`, waitCls(u.days_waiting))} since ${esc(u.date)}${chips(u.refs)}</div>`).join("")}
    ${p.claims.length ? `<h3 style="margin-top:10px">Their claims</h3>${p.claims.map((c) => `<div class="item">${verdict(c.verdict)} <span class="quote">${esc(c.quote)}</span> ${chip(c.claim_id)}</div>`).join("")}` : ""}
    ${p.asked_about.length ? `<h3 style="margin-top:10px">Could settle</h3>${chips(p.asked_about)}` : ""}
    ${p.note ? `<p class="muted" style="margin-top:8px">${esc(p.note)}</p>` : ""}
  </div>`).join("")}</div>`;
}

// ---------- claims ----------
function claimsTab(d) {
  const types = [...new Set(d.claims.map((c) => c.type))].sort();
  return `<div class="row" style="margin-bottom:12px">
      <select id="f-verdict"><option value="">All verdicts</option>${["contradicted", "partial", "unverifiable", "supported"].map((v) => `<option>${v}</option>`).join("")}</select>
      <select id="f-type"><option value="">All types</option>${types.map((t) => `<option>${t}</option>`).join("")}</select>
      <input type="search" id="f-text" placeholder="Search quotes…">
    </div>
    <div class="panel" style="padding:0; overflow-x:auto"><table><thead><tr><th>Verdict</th><th>Claim</th><th>Said by</th><th>Type</th><th>Basis</th></tr></thead>
    <tbody id="claim-rows"></tbody></table></div>`;
}
function claimRows(d) {
  const v = $("#f-verdict")?.value, t = $("#f-type")?.value, q = ($("#f-text")?.value || "").toLowerCase();
  const order = { contradicted: 0, partial: 1, unverifiable: 2, supported: 3 };
  const list = d.claims.filter((c) => (!v || c.verdict === v) && (!t || c.type === t) && (!q || (c.quote || "").toLowerCase().includes(q)))
    .sort((a, b) => order[a.verdict] - order[b.verdict]);
  $("#claim-rows").innerHTML = list.map((c, i) => `
    <tr class="clickable" data-claim="${i}"><td>${verdict(c.verdict)}</td><td><span class="quote">${esc(c.quote)}</span><div>${chip(c.claim_id)} <span class="tag">${esc(c.origin)}</span></div></td>
      <td>${c.said_by && c.said_by.includes("/") ? chip(c.said_by) : esc(c.said_by || "")}</td><td>${esc(c.type)}</td><td class="muted">${esc(c.basis)}</td></tr>
    <tr class="detail" data-detail="${i}" hidden><td colspan="5">${claimDetail(c)}</td></tr>`).join("") || '<tr><td colspan="5" class="empty">No claims match.</td></tr>';
  document.querySelectorAll("tr[data-claim]").forEach((tr) => tr.addEventListener("click", (e) => {
    if (e.target.closest(".chip")) return;
    const det = document.querySelector(`tr[data-detail="${tr.dataset.claim}"]`); det.hidden = !det.hidden;
  }));
}
function claimDetail(c) {
  const imp = c.impact || {};
  return `<div class="grid grid-2" style="padding:6px 0">
    <div>${c.note ? `<p>${esc(c.note)}</p>` : ""}
      ${c.tests.length ? `<h3>Tests</h3>${c.tests.map((t) => `<div>${t.passed === null ? badge("no data", "b-grey") : t.passed ? badge("pass", "b-green") : badge("fail", "b-red")}
        <span class="mono">${esc(t.metric)}${t.field ? "." + esc(t.field) : ""} ${esc(t.op)} ${esc(JSON.stringify(t.value))}</span>
        <span class="muted">actual: ${esc(String(JSON.stringify(t.actual)).slice(0, 120))}</span></div>`).join("")}` : ""}
      ${c.numbers.length ? `<h3 style="margin-top:8px">Numbers</h3>${chips(c.numbers)}` : ""}
      <h3 style="margin-top:8px">Sources</h3>${chips(c.source_ids)}</div>
    <div>${c.evidence_for.length ? `<h3>Evidence for</h3>${chips(c.evidence_for)}` : ""}
      ${c.evidence_against.length ? `<h3 style="margin-top:8px">Evidence against</h3>${chips(c.evidence_against)}` : ""}
      ${c.possible_causes.length ? `<h3 style="margin-top:8px">Possible causes</h3>${c.possible_causes.map((p) => `<div class="item"><span class="tag">inference</span> ${esc(p.text)}${chips(p.ids)}</div>`).join("")}` : ""}
      ${[...(imp.q1 || []), ...(imp.q4 || []), ...(imp.q5 || [])].length ? `<h3 style="margin-top:8px">Impact</h3>${[...(imp.q1 || []), ...(imp.q4 || []), ...(imp.q5 || [])].map((x) => `<div>${esc(x)}</div>`).join("")}` : ""}
      ${c.ask ? `<h3 style="margin-top:8px">Ask</h3><div>${esc(c.ask.question)} ${chip(c.ask.who)}</div>` : ""}</div></div>`;
}

// ---------- activity ----------
function activityTab(d) {
  const kinds = [...new Set(d.activity.map((a) => a.kind))];
  return `<div class="row" style="margin-bottom:12px"><select id="f-kind"><option value="">Everything</option>${kinds.map((k) => `<option>${k}</option>`).join("")}</select>
    <input type="search" id="f-act" placeholder="Search…"></div><div class="panel"><div class="timeline" id="timeline"></div></div>`;
}
function activityRows(d) {
  const k = $("#f-kind").value, q = $("#f-act").value.toLowerCase();
  $("#timeline").innerHTML = d.activity.filter((a) => (!k || a.kind === k) && (!q || JSON.stringify(a).toLowerCase().includes(q))).map((a) => `
    <div class="event"><div class="date">${esc(a.date)}</div><div class="kind"><span class="badge k-${a.kind}">${a.kind}</span></div>
      <div><strong>${esc(a.title)}</strong>${a.who ? ` <span class="muted">· ${esc(a.who)}</span>` : ""}
      ${a.detail ? `<div class="muted">${esc(a.detail)}</div>` : ""}${chips([a.ref])}</div></div>`).join("") || '<p class="empty">Nothing matches.</p>';
}

// ---------- evidence + metrics ----------
function evidenceTab(d) {
  return `<div class="panel" style="padding:0; overflow-x:auto"><table><thead><tr><th>Record</th><th>Kind</th><th>Source</th><th class="num">Rows</th></tr></thead><tbody>
    ${d.evidence.map((e) => `<tr class="clickable" data-open="${esc(e.ref)}"><td>${esc(e.title || e.path)}<div>${chip(e.ref)}</div></td><td>${esc(e.kind)}</td>
      <td class="muted mono">${esc(e.source)}</td><td class="num">${e.rows ?? ""}</td></tr>`).join("")}</tbody></table></div>`;
}
function metricsTab(d) {
  const short = (v) => { const s = typeof v === "string" ? v : JSON.stringify(v); return s.length > 90 ? s.slice(0, 90) + "…" : s; };
  return `<div class="panel" style="padding:0; overflow-x:auto"><table><thead><tr><th>Metric</th><th>Value</th><th>Definition</th><th class="num">Rows</th></tr></thead><tbody>
    ${d.metrics.map((m) => `<tr class="clickable" data-open="${esc(m.ref)}"><td class="mono">${esc(m.name)}</td><td class="mono">${esc(short(m.value))}</td>
      <td class="muted">${esc(m.definition)}</td><td class="num">${m.row_count}</td></tr>`).join("")}</tbody></table></div>`;
}

function bindTab(tab, d) {
  if (tab === "claims") { ["f-verdict", "f-type", "f-text"].forEach((id) => $("#" + id).addEventListener("input", () => claimRows(d))); claimRows(d); }
  if (tab === "activity") { ["f-kind", "f-act"].forEach((id) => $("#" + id).addEventListener("input", () => activityRows(d))); activityRows(d); }
  document.querySelectorAll("tr[data-open]").forEach((tr) => tr.addEventListener("click", (e) => { if (!e.target.closest(".chip")) openRef(tr.dataset.open); }));
}

// ---------- trace drawer ----------
async function openRef(ref) {
  const drawer = $("#drawer");
  $("#drawer-kind").textContent = "Loading";
  $("#drawer-title").textContent = ref;
  $("#drawer-body").innerHTML = "";
  drawer.classList.add("open"); drawer.setAttribute("aria-hidden", "false");
  try {
    const r = await api("/api/ref/" + encRef(ref));
    $("#drawer-kind").textContent = r.type === "record" ? r.kind : r.type;
    $("#drawer-body").innerHTML = r.type === "metric" ? metricBody(r) : r.type === "row" ? rowBody(r) : recordBody(r);
  } catch (e) {
    $("#drawer-kind").textContent = "Not found";
    $("#drawer-body").innerHTML = `<p class="muted">No record holds ${esc(ref)}.</p>`;
  }
}
function scalarFields(obj) {
  return Object.entries(obj || {}).filter(([, v]) => v === null || typeof v !== "object").map(([k, v]) => `<dt>${esc(k)}</dt><dd>${refOrText(v)}</dd>`).join("");
}
function refOrText(v) {
  const s = String(v ?? "");
  return /^[A-Za-z0-9_]+\/[^\s]+$/.test(s) && !s.includes(" ") ? chip(s) : esc(s);
}
function recordBody(r) {
  const rec = r.record || {};
  const text = typeof rec.text === "string" ? rec.text : null;
  const nested = Object.fromEntries(Object.entries(rec).filter(([k, v]) => v && typeof v === "object" && k !== "data"));
  return `<p>${esc(r.label)}</p><dl><dt>Source</dt><dd class="mono">${esc(r.source)}</dd><dt>Location</dt><dd class="mono">${esc(r.location)}</dd>${scalarFields(Object.fromEntries(Object.entries(rec).filter(([k]) => k !== "text")))}</dl>
    ${text ? `<h3>Text</h3><pre>${esc(text)}</pre>` : ""}
    ${rec.data ? `<h3 style="margin-top:12px">Data</h3><pre>${esc(JSON.stringify(rec.data, null, 2))}</pre>` : ""}
    ${Object.keys(nested).length ? `<h3 style="margin-top:12px">Details</h3><pre>${esc(JSON.stringify(nested, null, 2))}</pre>` : ""}`;
}
function metricBody(m) {
  const cols = [...new Set(m.rows.flatMap((r) => Object.keys(r.row || {})).filter((k) => typeof (m.rows[0]?.row || {})[k] !== "object"))].slice(0, 6);
  return `<p>${esc(m.definition)}</p><dl><dt>As of</dt><dd>${esc(m.as_of)}</dd></dl><h3>Value</h3><pre>${esc(JSON.stringify(m.value, null, 2))}</pre>
    <h3 style="margin-top:12px">Sources</h3>${chips(m.sources)}
    <h3 style="margin-top:12px">Rows counted (${m.rows.length})</h3>
    ${m.rows.length ? `<table><thead><tr><th>Ref</th>${cols.map((c) => `<th>${esc(c)}</th>`).join("")}</tr></thead><tbody>
      ${m.rows.slice(0, 300).map((r) => `<tr><td>${chip(r.ref, r.ref.split("/").slice(1).join("/"))}</td>${cols.map((c) => `<td>${esc((r.row || {})[c])}</td>`).join("")}</tr>`).join("")}</tbody></table>` : '<p class="empty">This metric counts no rows.</p>'}`;
}
function rowBody(r) {
  return `<dl><dt>File</dt><dd>${chip(r.file)}</dd><dt>Row</dt><dd>${r.row_number}</dd><dt>Source</dt><dd>${refOrText(r.source)}</dd>${scalarFields(r.row)}</dl>`;
}

// ---------- events ----------
document.addEventListener("click", (e) => {
  const c = e.target.closest(".chip");
  if (c) { e.preventDefault(); e.stopPropagation(); openRef(c.dataset.ref); }
});
$("#drawer-close").addEventListener("click", () => { $("#drawer").classList.remove("open"); $("#drawer").setAttribute("aria-hidden", "true"); });
document.addEventListener("keydown", (e) => { if (e.key === "Escape") $("#drawer-close").click(); });
$("#sync-btn").addEventListener("click", async () => {
  $("#sync-btn").disabled = true;
  try {
    const r = await api("/api/reload", { method: "POST" });
    toast(r.snapshots_added ? `Loaded ${plural(r.snapshots_added, "new payload")}` : "Already up to date");
    await refresh(true);
  } catch (e) { toast(e.body?.detail || "Sync failed"); }
  $("#sync-btn").disabled = false;
});
window.addEventListener("hashchange", () => { window.scrollTo(0, 0); render(); });

async function refresh(force) {
  state.portfolio = null; state.accounts = {};
  await render();
  if (force) $("#main").classList.add("flash");
  setTimeout(() => $("#main").classList.remove("flash"), 2000);
}
// Poll every 15s; re-render only when the data was rebuilt.
setInterval(async () => {
  try {
    const h = await api("/api/health");
    if (state.rebuiltAt && h.rebuilt_at !== state.rebuiltAt) { toast("New data: views updated"); await refresh(true); }
    else if (state.portfolio) $("#sync").textContent = `Synced ${ago(h.last_sync)}${h.live_mode ? " · live" : " · snapshots"}`;
  } catch (e) { /* keep the last view */ }
}, 15000);

render();

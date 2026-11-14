// FlytBase CRM: vanilla JS, no build step.
// Every item keeps its source ids; a small "Sources" link opens them in the side panel.
"use strict";

const STAGES = ["identified", "qualified", "cleared", "agreed"];
const SYMBOL = { GBP: "£", EUR: "€", USD: "$" };
const OWNERS = { me: "You", "team/achal_negi": "Achal Negi", "team/pranjal_suyal": "Pranjal Suyal", "team/support_desk": "Support desk",
  "team/neel_sharma": "Neel Sharma", "team/srv": "SRV", "team/yashash_rao": "Yashash Rao", "team/sam": "Sam" };
const cache = {};
let rebuiltAt = null;
let liveMode = false;
let today = null;

// ---------- helpers ----------
const $ = (s, r = document) => r.querySelector(s);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const money = (v, cur) => (v === null || v === undefined ? "—" : `${SYMBOL[cur] || ""}${Number(v).toLocaleString("en-GB")}`);
const pill = (text, cls) => `<span class="pill ${cls}">${esc(text)}</span>`;
const sevPill = (s) => pill(s === "grey" ? "info" : s, s);
const healthPill = (h) => `<span class="pill ${h}"><span class="dot ${h}"></span>${{ red: "At risk", amber: "Watch", green: "Healthy" }[h]}</span>`;
const stancePill = (s) => s ? pill(s, { ally: "green", warm: "green", neutral: "grey", guarded: "red" }[s] || "grey") : pill("unknown", "grey");
const owner = (o) => OWNERS[o] || (o || "").replace(/^team\//, "");
const plural = (n, w) => `${n} ${w}${n === 1 ? "" : "s"}`;
const fmtDate = (iso) => { if (!iso) return "—"; const d = new Date(iso + "T00:00:00"); return d.toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" }); };
const encRef = (ref) => ref.split("/").map(encodeURIComponent).join("/");
const ICON_SRC = '<svg viewBox="0 0 24 24"><path d="M14 3h7v7M21 3l-9 9M19 14v5a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V7a2 2 0 0 1 2-2h5"/></svg>';
// Register a source list and return a small link that opens it.
const sourceSets = [];
function sources(list, label) {
  const refs = [...new Set((list || []).filter(Boolean))];
  if (!refs.length) return "";
  sourceSets.push(refs);
  return `<button class="src" data-sources="${sourceSets.length - 1}" title="See where this comes from">${ICON_SRC}${label || plural(refs.length, "source")}</button>`;
}
function toast(m) { const t = $("#toast"); t.textContent = m; t.classList.add("show"); setTimeout(() => t.classList.remove("show"), 2400); }
function ago(iso) {
  if (!iso) return "never";
  const m = Math.round((Date.now() - new Date(iso)) / 60000);
  return m < 1 ? "just now" : m < 60 ? `${m} min ago` : m < 1440 ? `${Math.round(m / 60)} h ago` : `${Math.round(m / 1440)} d ago`;
}
async function api(path, opts) {
  const r = await fetch(path, opts);
  if (!r.ok) throw Object.assign(new Error(`${r.status}`), { status: r.status, body: await r.json().catch(() => ({})) });
  return r.json();
}
async function get(path) { return cache[path] || (cache[path] = await api(path)); }
function crumbs(parts) {
  $("#crumbs").innerHTML = parts.map((p, i) => i === parts.length - 1 ? `<span class="cur">${esc(p[0])}</span>` : `<a href="${p[1]}">${esc(p[0])}</a><span class="sep">/</span>`).join("");
}
const card = (title, body, meta = "", flush = false) => `<section class="card"><div class="card-head"><h3 class="card-title">${title}</h3><span class="card-meta">${meta}</span></div><div class="card-body ${flush ? "flush" : ""}">${body}</div></section>`;
const empty = (t) => `<p class="empty">${esc(t)}</p>`;

// ---------- theme ----------
function currentTheme() {
  const t = document.documentElement.dataset.theme;
  return t || (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
}
$("#theme-btn").addEventListener("click", () => {
  const next = currentTheme() === "dark" ? "light" : "dark";
  document.documentElement.dataset.theme = next;
  try { localStorage.setItem("theme", next); } catch (e) { /* storage unavailable */ }
});

// ---------- router ----------
function route() {
  const p = location.hash.replace(/^#\/?/, "").split("/").filter(Boolean).map(decodeURIComponent);
  return { page: p[0] || "home", id: p[1], tab: p[2] || "overview" };
}
async function render() {
  sourceSets.length = 0;
  const r = route();
  document.querySelectorAll("[data-nav]").forEach((a) => a.classList.toggle("active", a.dataset.nav === r.page));
  const main = $("#main");
  try {
    const home = await get("/api/home");
    rebuiltAt = rebuiltAt || home.rebuilt_at;
    today = home.as_of;
    $("#asof").textContent = `Today ${fmtDate(home.as_of)}`;
    $("#sync").textContent = `Synced ${ago(home.last_sync)}`;
    liveMode = home.live_mode;
    const view = { home: homePage, accounts: r.id ? accountPage : accountsPage, deals: dealsPage, contacts: contactsPage, activities: activitiesPage, insights: insightsPage }[r.page] || homePage;
    main.innerHTML = await view(r, home);
    bindPage(r);
  } catch (e) {
    main.innerHTML = card("Couldn't load this page", `<p class="muted">${esc(e.message)}</p>`);
  }
}

// ---------- home ----------
async function homePage(r, h) {
  crumbs([["Home", "#/"]]);
  const cur = Object.entries(h.totals);
  const book = cur.map(([c, t]) => money(t.backed, c)).join(" · ");
  const unbacked = cur.map(([c, t]) => money(t.unbacked, c)).join(" · ");
  const reds = h.accounts.reduce((n, a) => n + a.red_risks, 0);
  return `
    <div class="page-head"><div><h1>Good day</h1><div class="sub">${plural(h.accounts.length, "account")} · in-game ${fmtDate(h.as_of)}</div></div></div>
    <div class="stats">
      <div class="card stat"><div class="stat-label">Backed book</div><div class="stat-value">${book}</div><div class="stat-note">${unbacked} not backed yet</div></div>
      <div class="card stat"><div class="stat-label">Renewals in 120 days</div><div class="stat-value">${h.renewals_120}</div><div class="stat-note">none renew automatically</div></div>
      <div class="card stat"><div class="stat-label">Customers waiting on us</div><div class="stat-value">${h.waiting}</div><div class="stat-note">messages with no reply</div></div>
      <div class="card stat"><div class="stat-label">Red risks</div><div class="stat-value">${reds}</div><div class="stat-note">across all accounts</div></div>
    </div>
    <div class="grid cols-2">
      ${card("Next moves", h.moves.map((m, i) => moveItem(m, i + 1, true)).join("") || empty("Nothing to do."), "ranked by value and urgency")}
      <div class="grid">
        ${card("Accounts", h.accounts.map((a) => `
          <div class="list-item"><span class="dot ${a.health}"></span><div class="list-main">
            <a class="list-title" href="#/accounts/${a.id}">${esc(a.name)}</a>
            <div class="list-meta"><span>${money(a.annual_value, a.currency)}/yr</span><span>renews ${a.days_to_renewal !== null ? `in ${a.days_to_renewal} days` : "—"}</span></div>
          </div>${healthPill(a.health)}</div>`).join(""))}
        ${card("Live updates", h.signals.map(signalItem).join("") || empty("No live updates yet."))}
      </div>
    </div>
    <div style="margin-top:16px">${card("At risk now", h.risks.map((x) => riskItem(x, true)).join("") || empty("No red risks."), "red, soonest first")}</div>`;
}
function moveItem(m, n, showAccount) {
  return `<div class="list-item"><span class="rank">${n}</span><div class="list-main"><div class="list-title">${esc(m.text)}</div>
    <div class="list-meta">${showAccount ? `<a href="#/accounts/${m.account}">${esc(m.account_name)}</a>` : ""}<span>Owner: ${esc(owner(m.owner))}</span><span>Due ${fmtDate(m.due)}</span>${sources(m.sources)}</div></div></div>`;
}
function riskItem(x, showAccount) {
  return `<div class="list-item"><span class="dot ${x.severity}"></span><div class="list-main"><div class="list-title">${esc(x.text)}</div>
    <div class="list-meta">${showAccount ? `<a href="#/accounts/${x.account}">${esc(x.account_name)}</a>` : ""}<span>${!x.turns_bad_on ? "Date unknown" : x.turns_bad_on <= today ? `Overdue since ${fmtDate(x.turns_bad_on)}` : `Turns bad ${fmtDate(x.turns_bad_on)}`}</span>${sources(x.sources)}</div></div>${sevPill(x.severity)}</div>`;
}
function signalItem(s) {
  return `<div class="list-item"><span class="dot red"></span><div class="list-main"><div class="list-title">${esc(s.title)}</div>
    <div class="list-meta"><span>${fmtDate(s.dated)}</span><a href="#/accounts/${s.account}">Account ${esc(s.account)}</a>${sources([`${s.account}/${s.id}`])}</div></div></div>`;
}

// ---------- accounts ----------
async function accountsPage() {
  crumbs([["Accounts", "#/accounts"]]);
  const { accounts } = await get("/api/accounts");
  return `<div class="page-head"><div><h1>Accounts</h1><div class="sub">${plural(accounts.length, "account")}</div></div></div>
    <section class="card"><div class="card-body flush"><table>
      <thead><tr><th>Account</th><th>Health</th><th class="num">Contract / yr</th><th>Renewal</th><th class="num">Deals</th><th>Next move</th><th>Owner</th></tr></thead>
      <tbody>${accounts.map((a) => `<tr class="row-link" data-href="#/accounts/${a.id}">
        <td><div class="cell-title">${esc(a.name)}</div><div class="cell-sub">${esc(a.sector || "")}</div></td>
        <td>${healthPill(a.health)}</td><td class="num">${money(a.annual_value, a.currency)}</td>
        <td class="nowrap">${fmtDate(a.term_end)}<div class="cell-sub">${a.days_to_renewal !== null ? `in ${a.days_to_renewal} days` : ""}</div></td>
        <td class="num">${a.open_deals}</td><td>${a.next_move ? esc(a.next_move.text) + `<div class="cell-sub">Due ${fmtDate(a.next_move.due)}</div>` : '<span class="faint">—</span>'}</td>
        <td>${esc(a.owner)}</td></tr>`).join("")}</tbody></table></div></section>`;
}

async function accountPage(r) {
  const d = await get(`/api/accounts/${encodeURIComponent(r.id)}`);
  const a = d.account, ans = d.answers;
  crumbs([["Accounts", "#/accounts"], [a.name, `#/accounts/${a.id}`]]);
  const tabs = [["overview", "Overview"], ["deals", `Deals (${d.deals.length})`], ["contacts", `Contacts (${d.contacts.length})`], ["activity", "Activity"]];
  const body = { overview: accountOverview, deals: accountDeals, contacts: accountContacts, activity: accountActivity }[r.tab] || accountOverview;
  return `<section class="card record-head">
      <div class="page-head" style="margin:0"><div class="record-title"><div class="record-icon">${esc(a.id)}</div>
        <div><h1>${esc(a.name)}</h1><div class="sub">${esc(a.sector || "")}</div></div></div>${healthPill(a.health)}</div>
      <div class="fields">
        <div><div class="field-label">Contract / yr</div><div class="field-value">${money(a.annual_value, a.currency)}</div></div>
        <div><div class="field-label">Renewal</div><div class="field-value">${fmtDate(a.term_end)} ${a.days_to_renewal !== null ? `<span class="faint">(${a.days_to_renewal} days)</span>` : ""}</div></div>
        <div><div class="field-label">Backed book</div><div class="field-value">${money(ans.q1.backed_total, a.currency)}</div></div>
        <div><div class="field-label">Docks flying (30 days)</div><div class="field-value">${a.utilisation_30d !== null && a.utilisation_30d !== undefined ? Math.round(a.utilisation_30d * 100) + "%" : "—"} <span class="faint">of ${a.docks ?? "?"}</span></div></div>
        <div><div class="field-label">Account owner</div><div class="field-value">${esc(a.owner)}</div></div>
      </div>
      ${d.official_story ? `<div class="story">“${esc(d.official_story)}” <span class="faint">· account sheet, unverified</span></div>` : ""}
    </section>
    <nav class="tabs">${tabs.map(([k, l]) => `<a class="tab ${r.tab === k ? "active" : ""}" href="#/accounts/${a.id}/${k}">${l}</a>`).join("")}</nav>
    ${body(d)}`;
}

function accountOverview(d) {
  const a = d.account, q = d.answers, cur = a.currency;
  const book = `
    <div class="list-item"><div class="list-main"><div class="list-title">${money(q.q1.backed_total, cur)} backed</div><div class="list-meta">${sources(q.q1.backed_sources)}</div></div></div>
    <div class="list-item"><div class="list-main"><div class="list-title">${money(q.q1.unbacked_total, cur)} not backed</div><div class="list-meta"><span>told or claimed, no file behind it</span>${sources(q.q1.unbacked_sources)}</div></div></div>
    ${q.q1.agreed_no_order.map((o) => `<div class="list-item"><div class="list-main"><div class="list-title">${money(o.value, cur)} · ${esc(o.text)}</div><div class="list-meta">${pill("Agreed, no order yet", "blue")}${sources(o.sources)}</div></div></div>`).join("")}`;
  const mainDeal = q.q2.deals.find((x) => x.roles.some((r) => r.who)) || q.q2.deals[0];
  const power = mainDeal ? `<div class="faint small" style="margin:8px 0 10px">${esc(mainDeal.text)}</div><div class="roles">${mainDeal.roles.map((r) => `
      <span class="role-name">${r.role}</span><span>${r.who ? `${esc(r.name)} ${stancePill(r.stance)}` : `<span class="faint">Unknown${r.ask ? ` · ask ${esc(nameOf(d, r.ask))}` : ""}</span>`}</span>`).join("")}</div>
      <button class="more" data-href="#/accounts/${a.id}/deals">All deals and roles</button>` : empty("No deals on file.");
  const risks = q.q4.risks.slice(0, 5).map((x) => riskItem(x, false)).join("");
  const gaps = q.q5.gaps.slice(0, 4).map((g) => `<div class="list-item"><span class="dot grey"></span><div class="list-main"><div class="list-title">${esc(g.text)}</div>
      <div class="list-meta">${g.who ? `<span>Ask ${esc(nameOf(d, g.who))}</span>` : ""}${g.status === "inference" ? pill("inference", "violet") : ""}${sources(g.sources)}</div></div></div>`).join("");
  return `<div class="grid cols-2">
      <div class="grid">
        ${card("Next steps", q.q3.moves.map((m, i) => moveItem(m, i + 1, false)).join("") || empty("Nothing urgent."), "top 3")}
        ${card("Risks", (risks || empty("No open risks.")) + (q.q4.risks.length > 5 ? `<button class="more" data-more="risks">Show all ${q.q4.risks.length}</button>` : ""), q.q4.answer)}
      </div>
      <div class="grid">
        ${card("Book of business", book)}
        ${card("Who holds the power", power)}
        ${card("Open questions", (gaps || empty("Nothing open.")) + (q.q5.gaps.length > 4 ? `<button class="more" data-more="gaps">Show all ${q.q5.gaps.length}</button>` : ""))}
      </div>
    </div>`;
}
function nameOf(d, key) {
  if (!key) return "";
  const c = d.contacts.find((x) => x.ref === key);
  return c ? c.name : owner(key);
}

function accountDeals(d) {
  return `<section class="card"><div class="card-body flush"><table>
    <thead><tr><th>Deal</th><th>Stage</th><th class="num">Worth</th><th>Backing</th><th>Next step</th></tr></thead>
    <tbody>${d.deals.map((x) => `<tr class="row-link" data-deal="${esc(x.id)}">
      <td><div class="cell-title">${esc(x.title)}</div>${x.blocker && x.blocker.status === "open" ? `<div class="cell-sub">Blocked: ${esc(x.blocker.text)}</div>` : ""}</td>
      <td>${stagePill(x.stage)}</td><td class="num">${money(x.worth, x.currency)}</td>
      <td>${x.backing ? pill(x.backing, x.backing === "backed" ? "green" : "amber") : '<span class="faint">—</span>'}</td>
      <td>${x.next_move ? esc(x.next_move.text) : '<span class="faint">—</span>'}</td></tr>`).join("") || `<tr><td colspan="5" class="empty">No deals.</td></tr>`}</tbody></table></div></section>`;
}
const stagePill = (s) => pill(s, { identified: "grey", qualified: "blue", cleared: "violet", agreed: "green" }[s] || "grey");

function accountContacts(d) { return contactsTable(d.contacts, false); }
function contactsTable(list, showAccount) {
  return `<section class="card"><div class="card-body flush"><table>
    <thead><tr><th>Name</th>${showAccount ? "<th>Account</th>" : ""}<th>Role</th><th>Stance</th><th>Last contact</th><th>Waiting on us</th></tr></thead>
    <tbody>${list.map((c) => `<tr class="row-link" data-ref="${esc(c.ref)}">
      <td class="cell-title">${esc(c.name)}</td>${showAccount ? `<td><a href="#/accounts/${c.account}">${esc(c.account_name)}</a></td>` : ""}
      <td class="muted">${esc(c.role || "")}</td><td>${stancePill(c.relationship)}</td>
      <td class="nowrap">${fmtDate(c.last_contact)}${c.days_since_two_way !== null && c.days_since_two_way !== undefined && c.days_since_two_way >= 30 ? `<div class="cell-sub">we last reached out ${c.days_since_two_way} days ago</div>` : ""}</td>
      <td>${c.waiting_days ? pill(`${c.waiting_days} days`, c.waiting_days >= 14 ? "red" : "amber") : '<span class="faint">—</span>'}</td></tr>`).join("") || `<tr><td colspan="6" class="empty">No contacts.</td></tr>`}</tbody></table></div></section>`;
}

const KIND_ICON = {
  call: '<path d="M5 4h4l2 5-2.5 1.5a11 11 0 0 0 5 5L15 13l5 2v4a2 2 0 0 1-2 2A16 16 0 0 1 3 6a2 2 0 0 1 2-2"/>',
  email: '<path d="M4 6h16v12H4zM4 7l8 6 8-6"/>', ticket: '<path d="M4 8V6h16v2a2 2 0 0 0 0 4v2H4v-2a2 2 0 0 0 0-4z"/>',
  request: '<path d="M4 8V6h16v2a2 2 0 0 0 0 4v2H4v-2a2 2 0 0 0 0-4z"/>', signal: '<path d="M12 3v10M12 17v.5"/>',
  note: '<path d="M6 4h12v16H6zM9 8h6M9 12h6"/>', history: '<circle cx="12" cy="12" r="8"/><path d="M12 8v4l3 2"/>',
};
function activityList(items, showAccount) {
  return items.map((x) => `<div class="tl-item"><span class="tl-icon k-${x.kind}"><svg viewBox="0 0 24 24">${KIND_ICON[x.kind] || KIND_ICON.note}</svg></span>
    <div><div class="list-title">${esc(x.title)}</div><div class="list-meta">${showAccount ? `<a href="#/accounts/${x.account}">${esc(x.account_name)}</a>` : ""}${x.who ? `<span>${esc(x.who)}</span>` : ""}${sources([x.ref], "Open")}</div></div>
    <span class="tl-date">${fmtDate(x.date)}</span></div>`).join("") || empty("Nothing yet.");
}
function accountActivity(d) { return card("Activity", activityList(d.activity, false), plural(d.activity.length, "item")); }

// ---------- deals pipeline ----------
async function dealsPage() {
  crumbs([["Deals", "#/deals"]]);
  const { deals } = await get("/api/deals");
  const lanes = STAGES.map((s) => {
    const list = deals.filter((x) => x.stage === s);
    return `<div class="lane"><div class="lane-head"><span>${s}</span><span class="lane-sum">${list.length}</span></div>
      ${list.map((x) => `<div class="deal-card" data-deal="${esc(x.id)}"><div class="acct">${esc(x.account_name)}</div><div class="title">${esc(x.title)}</div>
        <div class="foot">${x.worth === null ? '<span class="faint small">No value yet</span>' : `<strong>${money(x.worth, x.currency)}</strong>`}${x.backing ? pill(x.backing, x.backing === "backed" ? "green" : "amber") : ""}</div></div>`).join("") || '<p class="empty small" style="padding:0 6px">No deals</p>'}</div>`;
  }).join("");
  return `<div class="page-head"><div><h1>Deals</h1><div class="sub">${plural(deals.length, "deal")} across all accounts</div></div></div><div class="board">${lanes}</div>`;
}

// ---------- contacts + activities ----------
async function contactsPage() {
  crumbs([["Contacts", "#/contacts"]]);
  const { contacts } = await get("/api/contacts");
  return `<div class="page-head"><div><h1>Contacts</h1><div class="sub">${plural(contacts.length, "person", )}</div></div>
    <input type="search" id="q" placeholder="Search contacts"></div><div id="list">${contactsTable(contacts, true)}</div>`;
}
async function activitiesPage() {
  crumbs([["Activities", "#/activities"]]);
  const { activities } = await get("/api/activities");
  const kinds = [...new Set(activities.map((x) => x.kind))];
  return `<div class="page-head"><div><h1>Activities</h1><div class="sub">Calls, emails, tickets and updates across all accounts</div></div></div>
    <div class="filters"><div class="seg" id="kind-seg"><button class="on" data-kind="">All</button>${kinds.map((k) => `<button data-kind="${k}">${k[0].toUpperCase() + k.slice(1)}s</button>`).join("")}</div></div>
    <section class="card"><div class="card-body" id="list">${activityList(activities.slice(0, 120), true)}</div></section>`;
}

// ---------- insights: claims vs evidence (separate section) ----------
async function insightsPage() {
  crumbs([["Insights", "#/insights"]]);
  const { claims } = await get("/api/insights");
  const accounts = [...new Map(claims.map((c) => [c.account, c.account_name])).entries()];
  return `<div class="page-head"><div><h1>Insights</h1><div class="sub">What customers told us, checked against what the paperwork shows</div></div></div>
    <div class="filters">
      <select id="f-acct"><option value="">All accounts</option>${accounts.map(([id, n]) => `<option value="${id}">${esc(n)}</option>`).join("")}</select>
      <div class="seg" id="verdict-seg">${["contradicted", "partial", "unverifiable", "supported", ""].map((v, i) => `<button class="${i === 0 ? "on" : ""}" data-verdict="${v}">${v ? v[0].toUpperCase() + v.slice(1) : "All"}</button>`).join("")}</div>
    </div>
    <section class="card"><div class="card-body flush" id="list"></div></section>`;
}
const VERDICT = { contradicted: "red", partial: "amber", unverifiable: "grey", supported: "green" };
function insightRows(claims) {
  return `<table><thead><tr><th>Verdict</th><th>Claim</th><th>Said by</th><th>Account</th></tr></thead><tbody>${claims.map((c) => `
    <tr class="row-link" data-claim="${esc(c.claim_id)}"><td>${pill(c.verdict, VERDICT[c.verdict])}</td>
      <td><div class="cell-title">“${esc(c.quote)}”</div><div class="cell-sub">${esc(c.note || c.basis)}</div></td>
      <td class="muted">${esc(c.said_by_name || (c.said_by === "official_story" ? "Account sheet" : c.said_by) || "")}</td>
      <td class="muted nowrap">${esc(c.account_name)}</td></tr>`).join("") || `<tr><td colspan="4" class="empty">Nothing matches.</td></tr>`}</tbody></table>`;
}

// ---------- page bindings ----------
function bindPage(r) {
  document.querySelectorAll("[data-href]").forEach((el) => el.addEventListener("click", (e) => { if (!e.target.closest("a,button.src")) location.hash = el.dataset.href; }));
  document.querySelectorAll("[data-deal]").forEach((el) => el.addEventListener("click", (e) => { if (!e.target.closest("a,button.src")) openDeal(el.dataset.deal); }));
  document.querySelectorAll("tr[data-ref]").forEach((el) => el.addEventListener("click", (e) => { if (!e.target.closest("a,button.src")) openRef(el.dataset.ref); }));
  document.querySelectorAll("[data-more]").forEach((el) => el.addEventListener("click", () => openMore(r, el.dataset.more)));
  if (r.page === "contacts") $("#q").addEventListener("input", async (e) => {
    const q = e.target.value.toLowerCase();
    const { contacts } = await get("/api/contacts");
    $("#list").innerHTML = contactsTable(contacts.filter((c) => JSON.stringify(c).toLowerCase().includes(q)), true);
    bindPage({ page: "_" });
  });
  if (r.page === "activities") $("#kind-seg").addEventListener("click", async (e) => {
    const b = e.target.closest("button"); if (!b) return;
    $("#kind-seg").querySelectorAll("button").forEach((x) => x.classList.toggle("on", x === b));
    const { activities } = await get("/api/activities");
    $("#list").innerHTML = activityList(activities.filter((x) => !b.dataset.kind || x.kind === b.dataset.kind).slice(0, 150), true);
  });
  if (r.page === "insights") {
    const draw = async () => {
      const { claims } = await get("/api/insights");
      const acct = $("#f-acct").value, v = $("#verdict-seg .on").dataset.verdict;
      $("#list").innerHTML = insightRows(claims.filter((c) => (!acct || c.account === acct) && (!v || c.verdict === v)));
      document.querySelectorAll("tr[data-claim]").forEach((el) => el.addEventListener("click", () => openClaim(el.dataset.claim)));
    };
    $("#f-acct").addEventListener("change", draw);
    $("#verdict-seg").addEventListener("click", (e) => { const b = e.target.closest("button"); if (!b) return; $("#verdict-seg").querySelectorAll("button").forEach((x) => x.classList.toggle("on", x === b)); draw(); });
    draw();
  }
}

// ---------- side panel ----------
const panelStack = [];
function panel(kicker, title, html, push = false) {
  if (!push) panelStack.length = 0;
  panelStack.push({ kicker, title, html });
  showPanel();
}
function showPanel() {
  const top = panelStack[panelStack.length - 1];
  $("#panel-kicker").textContent = top.kicker; $("#panel-title").textContent = top.title; $("#panel-body").innerHTML = top.html;
  $("#panel-back").hidden = panelStack.length < 2;
  $("#panel").classList.add("open"); $("#scrim").classList.add("open");
  $("#panel-body").querySelectorAll("[data-ref]").forEach((el) => el.addEventListener("click", () => openRef(el.dataset.ref, true)));
}
function closePanel() { $("#panel").classList.remove("open"); $("#scrim").classList.remove("open"); panelStack.length = 0; }
$("#panel-close").addEventListener("click", closePanel);
$("#scrim").addEventListener("click", closePanel);
$("#panel-back").addEventListener("click", () => { panelStack.pop(); showPanel(); });
document.addEventListener("keydown", (e) => { if (e.key === "Escape") closePanel(); });

function sourceButtons(refs) {
  return refs.map((ref) => `<button class="source-link" data-ref="${esc(ref)}"><span class="id">${esc(ref)}</span>${ICON_SRC}</button>`).join("");
}
document.addEventListener("click", (e) => {
  const b = e.target.closest("button.src");
  if (!b) return;
  e.preventDefault(); e.stopPropagation();
  const refs = sourceSets[+b.dataset.sources] || [];
  panel("Sources", plural(refs.length, "source"), `<p class="muted small" style="margin-top:0">Each item comes from these records. Open one to see it.</p>${sourceButtons(refs)}`);
});

async function openRef(ref, push = false) {
  try {
    const r = await api("/api/ref/" + encRef(ref));
    panel(r.type === "record" ? (r.kind || "record").replace("paperwork:", "") : r.type, ref, refBody(r), push);
  } catch (e) { panel("Not found", ref, empty("No record holds this id."), push); }
}
function refBody(r) {
  if (r.type === "metric") {
    const cols = [...new Set(r.rows.flatMap((x) => Object.keys(x.row || {})).filter((k) => typeof (r.rows[0]?.row || {})[k] !== "object"))].slice(0, 5);
    return `<p>${esc(r.definition)}</p><h4>Value</h4><pre class="doc">${esc(JSON.stringify(r.value, null, 2))}</pre>
      <h4>Rows counted (${r.rows.length})</h4>${r.rows.length ? `<table><thead><tr>${cols.map((c) => `<th>${esc(c)}</th>`).join("")}</tr></thead><tbody>
      ${r.rows.slice(0, 200).map((x) => `<tr>${cols.map((c) => `<td class="small">${esc((x.row || {})[c])}</td>`).join("")}</tr>`).join("")}</tbody></table>` : empty("No rows.")}
      <h4>Sources</h4>${sourceButtons(r.sources)}`;
  }
  if (r.type === "row") return `<dl class="kv">${Object.entries(r.row).map(([k, v]) => `<dt>${esc(k)}</dt><dd>${esc(v)}</dd>`).join("")}</dl><h4>File</h4>${sourceButtons([r.file])}`;
  const rec = r.record || {};
  const flat = Object.entries(rec).filter(([k, v]) => k !== "text" && (v === null || typeof v !== "object"));
  return `${r.label ? `<p class="muted" style="margin-top:0">${esc(r.label)}</p>` : ""}
    ${flat.length ? `<dl class="kv">${flat.map(([k, v]) => `<dt>${esc(k)}</dt><dd>${esc(v)}</dd>`).join("")}</dl>` : ""}
    ${typeof rec.text === "string" ? `<h4>Document</h4><pre class="doc">${esc(rec.text)}</pre>` : ""}
    ${rec.data ? `<h4>Data</h4><pre class="doc">${esc(JSON.stringify(rec.data, null, 2))}</pre>` : ""}
    <h4>Where it comes from</h4><p class="small muted">${esc(r.source)} · ${esc(r.location)}</p>`;
}

async function openDeal(id) {
  const { deals } = await get("/api/deals");
  const x = deals.find((d) => d.id === id);
  if (!x) return;
  panel("Deal", x.title, `
    <dl class="kv"><dt>Account</dt><dd>${esc(x.account_name)}</dd><dt>Stage</dt><dd>${stagePill(x.stage)}</dd>
      <dt>Worth</dt><dd>${money(x.worth, x.currency)}</dd><dt>Backing</dt><dd>${x.backing ? `${pill(x.backing, x.backing === "backed" ? "green" : "amber")} <span class="small muted">${esc(x.backing_why)}</span>` : "—"}</dd>
      ${x.blocker ? `<dt>Blocker</dt><dd>${pill(x.blocker.status, x.blocker.status === "open" ? "amber" : "green")} ${esc(x.blocker.text)}</dd>` : ""}
      ${x.deadline ? `<dt>Deadline</dt><dd>${esc(x.deadline)}</dd>` : ""}
      ${x.next_move ? `<dt>Next step</dt><dd>${esc(x.next_move.text)}<div class="small muted">${esc(owner(x.next_move.owner))} · due ${fmtDate(x.next_move.due)}</div></dd>` : ""}</dl>
    <h4>Who holds the power</h4><div class="roles">${x.roles.map((r) => `<span class="role-name">${r.role}</span>
      <span>${r.who ? `${esc(r.name)} ${stancePill(r.stance)}` : `<span class="faint">Unknown${r.ask ? ` · ask ${esc(r.ask.split("/")[1].replace(/_15x$/, "").replace(/_/g, " "))}` : ""}</span>`}${r.note ? `<div class="small faint">${esc(r.note)}</div>` : ""}</span>`).join("")}</div>
    <div class="callout small" style="margin-top:10px">Roles are analyst judgment, each backed by the records below.</div>
    <h4>Evidence</h4>${sourceButtons(x.evidence)}`);
}

async function openClaim(id) {
  const { claims } = await get("/api/insights");
  const c = claims.find((x) => x.claim_id === id);
  if (!c) return;
  const tests = (c.tests || []).map((t) => `<div class="list-item"><span class="dot ${t.passed === null ? "grey" : t.passed ? "green" : "red"}"></span><div class="list-main small">
      <div>${esc(t.metric)}${t.field ? "." + esc(t.field) : ""} ${esc(t.op)} ${esc(JSON.stringify(t.value))}</div><div class="faint">actual: ${esc(String(JSON.stringify(t.actual)).slice(0, 120))}</div></div></div>`).join("");
  panel("Claim", c.account_name, `
    <p class="quote">“${esc(c.quote)}”</p>
    <dl class="kv"><dt>Verdict</dt><dd>${pill(c.verdict, VERDICT[c.verdict])}</dd><dt>Said by</dt><dd>${esc(c.said_by_name || (c.said_by === "official_story" ? "Account sheet (official story)" : c.said_by) || "")}</dd>
      <dt>Type</dt><dd>${esc(c.type)}</dd><dt>How we know</dt><dd>${esc(c.basis)}</dd>${c.note ? `<dt>Note</dt><dd>${esc(c.note)}</dd>` : ""}</dl>
    ${tests ? `<h4>Checks against the data</h4>${tests}` : ""}
    ${c.evidence_against.length ? `<h4>Evidence against</h4>${sourceButtons(c.evidence_against)}` : ""}
    ${c.evidence_for.length ? `<h4>Evidence for</h4>${sourceButtons(c.evidence_for)}` : ""}
    ${c.possible_causes.length ? `<h4>Possible causes ${pill("inference", "violet")}</h4>${c.possible_causes.map((p) => `<div class="callout small" style="margin-bottom:6px">${esc(p.text)}</div>`).join("")}` : ""}
    ${c.ask ? `<h4>Who could settle it</h4><div class="small">${esc(c.ask.question)}</div><div class="small faint">${esc(owner(c.ask.who))}</div>` : ""}
    <h4>Said in</h4>${sourceButtons(c.source_ids)}`);
}

async function openMore(r, what) {
  const d = await get(`/api/accounts/${encodeURIComponent(r.id)}`);
  if (what === "risks") panel("Risks", d.account.name, d.answers.q4.risks.map((x) => riskItem(x, false)).join(""));
  if (what === "gaps") panel("Open questions", d.account.name, d.answers.q5.gaps.map((g) => `<div class="list-item"><span class="dot grey"></span><div class="list-main">
    <div class="list-title">${esc(g.text)}</div><div class="list-meta">${g.who ? `<span>Ask ${esc(nameOf(d, g.who))}</span>` : ""}${g.status === "inference" ? pill("inference", "violet") : ""}${sources(g.sources)}</div></div></div>`).join(""));
}

// ---------- sync + live polling ----------
$("#sync-btn").addEventListener("click", async () => {
  try {
    if (liveMode) {
      const r = await api("/api/sync", { method: "POST" });
      toast(`Checking for new data at ${new Date(r.next_call_at).toLocaleTimeString()}`);
      return;
    }
    const r = await api("/api/reload", { method: "POST" });
    toast(r.snapshots_added ? `Loaded ${plural(r.snapshots_added, "update")}` : "Already up to date");
    for (const k in cache) delete cache[k];
    render();
  } catch (e) { toast(e.body?.detail || "Sync failed"); }
});
setInterval(async () => {
  try {
    const h = await api("/api/health");
    if (rebuiltAt && h.rebuilt_at !== rebuiltAt) {
      rebuiltAt = h.rebuilt_at;
      for (const k in cache) delete cache[k];
      toast("New data arrived");
      render();
    } else $("#sync").textContent = `Synced ${ago(h.last_sync)}`;
  } catch (e) { /* keep the current view */ }
}, 15000);
window.addEventListener("hashchange", () => { closePanel(); window.scrollTo(0, 0); render(); });
render();

"""Deterministic metrics per account, computed from paperwork. Pure logic, no I/O.

Each metric: {name, account, value, as_of, definition, sources: [ids], rows: [row refs]}.
A row ref is either an indexed id (`A/FB-10412`, `A/U02`) or a CSV row `<account>/<path>#r<n>`
(1-based, in merged order: the original file's rows, then rows added by signals).
"Now" is the in-game date. Every threshold comes from config/rules.yaml.
"""

import re
from collections import defaultdict
from datetime import date, timedelta

from app.world import csv_rows

MONTHS = {m: i for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}
LONG_DATE = re.compile(r"\b(\d{1,2}) (January|February|March|April|May|June|July|August|September|October|November|December) (\d{4})\b")


def d(s):
    """ISO date string (or datetime prefix) -> date, else None."""
    try:
        return date.fromisoformat(str(s)[:10])
    except (TypeError, ValueError):
        return None


def long_date(text):
    m = LONG_DATE.search(text or "")
    if not m:
        return None
    return date(int(m.group(3)), MONTHS[m.group(2)[:3].lower()], int(m.group(1)))


class Paperwork:
    """Lookup helpers over one account's merged paperwork."""

    def __init__(self, world, account):
        self.account = account
        self.records = {}
        for r in world["paperwork"].get(account, []):
            self.records.setdefault(r["path"], r)

    def key(self, path):
        return f"{self.account}/{path}"

    def get(self, path):
        return self.records.get(path)

    def data(self, path):
        r = self.get(path)
        return r.get("data") if r and isinstance(r.get("data"), dict) else {}

    def rows(self, path):
        """[(row_ref, row, source)] for a CSV record."""
        r = self.get(path)
        if not r or not isinstance(r.get("text"), str):
            return []
        rows = r.get("rows") or [{"row": row, "source": r["source"]} for row in csv_rows(r["text"])]
        return [(f"{self.account}/{path}#r{i + 1}", x["row"], x["source"]) for i, x in enumerate(rows)]

    def of_kind(self, kind):
        return [r for r in self.records.values() if r.get("kind") == kind]


def metric(account, name, value, as_of, definition, sources, rows=()):
    return {"name": name, "account": account, "value": value, "as_of": as_of, "definition": definition,
            "sources": sorted(set(sources)), "rows": list(dict.fromkeys(rows))}


def compute(world, account, as_of, rules):
    """All metrics for one account, as a list. Sections with no data are simply absent."""
    now = d(as_of) or date.today()
    pw = Paperwork(world, account)
    out = []
    out += dock_metrics(pw, account, now, as_of, rules["metrics"])
    out += force_metrics(world, account, as_of)
    out += user_metrics(pw, account, as_of)
    out += ticket_metrics(pw, account, now, as_of, rules)
    out += invoice_metrics(pw, account, now, as_of, rules["metrics"])
    out += contract_metrics(pw, account, as_of)
    out += contact_metrics(world, pw, account, now, as_of, rules)
    return out


# ---------- docks ----------

def contracted_docks(pw):
    r = pw.get("contract.txt")
    m = re.search(r"(\d+) docks? with aircraft", (r or {}).get("text") or "")
    return (int(m.group(1)), pw.key("contract.txt")) if m else (None, None)


def dock_metrics(pw, A, now, as_of, cfg):
    out = []
    contracted, contract_key = contracted_docks(pw)
    if contracted is not None:
        out.append(metric(A, "docks.contracted", contracted, as_of, "Docks ordered, from the contract ('N docks with aircraft').", [contract_key]))

    docks = pw.data("devices.json").get("docks", [])
    dock_ids = [x["dock_id"] for x in docks if x.get("dock_id")]
    if docks:
        refs = [f"{A}/{i}" for i in dock_ids]
        out.append(metric(A, "docks.registered", len(dock_ids), as_of, "Docks registered to the account (devices.json).", [pw.key("devices.json")], refs))
        uncommissioned = [f"{A}/{x['dock_id']}" for x in docks if "commissioned" in x and not x.get("commissioned")]
        out.append(metric(A, "docks.not_commissioned", uncommissioned, as_of,
                          "Registered docks with no commissioning flight on record (devices.json `commissioned` empty).",
                          [pw.key("devices.json")], uncommissioned))

    usage = pw.rows("usage_weekly.csv")
    if not usage:
        return out
    usage_key = pw.key("usage_weekly.csv")
    usage_sources = {usage_key} | {src for _, _, src in usage if "/" in src}  # signals that added rows
    weeks = sorted({row["week_start"] for _, row, _ in usage if d(row.get("week_start"))})
    through = d(weeks[-1]) + timedelta(days=6)
    all_docks = sorted(set(dock_ids) | {row["dock_id"] for _, row, _ in usage})
    out.append(metric(A, "usage.data_through", through.isoformat(), as_of,
                      "Last day covered by weekly usage (latest week_start + 6). Usage windows end here, not at today.", sorted(usage_sources)))
    out.append(metric(A, "usage.data_age_days", (now - through).days, as_of,
                      f"Days between in-game today and the end of the usage data; stale above {cfg['stale_usage_days']}.", sorted(usage_sources)))

    def window(n_weeks):
        wanted = set(weeks[-n_weeks:])
        return [(ref, row) for ref, row, _ in usage if row["week_start"] in wanted]

    flights = lambda rows: defaultdict(int, _sum_by(rows, "flights"))
    for days in cfg["windows_days"]:
        rows = window(max(1, round(days / 7)))
        per = flights(rows)
        flew = sorted(k for k, v in per.items() if v > 0)
        out.append(metric(A, f"docks.flew_{days}d", len(flew), as_of,
                          f"Docks with at least one flight in the last {max(1, round(days / 7))} usage week(s) up to {through}.",
                          sorted(usage_sources), [ref for ref, row in rows if int(row["flights"] or 0) > 0]))

    last_week = window(1)
    online = sorted({row["dock_id"] for _, row in last_week if int(row["days_online"] or 0) > 0})
    out.append(metric(A, "docks.online_last_week", len(online), as_of,
                      f"Docks online at least one day in the week of {weeks[-1]}.", sorted(usage_sources),
                      [ref for ref, row in last_week if int(row["days_online"] or 0) > 0]))

    w30 = window(4)
    per30, on30 = flights(w30), _sum_by(w30, "days_online")
    idle = sorted(k for k in all_docks if per30.get(k, 0) == 0 and on30.get(k, 0) > 0)
    out.append(metric(A, "docks.idle_but_online", [f"{A}/{k}" for k in idle], as_of,
                      "Docks online in the last 4 usage weeks with zero flights in them.", sorted(usage_sources),
                      [ref for ref, row in w30 if row["dock_id"] in idle]))

    ever = _sum_by([(ref, row) for ref, row, _ in usage], "flights")
    for ref, row, _ in pw.rows("flights.csv"):
        ever[row["dock_id"]] = ever.get(row["dock_id"], 0) + 1
    never = sorted(k for k in all_docks if ever.get(k, 0) == 0)
    out.append(metric(A, "docks.never_flown", [f"{A}/{k}" for k in never], as_of,
                      "Docks with no flight in any usage week or flights.csv row.", sorted(usage_sources | {pw.key("flights.csv")} if pw.get("flights.csv") else usage_sources),
                      [ref for ref, row, _ in usage if row["dock_id"] in never]))

    n = cfg["regular_patrol_weeks"]
    wr = window(n)
    per_r = flights(wr)
    regular = sorted(k for k in all_docks if per_r.get(k, 0) / n >= cfg["regular_patrol_per_week"])
    out.append(metric(A, "docks.regular_patrol", [f"{A}/{k}" for k in regular], as_of,
                      f"Docks averaging >= {cfg['regular_patrol_per_week']} flights a week over the last {n} usage weeks.",
                      sorted(usage_sources), [ref for ref, row in wr if row["dock_id"] in regular]))

    s = cfg["stopped_weeks"]
    recent, before = set(weeks[-s:]), set(weeks[-(s + n):-s])
    per_recent = _sum_by([(ref, row) for ref, row, _ in usage if row["week_start"] in recent], "flights")
    per_before = _sum_by([(ref, row) for ref, row, _ in usage if row["week_start"] in before], "flights")
    stopped = sorted(k for k in all_docks if per_recent.get(k, 0) == 0 and per_before.get(k, 0) / max(len(before), 1) >= cfg["regular_patrol_per_week"])
    out.append(metric(A, "docks.stopped_flying", [f"{A}/{k}" for k in stopped], as_of,
                      f"Docks that patrolled regularly (>= {cfg['regular_patrol_per_week']}/week) in the {len(before)} weeks before, "
                      f"and flew nothing in the last {s} usage weeks.", sorted(usage_sources),
                      [ref for ref, row, _ in usage if row["dock_id"] in stopped and row["week_start"] in recent | before]))

    t = cfg["trend_weeks"]
    last, prev = set(weeks[-t:]), set(weeks[-2 * t:-t])
    last_n = sum(int(row["flights"] or 0) for _, row, _ in usage if row["week_start"] in last)
    prev_n = sum(int(row["flights"] or 0) for _, row, _ in usage if row["week_start"] in prev)
    change = round((last_n - prev_n) / prev_n * 100) if prev_n else None
    out.append(metric(A, "usage.trend", {"last_weeks": last_n, "previous_weeks": prev_n, "change_pct": change}, as_of,
                      f"Flights in the last {t} usage weeks vs the {t} before.", sorted(usage_sources),
                      [ref for ref, row, _ in usage if row["week_start"] in last | prev]))

    if contracted:
        flew30 = len([k for k, v in per30.items() if v > 0])
        out.append(metric(A, "docks.utilisation_30d", round(flew30 / contracted, 2), as_of,
                          "Docks that flew in the last 4 usage weeks / contracted docks.", sorted(usage_sources | {contract_key}),
                          [ref for ref, row in w30 if int(row["flights"] or 0) > 0]))

    events = pw.rows("device_events.csv")
    problem = set(cfg["problem_events"])
    by_dock = defaultdict(list)
    for ref, row, _ in events:
        if row.get("event") in problem:
            by_dock[row["dock_id"]].append((ref, row["event"]))
    if events:
        value = {f"{A}/{k}": dict(_count(e for _, e in v)) for k, v in sorted(by_dock.items())}
        out.append(metric(A, "docks.problem_events", value, as_of,
                          f"Device events per dock of types {sorted(problem)}.", [pw.key("device_events.csv")],
                          [ref for v in by_dock.values() for ref, _ in v]))
        battery = sorted(k for k, v in by_dock.items() if any(e == "battery_health_warning" for _, e in v))
        out.append(metric(A, "docks.battery_warning_docks", [f"{A}/{k}" for k in battery], as_of,
                          "Docks with at least one battery_health_warning event.", [pw.key("device_events.csv")],
                          [ref for k in battery for ref, e in by_dock[k] if e == "battery_health_warning"]))
    return out


def _sum_by(rows, field):
    out = defaultdict(int)
    for _, row in rows:
        out[row["dock_id"]] += int(row.get(field) or 0)
    return dict(out)


def _count(items):
    out = defaultdict(int)
    for i in items:
        out[i] += 1
    return out


# ---------- forces / sites (from a file with force + month columns) ----------

FORCE_SUFFIXES = re.compile(r"\b(police|constabulary)\b", re.I)
FORCE_ABBREV = {"ctrl": "central", "vly": "valley"}


def norm_force(name):
    words = FORCE_SUFFIXES.sub("", name or "").lower().split()
    return " ".join(FORCE_ABBREV.get(w, w) for w in words).title()


def norm_month(text):
    """'05/2026', '2026-05', 'May 2026', 'Aug-26', 'Sep-26' -> '2026-05'."""
    t = (text or "").strip()
    m = re.fullmatch(r"(\d{1,2})/(\d{4})", t) or None
    if m:
        return f"{m.group(2)}-{int(m.group(1)):02d}"
    m = re.fullmatch(r"(\d{4})-(\d{2})", t)
    if m:
        return t
    m = re.fullmatch(r"([A-Za-z]{3})[a-z]*[ -](\d{2,4})", t)
    if m and m.group(1).lower() in MONTHS:
        year = int(m.group(2)) + (2000 if len(m.group(2)) == 2 else 0)
        return f"{year}-{MONTHS[m.group(1).lower()]:02d}"
    return None


def force_metrics(world, A, as_of):
    """Per-force usage from an account file with columns force, month, docks_installed, docks_with_live_flights."""
    out = []
    for f in world["files"]:
        content = f.get("content")
        text = content.get("text") if isinstance(content, dict) else None
        if f.get("account") != A or not isinstance(text, str) or not text.startswith("force,month"):
            continue
        key = f"{A}/{f['id']}"
        latest = {}  # force -> (month, row_no, row); first clean row per (force, month) wins
        for i, row in enumerate(csv_rows(text), 1):
            force, month = norm_force(row.get("force")), norm_month(row.get("month"))
            if not force or not month or not (row.get("live_missions") or "").strip():
                continue  # a "re-sent" row with blank counts is dropped
            if force not in latest or month > latest[force][0]:
                latest[force] = (month, i, row)
        if not latest:
            continue
        per_force = {
            force: {"month": m, "docks_installed": int(r["docks_installed"]), "docks_with_live_flights": int(r["docks_with_live_flights"]),
                    "live_missions": int(r["live_missions"]), "live_share": round(int(r["docks_with_live_flights"]) / int(r["docks_installed"]), 2)}
            for force, (m, i, r) in sorted(latest.items())
        }
        rows = [f"{key}#r{i}" for _, i, _ in latest.values()]
        out.append(metric(A, "forces.latest_month", per_force, as_of,
                          f"Latest month per force in {f['id']}, after normalising force names and month formats and dropping blank re-sent rows.",
                          [key], rows))
        live = sorted(k for k, v in per_force.items() if v["live_missions"] > 0)
        out.append(metric(A, "forces.with_live_missions", len(live), as_of,
                          f"Forces with at least one live mission in their latest month ({', '.join(live)}).", [key], rows))
        for force, v in per_force.items():
            out.append(metric(A, f"forces.{force}.live_share", v["live_share"], as_of,
                              f"{force}: docks with live flights / docks installed, {v['month']}.", [key],
                              [f"{key}#r{latest[force][1]}"]))
    return out + site_metrics(world, A, as_of)


def site_metrics(world, A, as_of):
    """Per-site usage from a file with columns dock, site, month, patrol_flights (one row per dock per month)."""
    out = []
    for f in world["files"]:
        content = f.get("content")
        text = content.get("text") if isinstance(content, dict) else None
        if f.get("account") != A or not isinstance(text, str) or not text.startswith("dock,site,month"):
            continue
        key = f"{A}/{f['id']}"
        rows = [(i, r, norm_month(r.get("month"))) for i, r in enumerate(csv_rows(text), 1)]
        last = max((m for _, _, m in rows if m), default=None)
        latest = [(i, r) for i, r, m in rows if m == last]
        per_site = {}
        for i, r in latest:
            s = per_site.setdefault(r["site"], {"month": last, "docks": [], "docks_patrolling": [], "patrol_flights": 0, "rows": []})
            s["docks"].append(r["dock"])
            s["patrol_flights"] += int(r.get("patrol_flights") or 0)
            if int(r.get("patrol_flights") or 0) > 0:
                s["docks_patrolling"].append(r["dock"])
            s["rows"].append(f"{key}#r{i}")
        value = {k: {**{x: y for x, y in v.items() if x != "rows"}, "patrol_share": round(len(v["docks_patrolling"]) / len(v["docks"]), 2)}
                 for k, v in sorted(per_site.items())}
        refs = [f"{key}#r{i}" for i, _ in latest]
        out.append(metric(A, "sites.latest_month", value, as_of, f"Patrol flights per site in {last}, from {f['id']}.", [key], refs))
        live = sorted(k for k, v in per_site.items() if v["patrol_flights"] > 0)
        out.append(metric(A, "sites.with_patrols", len(live), as_of, f"Sites with any patrol flight in {last} ({', '.join(live)}).", [key], refs))
        for site, v in per_site.items():
            out.append(metric(A, f"sites.{site}.patrol_flights", v["patrol_flights"], as_of, f"{site}: patrol flights in {last}.", [key], v["rows"]))
    return out


# ---------- users ----------

def user_metrics(pw, A, as_of):
    users = pw.data("users.json").get("users", [])
    if not users:
        return []
    src = [pw.key("users.json")]
    active = [f"{A}/{u['user_id']}" for u in users if (u.get("sign_ins_last_90_days") or 0) > 0]
    never = [f"{A}/{u['user_id']}" for u in users if not u.get("last_sign_in")]
    named = {f"{A}/{u['user_id']}": {"name": u["name"], "sign_ins_last_90_days": u.get("sign_ins_last_90_days"), "last_sign_in": u.get("last_sign_in")}
             for u in users if u.get("name")}
    return [
        metric(A, "users.listed", len(users), as_of, "Logins listed on the account (users.json).", src, [f"{A}/{u['user_id']}" for u in users]),
        metric(A, "users.active_90d", len(active), as_of, "Logins with at least one sign-in in the last 90 days.", src, active),
        metric(A, "users.never_signed_in", never, as_of, "Logins with no sign-in on record.", src, never),
        metric(A, "users.named_activity", named, as_of, "Sign-ins of the logins named on file (contacts).", src, list(named)),
    ]


# ---------- tickets and requests ----------

def ticket_metrics(pw, A, now, as_of, rules):
    out = []
    tickets = pw.data("tickets.json").get("tickets", [])
    if tickets:
        src = [pw.key("tickets.json")]
        open_t = [t for t in tickets if (t.get("status") or "").lower() != "closed"]
        ages = {f"{A}/{t['id']}": (now - d(t["opened"])).days for t in open_t if d(t.get("opened"))}
        unowned = [f"{A}/{t['id']}" for t in open_t if not t.get("assignee")]
        chased = [f"{A}/{t['id']}" for t in tickets if any("chase" in (c.get("text") or "").lower() for c in t.get("conversation", []))]
        out += [
            metric(A, "tickets.open", ages, as_of, "Open tickets and their age in days (status not Closed).", src, list(ages)),
            metric(A, "tickets.open_unowned", unowned, as_of, "Open tickets with no assignee.", src, unowned),
            metric(A, "tickets.chased", chased, as_of, "Tickets where the requester chased.", src, chased),
        ]
    requests = pw.rows("requests.csv")
    if requests:
        srcs = sorted({pw.key("requests.csv")} | {s for _, _, s in requests if "/" in s})
        open_r = [(ref, row) for ref, row, _ in requests if (row.get("status") or "").lower() == "open"]
        waiting = {ref: (now - d(row["opened"])).days for ref, row in open_r if not (row.get("owner") or "").strip() and d(row.get("opened"))}
        urgent = [ref for ref, row in open_r if "urgent" in (row.get("summary") or "").lower()]
        out += [
            metric(A, "requests.open_unowned", waiting, as_of, "Open requests (requests.csv, incl. rows added by signals) with no owner, and days since opened.", srcs, list(waiting)),
            metric(A, "requests.urgent_open", urgent, as_of, "Open requests marked urgent.", srcs, urgent),
        ]
    return out


# ---------- invoices ----------

def invoice_metrics(pw, A, now, as_of, cfg):
    inv = pw.data("invoices.json").get("invoices", [])
    if not inv:
        return []
    src = [pw.key("invoices.json")]
    refs = [f"{A}/{i['number']}" for i in inv]
    unpaid = [i for i in inv if (i.get("status") or "").lower() != "paid"]
    overdue = [f"{A}/{i['number']}" for i in unpaid if d(i.get("issued")) and (now - d(i["issued"])).days > cfg["invoice_due_days"]]
    missing = [f"{A}/{i['number']}" for i in inv if i.get("amount") is None]
    return [
        metric(A, "invoices.count", len(inv), as_of, "Invoices on file.", src, refs),
        metric(A, "invoices.unpaid", [f"{A}/{i['number']}" for i in unpaid], as_of, "Invoices not marked Paid.", src, [f"{A}/{i['number']}" for i in unpaid]),
        metric(A, "invoices.overdue", overdue, as_of, f"Unpaid invoices issued more than {cfg['invoice_due_days']} days before today.", src, overdue),
        metric(A, "invoices.amount_known", sum(i["amount"] for i in inv if isinstance(i.get("amount"), (int, float))), as_of,
               "Sum of invoice amounts that are stated.", src, refs),
        metric(A, "invoices.amount_missing", missing, as_of, "Invoices whose amount is not stated (e.g. held in a schedule we don't have).", src, missing),
    ]


# ---------- contract ----------

def contract_metrics(pw, A, as_of):
    r = pw.get("contract.txt")
    profile = pw.data("profile.json")
    out = []
    text = (r or {}).get("text") or ""
    src = [pw.key("contract.txt")] if r else []
    term = re.search(r"Term: (.+?) to (.+?)\.", text)
    start, end = (long_date(term.group(1)), long_date(term.group(2))) if term else (None, None)
    renewal = profile.get("renewal") or {}
    if end or renewal.get("term_ends"):
        sources = src + ([pw.key("profile.json")] if renewal.get("term_ends") else [])
        out.append(metric(A, "contract.term", {"start": start.isoformat() if start else None,
                                               "end": (end.isoformat() if end else renewal.get("term_ends"))}, as_of,
                          "Contract term ('Term: <start> to <end>'), cross-checked with profile.json renewal.term_ends.", sources))
    price = re.search(r"([\d,]{4,}) a year", text)
    if price or renewal.get("annual_value"):
        value = int(price.group(1).replace(",", "")) if price else renewal.get("annual_value")
        out.append(metric(A, "contract.annual_value", value, as_of, "Annual price from the contract ('N a year'), else profile.json renewal.annual_value.",
                          src + ([pw.key("profile.json")] if renewal.get("annual_value") else [])))
    if r:
        out.append(metric(A, "contract.auto_renews", "no automatic renewal" not in text.lower(), as_of, "False when the contract says 'No automatic renewal'.", src))
        signed = re.search(r"Signed: (.+)", text) or re.search(r"Order placed: (.+)", text)
        if signed:
            out.append(metric(A, "contract.signed", signed.group(1).strip(), as_of, "Who signed / how the order was placed, from the contract.", src))
    contracted, _ = contracted_docks(pw)
    if contracted is not None:
        out.append(metric(A, "contract.docks", contracted, as_of, "Docks in the contract.", src))
    return out


# ---------- contact ----------

FROM_RE = re.compile(r"^From: (.+)$", re.M)
DATE_RE = re.compile(r"^Date: (.+)$", re.M)


def people_of(world, A):
    acct = next((a for a in world["accounts"] if a["id"] == A), {})
    people = {f"{A}/{s['id']}": s["name"] for s in acct.get("stakeholders", [])}
    for r in world["paperwork"].get(A, []):
        if r.get("kind") == "profile" and isinstance(r.get("data"), dict):
            for c in r["data"].get("contacts_on_file", []):
                people.setdefault(f"{A}/{c['id']}", c.get("name"))
    return {k: v for k, v in people.items() if v}


def contact_metrics(world, pw, A, now, as_of, rules):
    """Last contact per person, and customer messages nobody at FlytBase answered."""
    markers = [m.lower() for m in rules["contact"]["no_reply_markers"]]
    people = people_of(world, A)
    events = []  # (date, person_key or None, direction in|out|call, ref, unanswered: bool, sender text)

    def who(text):
        return next((k for k, name in people.items() if name and name.lower() in (text or "").lower()), None)

    team_names = [p.get("name", "").lower() for p in world["team"] if p.get("name")]

    def ours(sender):
        """A person on the FlytBase side (not an automatic reply)."""
        s = sender.lower()
        if "automatic" in s:
            return False
        return "flytbase" in s or "support desk" in s or any(re.search(rf"\b{re.escape(n)}\b", s) for n in team_names)

    for r in pw.of_kind("email"):
        blocks = [(FROM_RE.search(b), DATE_RE.search(b), b) for b in re.split(r"\n---\n", r.get("text") or "")]
        blocks = [(f.group(1), long_date(dt.group(1)) if dt else None, b) for f, dt, b in blocks if f]
        for i, (sender, day, block) in enumerate(blocks):
            if "automatic" in sender.lower():
                continue
            if ours(sender):
                to = re.search(r"^To: (.+)$", block, re.M)
                events.append((day, who(to.group(1) if to else ""), "out", pw.key(r["path"]), False, sender))
            else:
                replied_in_thread = any(ours(s) for s, _, _ in blocks[i + 1:])
                marked = any(m in block.lower() for m in markers)
                events.append((day, who(sender), "in", pw.key(r["path"]), marked or not replied_in_thread, sender))

    for m in pw.data("mail_log.json").get("mail", []):
        if m.get("kind") != "person" or m.get("path"):
            continue  # automatic mail is not contact; mails with a path were parsed from emails/ above
        sender, ref = m.get("from") or "", f"{A}/{m['thread']}" if m.get("thread") else pw.key("mail_log.json")
        if ours(sender):
            events.append((d(m.get("date")), who(m.get("to")), "out", ref, False, sender))
        else:
            marked = any(mk in (m.get("note") or "").lower() for mk in markers)
            events.append((d(m.get("date")), who(sender), "in", ref, marked, sender))

    for c in pw.data("calls_index.json").get("calls", []):
        ref = pw.key(c["path"]) if c.get("path") else pw.key("calls_index.json")
        for name in c.get("customer", []):
            events.append((d(c.get("date")), who(name), "call", ref, False, name))

    for t in pw.data("tickets.json").get("tickets", []):
        events.append((d(t.get("opened")), who(t.get("requester")), "in", f"{A}/{t['id']}", False, t.get("requester")))

    for ref, row, _ in pw.rows("requests.csv"):
        if (row.get("status") or "").lower() == "open" and not (row.get("owner") or "").strip():
            events.append((d(row.get("opened")), who(row.get("from")), "in", ref, True, row.get("from")))

    # An inbound message counts as answered by any later call or email from us to that person,
    # unless the record itself says nobody replied.
    per_person = {}
    unanswered = []
    for day, person, kind, ref, flagged, sender in sorted(events, key=lambda e: (e[0] or date.min)):
        if person:
            p = per_person.setdefault(person, {"last_inbound": None, "last_outbound": None, "last_call": None, "refs": []})
            field = {"in": "last_inbound", "out": "last_outbound", "call": "last_call"}[kind]
            if day and (p[field] is None or day.isoformat() > p[field]):
                p[field] = day.isoformat()
            p["refs"].append(ref)
        if kind == "in" and flagged and day:
            answered_later = person and any(e[1] == person and e[2] in ("out", "call") and e[0] and e[0] > day for e in events)
            text_flag = any(m in _record_text(pw, ref).lower() for m in markers)
            if not answered_later or text_flag:
                # The same message often appears twice (the email and its requests.csv row): merge them.
                group = (person or " ".join((sender or "").lower().split()[:2]), day.isoformat())
                same = next((u for u in unanswered if u["_group"] == group), None)
                if same:
                    same["refs"].append(ref)
                else:
                    unanswered.append({"_group": group, "ref": ref, "refs": [ref], "person": person, "from": sender,
                                       "date": day.isoformat(), "days_waiting": (now - day).days})
    for u in unanswered:
        u.pop("_group")

    out = []
    srcs = sorted({e[3].split("#")[0] for e in events})
    for person, p in sorted(per_person.items()):
        last_two_way = max([x for x in (p["last_call"], p["last_outbound"]) if x], default=None)
        p = {**p, "last_contact": max([x for x in (p["last_inbound"], p["last_outbound"], p["last_call"]) if x], default=None),
             "days_since_two_way": (now - d(last_two_way)).days if last_two_way else None, "refs": sorted(set(p["refs"]))}
        out.append(metric(A, f"contact.{person.split('/', 1)[1]}", p, as_of,
                          "Last inbound (their email/ticket), outbound (our email) and call with this person; days since our last call or email to them.",
                          sorted({r.split("#")[0] for r in p["refs"]}), p["refs"]))
    for person in sorted(set(people) - set(per_person)):
        out.append(metric(A, f"contact.{person.split('/', 1)[1]}", {"last_contact": None, "days_since_two_way": None, "refs": []}, as_of,
                          "No call, email or ticket with this person in the paperwork.", [person]))
    out.append(metric(A, "contact.unanswered", sorted(unanswered, key=lambda u: -u["days_waiting"]), as_of,
                      "Customer messages with no reply from FlytBase: marked 'no reply' in the record, or with no later call or email from us to that person.",
                      srcs, [r for u in unanswered for r in u["refs"]]))
    return out


def _record_text(pw, ref):
    path = ref.split("/", 1)[1].split("#")[0] if "/" in ref else ""
    r = pw.get(path)
    return (r or {}).get("text") or ""

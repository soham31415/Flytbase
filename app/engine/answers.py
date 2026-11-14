"""The five answers per account, built on the metrics and the claim verdicts. Pure logic.

Every item: {id, text, value, status, severity?, due?, owner?, deal_id?, sources: [ids], why}.
status is verified | told | claim | derived | inference | analyst judgment. Items citing an
unknown id are dropped by app/engine/validate.py.
"""

import hashlib
import re
from datetime import date, timedelta
from pathlib import Path

import yaml

from app.engine.metrics import Paperwork, d, people_of

MONTHS = ["january", "february", "march", "april", "may", "june", "july", "august", "september", "october", "november", "december"]
ROLES = ["decider", "payer", "user", "blocker", "champion"]


def item_id(*parts):
    return hashlib.sha1("|".join(str(p) for p in parts).encode()).hexdigest()[:10]


def load_roles(analysis_dir):
    path = Path(analysis_dir) / "roles.yaml"
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {} if path.exists() else {}


class Ctx:
    def __init__(self, world, A, acct, engine_acct, as_of, rules, roles, anchor):
        self.world, self.A, self.acct, self.rules, self.anchor = world, A, acct, rules, anchor
        self.now = d(as_of)
        self.M = {m["name"]: m for m in engine_acct["metrics"]}
        self.claims = engine_acct["claims"]
        self.roles = (roles or {}).get(A, {})
        self.cfg = rules["answers"]
        self.people = people_of(world, A)
        self.pw = Paperwork(world, A)
        self.currency = self.pw.data("profile.json").get("currency")

    def mv(self, name, default=None):
        return self.M[name]["value"] if name in self.M else default

    def mref(self, name):
        return f"{self.A}/metric/{name}"

    def name(self, key):
        return self.people.get(key) or key

    def days(self, day):
        return (day - self.now).days if day else None


# ---------- dates ----------

def minute_date(ctx, minute):
    """Deadline minutes count from the moment a drop account's sheet first arrived (its anchor date)."""
    if minute is None or not ctx.anchor:
        return None
    return d(ctx.anchor) + timedelta(days=round(minute / 1440))


def month_after(text, after):
    """'in January' after 2026-11-17 -> 2027-01-01."""
    m = re.search(r"\b(" + "|".join(MONTHS) + r")\b", (text or "").lower())
    if not m or not after:
        return None
    month = MONTHS.index(m.group(1)) + 1
    year = after.year + (1 if month < after.month else 0)
    return date(year, month, 1)


def deadline_dates(ctx):
    """Every dated deadline on the account: opportunity deadlines, deadlines[] and leaving people from signals."""
    out = []
    opps = {(o.get("deadline") or {}).get("text"): o for o in ctx.acct.get("opportunities", []) if o.get("deadline")}
    for x in ctx.acct.get("deadlines", []):
        o = opps.get(x.get("text"))
        ref = f"{ctx.A}/{o['id']}#deadline" if o else None
        out.append({"text": x.get("text"), "date": minute_date(ctx, x.get("minute")), "passed": x.get("passed"),
                    "deal": f"{ctx.A}/{o['id']}" if o else None, "sources": [ref] if ref else [f"{ctx.A}/{ctx.A}"],
                    "derived_from_minute": x.get("minute")})
    for text, o in opps.items():
        if not any(x["text"] == text for x in out):
            out.append({"text": text, "date": minute_date(ctx, o["deadline"].get("minute")), "passed": o["deadline"].get("passed"),
                        "deal": f"{ctx.A}/{o['id']}", "sources": [f"{ctx.A}/{o['id']}#deadline"], "derived_from_minute": o["deadline"].get("minute")})
    for s in ctx.world["signals"]:
        if s.get("account") == ctx.A and re.search(r"\b(leav|moving on|moves on)", (s.get("title", "") + s.get("summary", "")).lower()):
            when = month_after(s.get("summary"), d(s.get("dated")))
            who = next((k for k, n in ctx.people.items() if n and n in s.get("summary", "") + s.get("title", "")), None)
            out.append({"text": s.get("title"), "date": when, "passed": False, "deal": None, "leaving": True, "who": who,
                        "sources": [f"{ctx.A}/{s['id']}"] + ([who] if who else []), "derived_from_minute": None})
    term = ctx.mv("contract.term") or {}
    if term.get("end"):
        out.append({"text": "Contract term ends (no automatic renewal)", "date": d(term["end"]), "passed": False,
                    "deal": renewal_deal(ctx), "sources": [ctx.mref("contract.term")], "renewal": True})
    return out


def renewal_deal(ctx):
    o = next((o for o in ctx.acct.get("opportunities", []) if "renew" in (o.get("title") or "").lower()), None)
    return f"{ctx.A}/{o['id']}" if o else None


# ---------- Q1: the book ----------

def q1_book(ctx):
    deals, offers = [], []
    facts = {f["id"]: f for f in ctx.acct.get("facts", [])}
    file_ids = {f["id"] for f in ctx.world["files"] if f.get("account") == ctx.A}
    for o in ctx.acct.get("opportunities", []):
        key = f"{ctx.A}/{o['id']}"
        backing = [f"{ctx.A}/{e}" for e in o.get("evidence", [])
                   if (facts.get(e) or {}).get("status") == "verified" or e in file_ids]
        best = "verified" if backing else "told" if any((facts.get(e) or {}).get("status") == "told" for e in o.get("evidence", [])) else "claim"
        premise = [c["claim_id"] for c in ctx.claims if key in c.get("premise_of", []) and c["verdict"] == "contradicted"]
        weak = [c["claim_id"] for c in ctx.claims if key in c["impact"]["opportunities"] and c["verdict"] == "contradicted"]
        worth = o.get("worth_up_to")
        if worth is None:
            continue  # worth is shown once qualified (identified deals appear in Deals, not the book)
        backed = bool(backing) and not premise
        status = "verified" if backed else ("claim" if premise else best)
        why = ("its worth is a claim the paperwork contradicts" if premise else
               f"backed by {len(backing)} verified fact/file" + (f"; {len(weak)} contradicted claim(s) attached" if weak else "") if backed else
               f"best evidence is {best}")
        deals.append({"id": item_id("q1", key), "deal_id": key, "text": o.get("title"), "stage": o.get("stage"), "value": worth,
                      "backed": backed, "status": status, "sources": [key] + (backing or [f"{ctx.A}/{e}" for e in o.get("evidence", [])][:4]) + premise,
                      "why": why, "contradicted_claims": weak})
    for o in ctx.acct.get("offers", []):
        if o.get("status") == "won":
            offers.append({"id": item_id("q1o", o["id"]), "deal_id": None, "text": o.get("title"), "value": o.get("value"),
                           "status": "told", "label": "Agreed, no order yet", "sources": [f"{ctx.A}/{o['id']}"],
                           "why": "Won offer with no order on file: outside both totals."})
    backed = [x for x in deals if x["backed"]]
    unbacked = [x for x in deals if not x["backed"]]
    total = lambda xs: sum(x["value"] for x in xs)
    contract = ctx.mv("contract.annual_value")
    summary = f"{fmt(total(backed), ctx.currency)} backed, {fmt(total(unbacked), ctx.currency)} unbacked"
    return {"answer": summary, "currency": ctx.currency, "backed_total": total(backed), "unbacked_total": total(unbacked),
            "backed_sources": sorted({s for x in backed for s in x["sources"]}), "unbacked_sources": sorted({s for x in unbacked for s in x["sources"]}),
            "deals": deals, "agreed_no_order": offers,
            "in_force": {"value": contract, "sources": [ctx.mref("contract.annual_value")]} if contract else None}


SYMBOLS = {"GBP": "£", "EUR": "€", "USD": "$"}


def fmt(v, cur):
    return "—" if v is None else f"{SYMBOLS.get(cur, '')}{v:,.0f}"


# ---------- Q2: power ----------

def q2_power(ctx):
    out = []
    for o in ctx.acct.get("opportunities", []):
        key = f"{ctx.A}/{o['id']}"
        spec = ctx.roles.get(o["id"], {})
        roles = []
        for r in ROLES:
            e = spec.get(r) or {}
            who = e.get("who")
            person = next((s for s in ctx.acct.get("stakeholders", []) if f"{ctx.A}/{s['id']}" == who), None)
            claims = [c for c in ctx.claims if who and c.get("said_by") == who]
            roles.append({"role": r, "who": who, "name": ctx.name(who) if who else None,
                          "stance": person.get("relationship") if person else None,
                          "text": f"{r.title()}: {ctx.name(who)}" if who else f"{r.title()}: unknown" + (f", ask {ctx.name(e['ask'])}" if e.get("ask") else ""),
                          "ask": e.get("ask"), "note": e.get("note"), "status": "analyst judgment" if e else "derived",
                          "claims": {"contradicted": sum(c["verdict"] == "contradicted" for c in claims), "total": len(claims)},
                          "sources": [key] + (e.get("sources") or []) + ([who] if who else [])})
        out.append({"id": item_id("q2", key), "deal_id": key, "text": o.get("title"), "stage": o.get("stage"), "roles": roles,
                    "sources": [key]})
    known = sum(1 for x in out for r in x["roles"] if r["who"])
    return {"answer": f"{known} of {len(out) * len(ROLES)} roles filled across {len(out)} deals", "deals": out}


# ---------- Q4: risks ----------

def q4_risks(ctx):
    cfg, rc = ctx.cfg, ctx.rules["contact"]
    risks = []

    def add(kind, severity, text, turns_bad_on, sources, why, deal=None, status="derived"):
        risks.append({"id": item_id("q4", kind, text), "kind": kind, "severity": severity, "text": text,
                      "turns_bad_on": turns_bad_on.isoformat() if turns_bad_on else None, "deal_id": deal,
                      "sources": [s for s in sources if s], "why": why, "status": status})

    unowned = {t.split("/", 1)[1] for t in ctx.mv("tickets.open_unowned", []) or []}
    request_ids = {ref: row.get("request_id") for ref, row, _ in ctx.pw.rows("requests.csv")}
    for u in ctx.mv("contact.unanswered", []):
        if any(request_ids.get(r) in unowned for r in u["refs"]):
            continue  # an unowned ticket: the "stuck" risk and its unblock move cover it
        day = d(u["date"])
        red, amber = rc["waiting_on_us_days"]["red"], rc["waiting_on_us_days"]["amber"]
        sev = "red" if u["days_waiting"] >= red else "amber" if u["days_waiting"] >= amber else "green"
        who = ctx.name(u["person"]) if u["person"] else u["from"]
        add("waiting", sev, f"{who} has waited {u['days_waiting']} days for a reply", day + timedelta(days=red),
            u["refs"] + [ctx.mref("contact.unanswered")], f"No reply from FlytBase on file (amber {amber} / red {red} days).")

    for s in ctx.acct.get("stakeholders", []):
        c = ctx.mv(f"contact.{s['id']}") or {}
        days = c.get("days_since_two_way")
        cold = rc["cold_contact_days"]
        if days is not None and days >= cold["amber"]:
            last = ctx.now - timedelta(days=days)
            add("cold", "red" if days >= cold["red"] else "amber", f"{s['name']} going cold: no call or email from us in {days} days",
                last + timedelta(days=cold["red"]), [f"{ctx.A}/{s['id']}", ctx.mref(f"contact.{s['id']}")],
                f"Cold contact: amber {cold['amber']} / red {cold['red']} days since our last call or email.")

    stopped = ctx.mv("docks.stopped_flying", [])
    if stopped:
        add("usage", "red", f"{len(stopped)} docks that patrolled regularly have stopped flying", ctx.now,
            [ctx.mref("docks.stopped_flying")] + stopped, "Regular before, zero flights in the last two usage weeks.")

    idle = ctx.mv("docks.idle_but_online", [])
    contracted = ctx.mv("docks.contracted")
    for x in q1_book(ctx)["deals"]:
        if x["backed"] and idle and contracted and x["deal_id"] == renewal_deal(ctx):
            share = round(x["value"] * len(idle) / contracted)
            term = d((ctx.mv("contract.term") or {}).get("end"))
            add("idle_docks", "amber", f"{fmt(share, ctx.currency)} of the backed renewal sits on {len(idle)} docks that don't fly",
                term - timedelta(days=cfg["renewal_red_days"]) if term else None,
                [x["deal_id"], ctx.mref("docks.idle_but_online"), ctx.mref("docks.contracted")],
                "Worth × idle docks / contracted docks: the part a customer can cut at renewal.", deal=x["deal_id"])

    for c in ctx.claims:
        if c["verdict"] != "contradicted":
            continue
        live = [o for o in ctx.acct.get("opportunities", []) if f"{ctx.A}/{o['id']}" in c["impact"]["opportunities"]
                and o.get("stage") != "identified"]
        if not live:
            continue
        big = max((o.get("worth_up_to") or 0) for o in live)
        dates = [x["date"] for x in deadline_dates(ctx) if x["date"] and x["deal"] in [f"{ctx.A}/{o['id']}" for o in live]]
        add("contradiction", "red" if big >= cfg["contradiction_worth"] else "amber", f"Contradicted: “{c['quote']}”",
            min(dates) if dates else None, [c["claim_id"]] + [f"{ctx.A}/{o['id']}" for o in live] + c["evidence_against"][:3],
            "A live deal rests on a claim the paperwork contradicts.", deal=f"{ctx.A}/{live[0]['id']}")

    for x in deadline_dates(ctx):
        if x.get("renewal"):
            days = ctx.days(x["date"])
            sev = "red" if days <= cfg["renewal_red_days"] else "amber" if days <= cfg["renewal_amber_days"] else "green"
            add("renewal", sev, f"Renewal due in {days} days with no new order on file", x["date"] - timedelta(days=cfg["renewal_red_days"]),
                x["sources"] + [f"{ctx.A}/contract.txt"], "The contract does not renew automatically.", deal=x["deal"])
        elif x.get("leaving"):
            add("leaving", "red", x["text"], x["date"], x["sources"], "A key person leaves; plan the handover before then.", deal=renewal_deal(ctx))
        elif not x.get("passed"):
            days = ctx.days(x["date"]) if x["date"] else None
            sev = "amber" if days is None else "red" if days <= 14 else "amber" if days <= 45 else "green"
            add("deadline", sev, f"Deadline: {x['text']}" + ("" if x["date"] else " (date unknown)"), x["date"],
                x["sources"], "From the account sheet" + (f"; game minute {x['derived_from_minute']} counted from the drop's arrival" if x.get("derived_from_minute") else ""),
                deal=x["deal"], status="derived" if x["date"] else "told")

    for tid, age in (ctx.mv("tickets.open", {}) or {}).items():
        if tid in (ctx.mv("tickets.open_unowned") or []) and age >= 60:
            add("stuck", "red", f"Ticket {tid.split('/')[1]} has sat {age} days with no owner", ctx.now, [tid, ctx.mref("tickets.open_unowned")],
                "An unowned customer request this old blocks whatever depends on it.")

    age = ctx.mv("usage.data_age_days")
    if age is not None and age > ctx.rules["metrics"]["stale_usage_days"]:
        add("stale", "grey", f"Flight data is {age} days old", None, [ctx.mref("usage.data_through")], "Usage windows end at the last week we hold.")

    order = {"red": 0, "amber": 1, "grey": 2, "green": 3}
    risks = [r for r in risks if r["severity"] != "green"]
    risks.sort(key=lambda r: (order[r["severity"]], r["turns_bad_on"] or "9999"))
    reds = sum(r["severity"] == "red" for r in risks)
    return {"answer": f"{reds} red, {sum(r['severity'] == 'amber' for r in risks)} amber", "risks": risks}


# ---------- Q3: moves ----------

def q3_moves(ctx, book, risks):
    cfg = ctx.cfg
    owners = cfg["owners"]
    deals = {x["deal_id"]: x for x in book["deals"]}
    opps = {f"{ctx.A}/{o['id']}": o for o in ctx.acct.get("opportunities", [])}
    dates = deadline_dates(ctx)
    candidates = []

    def worth(deal):
        if deal in deals:
            x = deals[deal]
            return x["value"] if x["backed"] else x["value"] * cfg["unbacked_factor"]
        o = opps.get(deal) or {}
        return o.get("worth_up_to") or cfg["identified_worth"]

    def urgency(target):
        if target is None:
            return cfg["unknown_date_urgency"]
        days = ctx.days(target)
        return next(b["x"] for b in cfg["urgency"] if days <= b["days"])

    def add(kind, text, deal, target, sources, defuses=(), bonus=0):
        deal = deal or renewal_deal(ctx) or next(iter(opps), None)
        stage = (opps.get(deal) or {}).get("stage", "identified")
        sw = cfg["stage_weight"][stage]
        defused = sum(cfg["risk_defused"].get(r["severity"], 0) for r in defuses)
        score = worth(deal) * sw * urgency(target) + defused + bonus
        due = min(ctx.now + timedelta(days=cfg["move_default_days"]),
                  (target - timedelta(days=2)) if target and target > ctx.now + timedelta(days=2) else ctx.now + timedelta(days=1))
        deadline = next((x["date"] for x in dates if x["deal"] == deal and x["date"] and x["date"] > ctx.now), None)
        if deadline and due >= deadline:
            due = deadline - timedelta(days=1)
        why = (f"worth {fmt(worth(deal), ctx.currency)} × stage {sw} × urgency {urgency(target)}"
               + (f" + risks defused {defused:,}" if defused else "") + (f" + unblock {bonus:,}" if bonus else ""))
        candidates.append({"id": item_id("q3", kind, text), "kind": kind, "text": text, "owner": owners[kind], "due": due.isoformat(),
                           "deal_id": deal, "score": round(score), "sources": [s for s in sources if s] + ([deal] if deal else []),
                           "why": why, "status": "derived"})

    by_kind = lambda k: [r for r in risks["risks"] if r["kind"] == k]
    for r in by_kind("waiting"):
        if r["severity"] in ("red", "amber"):
            deal = person_deal(ctx, r)
            add("reply", f"Reply to {r['text'].split(' has waited')[0]} and agree a next step", deal, d(r["turns_bad_on"]), r["sources"], [r])
    for r in by_kind("stuck"):
        blocked = next((k for k, o in opps.items() if (o.get("blocker") or {}).get("status") == "open"
                        and re.search(r"document|never sent|vendor|data-flow", (o["blocker"].get("text") or "").lower())), None)
        add("unblock", f"Answer {r['text'].split(' has sat')[0].replace('Ticket ', 'ticket ')}: send the documents it asks for",
            blocked, ctx.now, r["sources"], [r], bonus=cfg["unblock_bonus"] if blocked else 0)
    for x in dates:
        if x.get("leaving"):
            add("successor", f"Before {ctx.name(x['who']) if x.get('who') else 'our contact'} leaves: get a named successor and the sign-off path in writing",
                renewal_deal(ctx), x["date"], x["sources"], by_kind("leaving"))
        elif re.search(r"\bleaves?\b", (x["text"] or "").lower()) and not x.get("passed"):
            subject = re.split(r"\s+leaves?\b", x["text"], maxsplit=1)[0]
            subject = subject[0].lower() + subject[1:] if subject.startswith("The ") else subject
            add("knowledge", f"Before {subject} leaves: get the setup, routes and know-how written down",
                x["deal"] or renewal_deal(ctx), x["date"], x["sources"])
        elif x["date"] and not x.get("passed") and not x.get("renewal") and 0 <= ctx.days(x["date"]) <= 14:
            add("deadline", f"Meet the deadline: {x['text']} ({x['date'].isoformat()})", x["deal"], x["date"], x["sources"],
                [r for r in risks["risks"] if r["kind"] == "deadline" and r["turns_bad_on"] == x["date"].isoformat()])
    for r in by_kind("usage"):
        add("usage", f"Find out why {r['text'].split(' that')[0]} stopped flying, and restart them", renewal_deal(ctx), ctx.now, r["sources"], [r])
    for r in by_kind("contradiction"):
        claim = next((c for c in ctx.claims if c["claim_id"] == r["sources"][0]), None)
        if claim and claim.get("ask") and claim["ask"]["who"] in ctx.people and r["severity"] == "red":
            add("settle", f"Settle “{claim['quote'][:60]}” with {ctx.name(claim['ask']['who'])}", r["deal_id"], d(r["turns_bad_on"]),
                [claim["claim_id"], claim["ask"]["who"]] + r["sources"][1:], [r])

    # One move per deal and kind; keep the best.
    best = {}
    for m in sorted(candidates, key=lambda m: -m["score"]):
        best.setdefault((m["kind"], m["text"]) if m["kind"] in ("reply", "deadline") else (m["deal_id"], m["kind"]), m)
    ranked = sorted(best.values(), key=lambda m: -m["score"])
    # The top 3 mixes kinds (at most max_moves_per_kind each), so three replies can't crowd out an unblock.
    top, per_kind = [], {}
    for m in ranked:
        if len(top) < 3 and per_kind.get(m["kind"], 0) < cfg["max_moves_per_kind"]:
            top.append(m)
            per_kind[m["kind"]] = per_kind.get(m["kind"], 0) + 1
    top += [m for m in ranked if m not in top][: 3 - len(top)]
    rest = [m for m in ranked if m not in top]
    for i, m in enumerate(top + rest, 1):
        m["rank"] = i
    return {"answer": top[0]["text"] if top else "Nothing urgent.", "moves": top, "candidates": rest[:7]}


def person_deal(ctx, risk):
    """The deal a waiting customer's message bears on: a deal where they hold a role, else the renewal."""
    person = next((s for s in risk["sources"] if s in ctx.people), None)
    for deal_id, spec in ctx.roles.items():
        if person and any((spec.get(r) or {}).get("who") == person for r in ROLES):
            return f"{ctx.A}/{deal_id}"
    return renewal_deal(ctx)


# ---------- Q5: gaps ----------

def q5_gaps(ctx, power):
    gaps = []

    def add(text, why, who, sources, status="derived", deal=None):
        gaps.append({"id": item_id("q5", text), "text": text, "why": why, "who": who, "sources": [s for s in sources if s],
                     "status": status, "deal_id": deal})

    for c in ctx.claims:
        if c["verdict"] == "unverifiable" and c.get("ask") and c["origin"] not in ("request", "ticket"):
            deals = c["impact"]["opportunities"]
            add(c["ask"]["question"], f"Settles “{c['quote'][:80]}”" + (f", which {', '.join(deals)} rests on" if deals else ""),
                c["ask"]["who"], [c["claim_id"]] + c["source_ids"][:2], deal=deals[0] if deals else None)
        for p in c["possible_causes"]:
            add(f"Confirm: {p['text']}", "Inference: shown with its supporting ids, never as fact.",
                (c.get("ask") or {}).get("who") or c.get("said_by"), [c["claim_id"]] + p["ids"], status="inference")
    for x in power["deals"]:
        for r in x["roles"]:
            if not r["who"] and r["ask"]:
                add(f"Who is the {r['role']} on {x['text']}?", r["note"] or "Nobody we know fills this role.", r["ask"], r["sources"],
                    status="analyst judgment", deal=x["deal_id"])
    champion = next((r["who"] for x in power["deals"] for r in x["roles"] if r["role"] == "champion" and r["who"]), None)
    for n in ctx.pw.data("profile.json").get("not_on_file", []):
        add(n.rstrip("."), "Listed as not on file in the account profile.", champion, [f"{ctx.A}/profile.json"])
    for x in deadline_dates(ctx):
        if not x["date"] and not x.get("passed"):
            add(f"When: {x['text']}?", "A deadline with no date can't be planned around.", None, x["sources"])
    order = {"derived": 0, "analyst judgment": 1, "inference": 2}
    gaps.sort(key=lambda g: (g["deal_id"] is None, order.get(g["status"], 3)))
    return {"answer": f"{len(gaps)} open questions", "gaps": gaps}


# ---------- all five ----------

def compute(world, engine, as_of, rules, roles, anchors):
    out = {}
    for acct in world["accounts"]:
        A = acct["id"]
        ctx = Ctx(world, A, acct, engine["accounts"][A], as_of, rules, roles, anchors.get(A))
        book, power, risks = q1_book(ctx), q2_power(ctx), q4_risks(ctx)
        out[A] = {"q1": book, "q2": power, "q3": q3_moves(ctx, book, risks), "q4": risks, "q5": q5_gaps(ctx, power)}
    return out

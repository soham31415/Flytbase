"""CRM views over the assembled world and the engine output. Pure logic, no I/O.

Everything returned carries ids (`ref`, `sources`) so the UI can trace any item to its record.
"""

import re
from collections import Counter
from datetime import date

from app.engine.metrics import FROM_RE, Paperwork, d, long_date, people_of

STAGES = ["identified", "qualified", "cleared", "agreed"]
WEAK = ("contradicted", "unverifiable")


def metric_map(engine, A):
    return {m["name"]: m for m in engine["accounts"].get(A, {}).get("metrics", [])}


def mval(M, name, default=None):
    return M[name]["value"] if name in M else default


def account_summary(world, engine, acct, as_of):
    A = acct["id"]
    M = metric_map(engine, A)
    claims = engine["accounts"].get(A, {}).get("claims", [])
    pw = Paperwork(world, A)
    profile = pw.data("profile.json")
    term = mval(M, "contract.term") or {}
    renew = d(term.get("end"))
    now = d(as_of) or date.today()
    unanswered = mval(M, "contact.unanswered", [])
    opps = acct.get("opportunities", [])
    return {
        "id": A, "name": acct.get("name"), "has_sheet": acct.get("has_sheet"), "currency": profile.get("currency"),
        "sector": profile.get("sector"), "official_story": acct.get("official_story"),
        "annual_value": mval(M, "contract.annual_value"), "term_end": term.get("end"),
        "days_to_renewal": (renew - now).days if renew else None,
        "pipeline": sum(o.get("worth_up_to") or 0 for o in opps if o.get("stage") in STAGES[1:]),
        "deals": len(opps), "people": len(people_of(world, A)),
        "docks": mval(M, "docks.contracted"), "utilisation_30d": mval(M, "docks.utilisation_30d"),
        "idle_docks": len(mval(M, "docks.idle_but_online", [])), "stopped_flying": len(mval(M, "docks.stopped_flying", [])),
        "usage_data_age_days": mval(M, "usage.data_age_days"),
        "unanswered": len(unanswered), "longest_wait_days": max((u["days_waiting"] for u in unanswered), default=0),
        "claims": dict(Counter(c["verdict"] for c in claims)),
        "signals": sum(1 for s in world["signals"] if s.get("account") == A),
        "sources": [f"{A}/{A}"] + ([f"{A}/profile.json"] if profile else []),
    }


def portfolio(world, engine, as_of):
    return {
        "as_of": as_of,
        "accounts": [account_summary(world, engine, a, as_of) for a in world["accounts"]],
        "signals": sorted(world["signals"], key=lambda s: s.get("dated", ""), reverse=True),
    }


def people(world, engine, acct, as_of):
    """Stakeholders and profile contacts, with last contact, what they wait on, and their claims."""
    A = acct["id"]
    M = metric_map(engine, A)
    claims = engine["accounts"].get(A, {}).get("claims", [])
    stake = {f"{A}/{s['id']}": s for s in acct.get("stakeholders", [])}
    contacts = {}
    for r in world["paperwork"].get(A, []):
        if r.get("kind") == "profile" and isinstance(r.get("data"), dict):
            contacts = {f"{A}/{c['id']}": c for c in r["data"].get("contacts_on_file", [])}
    out = []
    for key, name in people_of(world, A).items():
        s, c = stake.get(key, {}), contacts.get(key, {})
        contact = mval(M, f"contact.{key.split('/', 1)[1]}", {}) or {}
        mine = [x for x in claims if x.get("said_by") == key]
        out.append({
            "ref": key, "name": name, "role": s.get("role") or c.get("role"), "relationship": s.get("relationship"),
            "available": s.get("available"), "origin": "stakeholder" if s else "contact", "note": c.get("note"),
            "last_contact": contact.get("last_contact"), "last_inbound": contact.get("last_inbound"),
            "last_outbound": contact.get("last_outbound"), "last_call": contact.get("last_call"),
            "days_since_two_way": contact.get("days_since_two_way"),
            "waiting_on_us": [u for u in mval(M, "contact.unanswered", []) if u.get("person") == key],
            "claims": [{"claim_id": x["claim_id"], "verdict": x["verdict"], "quote": x["quote"]} for x in mine],
            "deals": [f"{A}/{o['id']}" for o in acct.get("opportunities", [])
                      if any(f"{A}/{e}" in {f"{A}/{f['id']}" for f in acct.get("facts", []) if f.get("said_by") == name} for e in o.get("evidence", []))
                      or o.get("pitch_to") == name],
            "asked_about": [x["claim_id"] for x in claims if (x.get("ask") or {}).get("who") == key and x["verdict"] in WEAK],
            "sources": [key] + contact.get("refs", [])[:20],
        })
    return sorted(out, key=lambda p: (p["origin"] != "stakeholder", p["name"] or ""))


def deals(world, engine, acct):
    """Opportunities with evidence (resolved to labels), deadline, and the claims they rest on."""
    A = acct["id"]
    claims = engine["accounts"].get(A, {}).get("claims", [])
    facts = {f"{A}/{f['id']}": f for f in acct.get("facts", [])}
    names = {s["name"]: f"{A}/{s['id']}" for s in acct.get("stakeholders", [])}
    out = []
    for o in acct.get("opportunities", []):
        key = f"{A}/{o['id']}"
        linked = [c for c in claims if key in c.get("impact", {}).get("opportunities", [])]
        evidence = []
        for e in o.get("evidence", []):
            f = facts.get(f"{A}/{e}")
            evidence.append({"ref": f"{A}/{e}", "status": f["status"] if f else "file",
                             "text": f["text"] if f else e, "said_by": names.get(f.get("said_by")) if f else None})
        statuses = {x["status"] for x in evidence}
        out.append({
            "ref": key, "title": o.get("title"), "stage": o.get("stage"), "worth_up_to": o.get("worth_up_to"),
            "blocker": o.get("blocker"), "deadline": o.get("deadline"),
            "deadline_ref": f"{key}#deadline" if o.get("deadline") else None,
            "evidence": evidence, "evidence_best": "verified" if statuses & {"verified", "file"} else "told" if "told" in statuses else "claim" if statuses else None,
            "claims": [{"claim_id": c["claim_id"], "verdict": c["verdict"], "quote": c["quote"], "said_by": c.get("said_by")} for c in linked],
            "weak_claims": sum(1 for c in linked if c["verdict"] in WEAK),
            "contradicted_premise": [c["claim_id"] for c in claims if key in c.get("premise_of", []) and c["verdict"] == "contradicted"],
            "sources": [key] + [x["ref"] for x in evidence],
        })
    return out


def activity(world, acct, limit=400):
    """Everything that happened on the account, newest first: calls, emails, tickets, requests, notes, history, signals."""
    A = acct["id"]
    pw = Paperwork(world, A)
    items = []

    def add(day, kind, title, who, ref, detail=None):
        if day:
            items.append({"date": day if isinstance(day, str) else day.isoformat(), "kind": kind, "title": title,
                          "who": who, "ref": ref, "detail": detail})

    for c in pw.data("calls_index.json").get("calls", []):
        add(c.get("date"), "call", c.get("title"), ", ".join(c.get("customer", []) + c.get("flytbase", [])),
            pw.key(c["path"]) if c.get("path") else pw.key("calls_index.json"), c.get("summary"))
    for m in pw.data("mail_log.json").get("mail", []):
        if m.get("kind") == "person":
            ref = pw.key(m["path"]) if m.get("path") else f"{A}/{m['thread']}"
            add(m.get("date"), "email", m.get("subject"), f"{m.get('from')} → {m.get('to')}", ref, m.get("note"))
    for r in pw.of_kind("email"):
        if not any(m.get("path") == r["path"] for m in pw.data("mail_log.json").get("mail", [])):  # signal emails
            text = r.get("text") or ""
            frm = FROM_RE.search(text)
            subject = re.search(r"^Subject: (.+)$", text, re.M)
            date_m = re.search(r"^Date: (.+)$", text, re.M)
            add(long_date(date_m.group(1)) if date_m else None, "email", subject.group(1) if subject else r["path"],
                frm.group(1) if frm else None, pw.key(r["path"]), "via signal" if "/" in r.get("source", "") else None)
    for t in pw.data("tickets.json").get("tickets", []):
        add(t.get("opened"), "ticket", f"{t['id']} {t.get('subject')}", t.get("requester"), f"{A}/{t['id']}",
            f"{t.get('status')}, assignee: {t.get('assignee') or 'none'}")
    for ref, row, source in pw.rows("requests.csv"):
        add(row.get("opened"), "request", row.get("summary"), row.get("from"), ref, f"{row.get('status')}, owner: {row.get('owner') or 'none'}")
    for n in pw.data("internal_notes.json").get("notes", []):
        add(n.get("date"), "note", n.get("text"), n.get("from"), pw.key("internal_notes.json"))
    for h in pw.data("deal_history.json").get("history", []):
        day = h.get("date", "")
        day = day if re.fullmatch(r"\d{4}-\d{2}-\d{2}", day) else (day[:7] + "-01" if re.fullmatch(r"\d{4}-\d{2}.*", day) else None)
        add(day, "history", h.get("what"), None, pw.key("deal_history.json"), h.get("detail"))
    for s in world["signals"]:
        if s.get("account") == A:
            add(s.get("dated"), "signal", s.get("title"), None, f"{A}/{s['id']}", s.get("summary"))
    items.sort(key=lambda x: x["date"], reverse=True)
    return items[:limit]


def evidence(world, acct):
    """The paperwork and files we hold, for the evidence browser."""
    A = acct["id"]
    docs = [{"ref": f"{A}/{r['path']}", "path": r["path"], "kind": r.get("kind"), "source": r.get("source"),
             "rows": len(r["rows"]) if r.get("rows") else None} for r in world["paperwork"].get(A, [])]
    files = [{"ref": f"{A}/{f['id']}", "path": f.get("path"), "kind": "file", "source": f.get("source"),
              "title": f.get("title"), "has_content": f.get("content") is not None} for f in world["files"] if f.get("account") == A]
    return sorted(files, key=lambda x: x["path"] or "") + sorted(docs, key=lambda x: (x["kind"] or "", x["path"]))


def account_detail(world, engine, A, as_of):
    acct = next((a for a in world["accounts"] if a["id"] == A), None)
    if acct is None:
        return None
    return {
        "summary": account_summary(world, engine, acct, as_of),
        "people": people(world, engine, acct, as_of),
        "deals": deals(world, engine, acct),
        "offers": [{**o, "ref": f"{A}/{o['id']}"} for o in acct.get("offers", [])],
        "deadlines": [{**x, "ref": next((f"{A}/{o['id']}#deadline" for o in acct.get("opportunities", [])
                                         if (o.get("deadline") or {}).get("text") == x.get("text")), None)} for x in acct.get("deadlines", [])],
        "facts": [{**f, "ref": f"{A}/{f['id']}"} for f in acct.get("facts", [])],
        "metrics": [{k: m[k] for k in ("name", "value", "definition", "sources")} | {"ref": f"{A}/metric/{m['name']}", "row_count": len(m["rows"])}
                    for m in engine["accounts"].get(A, {}).get("metrics", [])],
        "claims": engine["accounts"].get(A, {}).get("claims", []),
        "activity": activity(world, acct),
        "evidence": evidence(world, acct),
        "signals": [s for s in world["signals"] if s.get("account") == A],
    }

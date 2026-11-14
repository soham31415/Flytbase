"""Claims per account: the analyst's YAML plus claims derived from structured fields.

Sources of claims:
- data/analysis/claims_<account>.yaml: drafted once by the analyst, reviewed by you. Holds claims
  from the official story, emails and call notes, plus `fact_overrides` (type, subject, test, ...)
  for the auto-derived fact claims.
- auto-derived, so new data from signals produces new claims with no manual work:
  every fact, every open requests.csv row, every open ticket.

Claim ids are derived (never in the data) and stable: `<account>/claim/<n>` for YAML claims,
`<account>/claim/f-<fact id>`, `r-<request id or row>`, `t-<ticket id>` for derived ones.
"""

import re
from pathlib import Path

import yaml

from app.engine.metrics import Paperwork, people_of

TYPES = ("demand", "usage", "satisfaction", "budget", "authority", "timeline", "problem")
DOCK_ID = re.compile(r"\b[A-Z]{2,5}-D?\d{2}\b")


def load_yaml(analysis_dir, account):
    path = Path(analysis_dir) / f"claims_{account}.yaml"
    if not path.exists():
        return {"claims": [], "fact_overrides": {}}
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return {"claims": data.get("claims") or [], "fact_overrides": data.get("fact_overrides") or {}}


def classify(text, rules):
    t = (text or "").lower()
    for kind, words in rules["checks"]["type_keywords"].items():
        if any(w in t for w in words):
            return kind
    return "problem"


def request_type(text, rules):
    """A request or ticket states a need or a problem; it is never tested as a usage or budget claim."""
    return "demand" if classify(text, rules) == "demand" else "problem"


def subject_docks(account, text):
    return [f"{account}/{m}" for m in dict.fromkeys(DOCK_ID.findall(text or ""))]


def build(world, account, rules, analysis_dir):
    """All claims for one account, as dicts with the fields the checks need."""
    spec = load_yaml(analysis_dir, account)
    acct = next((a for a in world["accounts"] if a["id"] == account), {})
    people = people_of(world, account)
    by_name = {name: key for key, name in people.items()}
    claims = []

    def who(text):
        return next((key for name, key in by_name.items() if name and name.lower() in (text or "").lower()), None)

    replaced = set()
    for c in spec["claims"]:
        claim = {
            "claim_id": f"{account}/claim/{c['n']}", "derived": True, "origin": "analyst",
            "said_by": c.get("said_by"), "type": c.get("type"), "subject": c.get("subject") or [],
            "quantity": c.get("quantity"), "date": c.get("date"), "source_ids": c.get("source_ids") or [],
            "quote": c.get("quote"), "test": c.get("test") or [], "evidence_for": c.get("evidence_for") or [],
            "evidence_against": c.get("evidence_against") or [], "possible_causes": c.get("possible_causes") or [],
            "ask": c.get("ask"), "note": c.get("note"),
        }
        claims.append(claim)
        replaced.update(c.get("replaces") or [])

    # Facts: every fact is something a person told us.
    overrides = spec["fact_overrides"]
    for f in acct.get("facts", []):
        cid = f"{account}/claim/f-{f['id']}"
        o = overrides.get(f["id"]) or {}
        sources = [f"{account}/{f['id']}"] + ([f"{account}/{f['verified_by']}"] if f.get("verified_by") else [])
        claims.append({
            "claim_id": cid, "derived": True, "origin": "fact", "fact_status": f.get("status"),
            "said_by": by_name.get(f.get("said_by")) or f.get("said_by"), "type": o.get("type") or classify(f.get("text"), rules),
            "subject": o.get("subject") or subject_docks(account, f.get("text")), "quantity": o.get("quantity"),
            "date": None, "minute": f.get("learned_at_minute"), "source_ids": sources, "quote": f.get("text"),
            "test": o.get("test") or [], "evidence_for": o.get("evidence_for") or [], "evidence_against": o.get("evidence_against") or [],
            "possible_causes": o.get("possible_causes") or [], "ask": o.get("ask"), "note": o.get("note"),
        })

    pw = Paperwork(world, account)
    open_tickets = {}
    for t in pw.data("tickets.json").get("tickets", []):
        if (t.get("status") or "").lower() == "closed":
            continue
        # The requester's own words; a bracketed note like "(details not copied)" is not their words.
        words = next((c.get("text") for c in t.get("conversation", [])
                      if c.get("from") != "Support desk" and not (c.get("text") or "").startswith("(")), None) or t.get("subject")
        open_tickets[t["id"]] = {
            "claim_id": f"{account}/claim/t-{t['id']}", "derived": True, "origin": "ticket",
            "said_by": who(t.get("requester")) or t.get("requester"), "type": request_type(words, rules),
            "subject": subject_docks(account, words), "quantity": None, "date": t.get("opened"),
            "source_ids": [f"{account}/{t['id']}"], "quote": words, "test": [], "evidence_for": [], "evidence_against": [],
            "possible_causes": [], "ask": None, "note": None,
        }
    claims += open_tickets.values()

    for ref, row, source in pw.rows("requests.csv"):
        rid = (row.get("request_id") or "").strip()
        if (row.get("status") or "").lower() != "open" or rid in open_tickets:
            continue  # closed, or already a claim through its ticket
        real = rid and not rid.startswith("(")
        sources = [f"{account}/requests.csv"] + ([f"{account}/{rid}"] if real else []) + ([source] if "/" in source else [])
        claims.append({
            "claim_id": f"{account}/claim/r-{rid if real else 'row' + ref.rsplit('#r', 1)[1]}", "derived": True, "origin": "request",
            "said_by": who(row.get("from")) or row.get("from"), "type": request_type(row.get("summary"), rules),
            "subject": subject_docks(account, row.get("summary")), "quantity": None, "date": row.get("opened"),
            "source_ids": sources, "row": ref, "quote": row.get("summary"), "test": [], "evidence_for": [], "evidence_against": [],
            "possible_causes": [], "ask": None, "note": None,
        })

    return [c for c in claims if c["claim_id"] not in replaced]


# ---------- quote verification ----------

def norm(text):
    return " ".join((text or "").split())


def record_text(rec):
    """Every string inside a record, joined: the text a quote must come from."""
    out = []

    def visit(v):
        if isinstance(v, str):
            out.append(v)
        elif isinstance(v, dict):
            for x in v.values():
                visit(x)
        elif isinstance(v, list):
            for x in v:
                visit(x)

    visit(rec)
    return "\n".join(out)


def quote_found(claim, lookup):
    """True if the quote appears verbatim (whitespace-normalised) in one of the claim's sources.

    `lookup(key)` returns the record behind an id (id_index record), or None.
    """
    q = norm(claim.get("quote"))
    if not q:
        return False
    for key in claim.get("source_ids", []):
        rec = lookup(key)
        if rec is not None and q in norm(record_text(rec)):
            return True
    return False

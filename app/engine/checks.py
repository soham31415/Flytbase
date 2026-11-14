"""Check every claim against the metrics and the evidence. Pure logic.

A claim is settled, in this order:
1. its `test` (metric comparisons from the YAML): all pass -> supported, none -> contradicted, mixed -> partial;
2. a verified fact (`verified_by` names a file we hold) -> supported;
3. the analyst's evidence_for / evidence_against ids ("analyst judgment");
4. the default check for its type (the check library below);
5. otherwise unverifiable.
Analyst evidence against a claim never lets it stay "supported": it drops to partial.

Result per claim: {verdict, basis, numbers, evidence_for, evidence_against, possible_causes, impact, ask, tests}.
"""

import operator

STAGES = ["identified", "qualified", "cleared", "agreed"]
OPS = {">=": operator.ge, "<=": operator.le, ">": operator.gt, "<": operator.lt, "==": operator.eq, "!=": operator.ne}


def metric_ref(m):
    return f"{m['account']}/metric/{m['name']}"


def comparable(value):
    """Lists and dicts compare by size; everything else as is."""
    return len(value) if isinstance(value, (list, dict)) else value


def run_test(test, M):
    """One comparison. Returns (passed: bool|None, metric or None). None = metric missing."""
    m = M.get(test["metric"])
    if m is None:
        return None, None
    value = m["value"]
    if "field" in test:
        for part in str(test["field"]).split("."):
            value = value.get(part) if isinstance(value, dict) else None
    op = test.get("op", ">=")
    if op == "contains":
        return (test["value"] in (value or [])), m
    if op == "lacks":
        return (test["value"] not in (value or [])), m
    if value is None:
        return None, m
    return bool(OPS[op](comparable(value), test["value"])), m


# ---------- the check library, keyed by claim type (used when a claim has no test) ----------

def usage_vs_flight_log(claim, M, cfg):
    """Account-level usage claims against utilisation; dock-level ones against those docks' flights."""
    registered = set((M.get("docks.registered") or {}).get("rows", []))
    docks = [s for s in claim["subject"] if s in registered]
    if docks:
        never = set(_v(M, "docks.never_flown") or [])
        idle = set(_v(M, "docks.idle_but_online") or [])
        flying = [s for s in docks if s not in never | idle]
        verdict = "supported" if len(flying) == len(docks) else "contradicted" if not flying else "partial"
        return verdict, ["docks.never_flown", "docks.idle_but_online"]
    if claim["subject"]:
        return None  # about a force, plant or site: only an explicit test can settle it
    u = _v(M, "docks.utilisation_30d")
    if u is None:
        return None
    bands = cfg["checks"]["utilisation"]
    return ("supported" if u >= bands["supported"] else "partial" if u >= bands["partial"] else "contradicted"), ["docks.utilisation_30d"]


def demand_vs_utilisation(claim, M, cfg):
    """Asking for N more docks while existing docks sit idle. Only claims with a quantity."""
    u, idle = _v(M, "docks.utilisation_30d"), _v(M, "docks.idle_but_online")
    if u is None or claim.get("quantity") is None:
        return None
    bands = cfg["checks"]["utilisation"]
    if u >= bands["supported"] and not idle:
        return "supported", ["docks.utilisation_30d", "docks.idle_but_online"]
    return ("partial" if u >= bands["partial"] else "contradicted"), ["docks.utilisation_30d", "docks.idle_but_online"]


def satisfaction_vs_tickets(claim, M, cfg):
    """Contentment with us, account-wide, against unanswered messages and unowned open tickets."""
    if claim["subject"]:
        return None
    red = cfg["contact"]["waiting_on_us_days"]["red"]
    waiting = [u for u in (_v(M, "contact.unanswered") or []) if u["days_waiting"] >= red]
    unowned = _v(M, "tickets.open_unowned") or []
    if "contact.unanswered" not in M and "tickets.open_unowned" not in M:
        return None
    return ("partial" if waiting or unowned else "supported"), ["contact.unanswered", "tickets.open_unowned"]


def budget_vs_invoices(claim, M, cfg):
    """Money claims against invoices: overdue invoices contradict. Paid history proves no future budget."""
    if _v(M, "invoices.overdue"):
        return "contradicted", ["invoices.overdue"]
    return None


def authority_vs_signatory(claim, M, cfg):
    """A person claiming authority, against who signed the contract."""
    signed = str(_v(M, "contract.signed") or "")
    name = str(claim.get("said_by_name") or "")
    if not signed or not name or name.lower() not in signed.lower():
        return None
    return "supported", ["contract.signed"]


def timeline_vs_contract(claim, M, cfg):
    return None  # dates need an explicit test (e.g. contract.term end); no safe default


def problem_vs_telemetry(claim, M, cfg):
    """A reported problem on named docks, against device events, idle and never-flown docks."""
    registered = set((M.get("docks.registered") or {}).get("rows", []))
    docks = [s for s in claim["subject"] if s in registered]
    if not docks:
        return None
    seen = set((_v(M, "docks.problem_events") or {}).keys()) | set(_v(M, "docks.never_flown") or []) \
        | set(_v(M, "docks.idle_but_online") or []) | set(_v(M, "docks.not_commissioned") or [])
    hit = [d for d in docks if d in seen]
    return ("supported" if len(hit) == len(docks) else "partial" if hit else "contradicted"), \
        ["docks.problem_events", "docks.never_flown", "docks.idle_but_online", "docks.not_commissioned"]


CHECKS = {
    "usage": usage_vs_flight_log,
    "demand": demand_vs_utilisation,
    "satisfaction": satisfaction_vs_tickets,
    "budget": budget_vs_invoices,
    "authority": authority_vs_signatory,
    "timeline": timeline_vs_contract,
    "problem": problem_vs_telemetry,
}


def _v(M, name):
    m = M.get(name)
    return m["value"] if m else None


# ---------- evaluation ----------

def evaluate(claim, M, ctx, cfg):
    """Settle one claim. M: {metric name: metric}. ctx: {opportunities, people, account}."""
    tests, numbers = [], []
    verdict, basis = None, None

    if claim["test"]:
        results = []
        for t in claim["test"]:
            passed, m = run_test(t, M)
            results.append(passed)
            tests.append({**t, "passed": passed, "actual": None if m is None else m["value"]})
            if m:
                numbers.append(metric_ref(m))
        known = [r for r in results if r is not None]
        if known:
            verdict = "supported" if all(known) else "contradicted" if not any(known) else "partial"
            if len(known) < len(results) and verdict == "supported":
                verdict = "partial"
            basis = "metric"

    if verdict is None and claim.get("fact_status") == "verified":
        verdict, basis = "supported", "verified file"

    against, support = list(claim["evidence_against"]), list(claim["evidence_for"])
    if verdict is None and (against or support):
        verdict = "partial" if against and support else "contradicted" if against else "supported"
        basis = "analyst judgment"

    if verdict is None:
        check = CHECKS.get(claim["type"])
        result = check(claim, M, cfg) if check else None
        if result:
            verdict, used = result
            basis = f"default check: {check.__name__}"
            numbers += [metric_ref(M[n]) for n in used if n in M]

    if verdict is None:
        verdict, basis = "unverifiable", "no test, file or record settles it"
    elif verdict == "supported" and against:
        verdict, basis = "partial", f"{basis}; analyst evidence against"

    # Metric sources join the evidence on the side the numbers point to.
    metric_sources = sorted({s for ref in numbers for s in M[ref.split("/metric/", 1)[1]]["sources"]})
    if verdict in ("supported", "partial"):
        support += [s for s in metric_sources if s not in support]
    if verdict in ("contradicted", "partial"):
        against += [s for s in metric_sources if s not in against and s not in support]
    if basis == "verified file":
        support += [s for s in claim["source_ids"][1:] if s not in support]

    return {
        "verdict": verdict, "basis": basis, "tests": tests, "numbers": list(dict.fromkeys(numbers)),
        "evidence_for": support, "evidence_against": against,
        "possible_causes": [{**p, "status": "inference"} for p in claim["possible_causes"]],
        "impact": impact(claim, verdict, ctx), "ask": ask(claim, verdict, ctx, cfg),
    }


def linked_opportunities(claim, ctx):
    """Deals this claim bears on: in the deal's evidence, or named in the claim's subject."""
    out = []
    for o in ctx["opportunities"]:
        key = f"{ctx['account']}/{o['id']}"
        evidence = {f"{ctx['account']}/{e}" for e in o.get("evidence", [])}
        if key in claim["subject"] or evidence & set(claim["source_ids"]):
            out.append(o)
    return out


def impact(claim, verdict, ctx):
    A = ctx["account"]
    opps = linked_opportunities(claim, ctx)
    out = {"opportunities": [f"{A}/{o['id']}" for o in opps], "q1": [], "q4": [], "q5": []}
    weak = verdict in ("contradicted", "unverifiable")
    for o in opps:
        key = f"{A}/{o['id']}"
        live = o.get("stage") in STAGES[1:]
        if weak and live and o.get("worth_up_to"):
            out["q1"].append(f"{key} can't count as backed on this claim ({verdict}).")
        if verdict == "contradicted" and live:
            out["q4"].append(f"{key} rests on a contradicted claim.")
    if verdict == "contradicted" and claim["said_by"] == "official_story":
        out["q4"].append("The official story is contradicted by the paperwork.")
    if verdict == "unverifiable":
        out["q5"].append("Nothing we hold settles this claim.")
    return out


def ask(claim, verdict, ctx, cfg):
    """Who could settle the claim. The YAML's ask wins; else a stakeholder by role, else the team."""
    if verdict == "supported" and not claim.get("ask"):
        return None
    if claim.get("ask"):
        return claim["ask"]
    rule = cfg["checks"]["ask"].get(claim["type"]) or {}
    speaker = claim.get("said_by")
    who = next((key for key, p in ctx["people"].items()
                if key != speaker and any(w in (p.get("role") or "").lower() for w in rule.get("role_words", []))), None)
    who = who or rule.get("team") or "team/neel_sharma"
    return {"question": f"Can you confirm or correct: \"{claim['quote']}\"", "who": who}

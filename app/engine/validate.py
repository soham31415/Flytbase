"""Drop any answer item whose sources are empty or cite something we don't hold (CLAUDE.md hard rule).

A source may be an id in the index, a metric ref (`A/metric/<name>`) or a CSV row ref (`A/<path>#r<n>`).
"""

import logging

log = logging.getLogger("validate")


def check(answers, idx, engine):
    metrics = {f"{A}/metric/{m['name']}" for A, acct in engine["accounts"].items() for m in acct["metrics"]}
    rows = {r for acct in engine["accounts"].values() for m in acct["metrics"] for r in m["rows"]}
    claims = {c["claim_id"] for acct in engine["accounts"].values() for c in acct["claims"]}

    def known(ref):
        return ref in idx.entries or ref in metrics or ref in rows or ref in claims

    dropped = []

    def keep(item, where):
        bad = [s for s in item.get("sources", []) if not known(s)]
        if not item.get("sources") or bad:
            dropped.append({"where": where, "text": item.get("text"), "unknown_sources": bad or ["(no sources)"]})
            log.warning("dropped %s item %r: %s", where, item.get("text"), bad or "no sources")
            return False
        return True

    for A, a in answers.items():
        a["q1"]["deals"] = [x for x in a["q1"]["deals"] if keep(x, f"{A}.q1")]
        a["q1"]["agreed_no_order"] = [x for x in a["q1"]["agreed_no_order"] if keep(x, f"{A}.q1")]
        for deal in a["q2"]["deals"]:
            deal["roles"] = [r for r in deal["roles"] if keep(r, f"{A}.q2")]
        a["q3"]["moves"] = [x for x in a["q3"]["moves"] if keep(x, f"{A}.q3")]
        a["q3"]["candidates"] = [x for x in a["q3"]["candidates"] if keep(x, f"{A}.q3")]
        a["q4"]["risks"] = [x for x in a["q4"]["risks"] if keep(x, f"{A}.q4")]
        a["q5"]["gaps"] = [x for x in a["q5"]["gaps"] if keep(x, f"{A}.q5")]
    return dropped

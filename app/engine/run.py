"""Run the claims-vs-evidence engine for every account. Pure logic over the assembled world.

evaluate(...) -> {as_of, accounts: {id: {metrics: [...], claims: [...]}}}. The five answers
(Step 3c) are built on top of this.
"""

from pathlib import Path

import yaml

from app.config import ROOT
from app.engine import checks, claims as claims_mod, metrics as metrics_mod
from app.world import csv_rows

RULES_PATH = ROOT / "config" / "rules.yaml"
ANALYSIS_DIR = ROOT / "data" / "analysis"


def load_rules(path=RULES_PATH):
    return yaml.safe_load(Path(path).read_text(encoding="utf-8"))


def evaluate(world, as_of, rules=None, analysis_dir=ANALYSIS_DIR):
    rules = rules or load_rules()
    out = {"as_of": as_of, "accounts": {}}
    for acct in world["accounts"]:
        A = acct["id"]
        ms = metrics_mod.compute(world, A, as_of, rules)
        M = {m["name"]: m for m in ms}
        people = {f"{A}/{s['id']}": s for s in acct.get("stakeholders", [])}
        for key, name in metrics_mod.people_of(world, A).items():
            people.setdefault(key, {"name": name, "role": None})
        ctx = {"account": A, "opportunities": acct.get("opportunities", []), "people": people}
        results = []
        for c in claims_mod.build(world, A, rules, analysis_dir):
            c["said_by_name"] = (people.get(c["said_by"]) or {}).get("name") if c.get("said_by") else None
            results.append({**c, **checks.evaluate(c, M, ctx, rules)})
        out["accounts"][A] = {"metrics": ms, "claims": results}
    return out


def resolve_metric(engine, world, account, name, lookup):
    """A metric with its definition and the exact rows it counted, each row resolved to its content."""
    acct = engine["accounts"].get(account)
    m = next((x for x in (acct or {}).get("metrics", []) if x["name"] == name), None)
    if m is None:
        return None
    pw = metrics_mod.Paperwork(world, account)
    files = {f["id"]: f for f in world["files"] if f.get("account") == account}
    rows = []
    for ref in m["rows"]:
        if "#r" in ref:
            path, n = ref.split("/", 1)[1].rsplit("#r", 1)
            if path in files:  # a row of a file (get_file / get_new_account_file), not paperwork
                content = files[path].get("content") or {}
                found = [(ref, row, f"{account}/{path}") for row in csv_rows(content.get("text") or "")]
            else:
                found = pw.rows(path)
            rec = found[int(n) - 1] if 0 < int(n) <= len(found) else None
            rows.append({"ref": ref, "row": rec[1] if rec else None, "source": rec[2] if rec else None})
        else:
            rec = lookup(ref)
            rows.append({"ref": ref, "row": rec["record"] if rec else None, "source": (rec.get("source") or rec.get("file")) if rec else None})
    return {**m, "rows": rows}

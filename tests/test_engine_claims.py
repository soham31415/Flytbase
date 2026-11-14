"""Step 3b: metrics, claims and checks."""

import copy
import json

import pytest

from app.engine.claims import quote_found
from app.engine.run import evaluate, load_rules, resolve_metric
from app.ids import build as build_ids
from app.world import assemble, in_game_today
from tests.conftest import RAW_DIR, needs_snapshots

pytestmark = needs_snapshots


def load_raw():
    return {p.stem: json.loads(p.read_text(encoding="utf-8")) for p in RAW_DIR.glob("*.json") if not p.stem.startswith("_")}


@pytest.fixture(scope="module")
def state():
    world = assemble(load_raw())
    idx = build_ids(world)
    as_of, _ = in_game_today(world)
    return world, idx, evaluate(world, as_of, load_rules())


def lookup(idx):
    return lambda key: (idx.entries.get(key) or {}).get("record")


def test_every_quote_is_verbatim_in_its_source(state):
    world, idx, engine = state
    bad = [c["claim_id"] for acct in engine["accounts"].values() for c in acct["claims"] if not quote_found(c, lookup(idx))]
    assert not bad, f"quotes not found verbatim in their sources: {bad}"


def test_every_cited_id_resolves(state):
    world, idx, engine = state
    for acct in engine["accounts"].values():
        for c in acct["claims"]:
            for key in c["source_ids"] + c["evidence_for"] + c["evidence_against"] + [i for p in c["possible_causes"] for i in p["ids"]]:
                assert key in idx.entries, f"{c['claim_id']} cites unknown id {key}"
            if c["ask"] and "/" in c["ask"]["who"]:
                assert c["ask"]["who"] in idx.entries, f"{c['claim_id']} asks unknown person {c['ask']['who']}"


def test_every_metric_row_and_source_resolves(state):
    world, idx, engine = state
    for A, acct in engine["accounts"].items():
        for m in acct["metrics"]:
            for s in m["sources"]:
                assert s in idx.entries, f"{A} {m['name']} source {s}"
            traced = resolve_metric(engine, world, A, m["name"], lambda k: idx.entries.get(k))
            missing = [r["ref"] for r in traced["rows"] if r["row"] is None]
            assert not missing, f"{A} {m['name']} rows don't resolve: {missing[:5]}"


def test_each_account_has_a_contradiction_from_the_findings(state):
    _, _, engine = state
    verdict = {c["claim_id"]: c["verdict"] for acct in engine["accounts"].values() for c in acct["claims"]}
    # One contradiction per account that the findings call out.
    expected = {
        "A/claim/f-rc_appetite_0": "contradicted",   # "All four forces are keen" (findings_A section 3.1)
        "A/claim/f-rc_funding_0": "contradicted",    # "The alliance has the budget" (3.3)
        "B/claim/f-sr_roi_0": "contradicted",        # ROI 1.2M (findings_B 3.1)
        "B/claim/5": "contradicted",                 # "The renewal is a formality" (3.5)
        "K/claim/f-k_keen_15x": "contradicted",      # "Everyone here is excited" vs a dock that never flew
        "H/claim/2": "contradicted",                 # "Saves about 400,000 a year"
    }
    for cid, v in expected.items():
        if cid.split("/")[0] in engine["accounts"]:
            assert verdict.get(cid) == v, f"{cid}: {verdict.get(cid)}"
    for A, acct in engine["accounts"].items():
        if acct["claims"]:
            assert any(c["verdict"] == "contradicted" for c in acct["claims"]), f"no contradiction found for {A}"


def test_verified_facts_are_supported(state):
    _, _, engine = state
    for acct in engine["accounts"].values():
        for c in acct["claims"]:
            if c.get("fact_status") == "verified" and not c["test"] and not c["evidence_against"]:
                assert c["verdict"] == "supported", c["claim_id"]


def test_a_signal_with_new_flight_data_flips_a_verdict():
    raw = load_raw()
    if "get_new_account_K" not in raw:
        pytest.skip("needs account K")
    before = evaluate(assemble(raw), "2026-11-30", load_rules())
    claim = lambda e: next(c for c in e["accounts"]["K"]["claims"] if c["claim_id"] == "K/claim/2")  # "It has not flown."
    assert claim(before)["verdict"] == "supported"

    raw = copy.deepcopy(raw)
    raw["get_signals"]["signals"].append({
        "id": "test_first_flight", "account": "K", "dated": "2026-12-01", "title": "Kestrel dock flies", "summary": "First patrols.",
        "records": [{"kind": "usage", "path": "K/usage_weekly.csv", "note": "Rows to add to the account's weekly usage.",
                     "text": "week_start,dock_id,flights,days_online\n2026-11-02,KE-D01,3,7\n2026-11-09,KE-D01,5,7\n"}],
    })
    after = evaluate(assemble(raw), "2026-12-01", load_rules())
    c = claim(after)
    assert c["verdict"] == "contradicted"
    assert "K/test_first_flight" in c["evidence_against"], "the signal's rows are cited as the evidence"


def test_claims_and_metric_trace_api(client):
    r = client.get("/api/claims/A")
    assert r.status_code == 200 and r.json()["claims"]
    assert client.get("/api/claims/ZZ").status_code == 404
    m = client.get("/api/metrics/B/docks.stopped_flying").json()
    assert m["definition"] and m["value"]
    assert all(row["row"] is not None for row in m["rows"])
    assert client.get("/api/metrics/B/no.such.metric").status_code == 404

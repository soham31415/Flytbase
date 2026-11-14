"""Step 3c: the five answers per account."""

import json
from datetime import date

import pytest

from app.engine.answers import deadline_dates, Ctx
from app.engine.run import full_state, load_rules
from app.ids import build as build_ids
from app.world import assemble, in_game_today
from tests.conftest import RAW_DIR, needs_snapshots

pytestmark = needs_snapshots


@pytest.fixture(scope="module")
def state():
    raw = {p.stem: json.loads(p.read_text(encoding="utf-8")) for p in RAW_DIR.glob("*.json") if not p.stem.startswith("_")}
    world = assemble(raw)
    as_of, _ = in_game_today(world)
    anchors = {a["id"]: as_of for a in world["accounts"] if (a.get("sheet_source") or "").startswith("get_new_account_")}
    return world, as_of, anchors, full_state(world, build_ids(world), as_of, anchors)


def test_every_account_has_all_five_answers(state):
    world, _, _, engine = state
    for acct in world["accounts"]:
        a = engine["answers"][acct["id"]]
        assert a["q1"]["answer"] and a["q2"]["answer"] and a["q3"]["moves"] and a["q4"]["risks"] and a["q5"]["gaps"], acct["id"]


def test_nothing_was_dropped_and_every_item_has_sources(state):
    _, _, _, engine = state
    assert engine["dropped"] == []
    for a in engine["answers"].values():
        for item in a["q1"]["deals"] + a["q3"]["moves"] + a["q4"]["risks"] + a["q5"]["gaps"]:
            assert item["sources"], item["text"]


def test_moves_are_ranked_owned_and_dated_before_their_deadline(state):
    world, as_of, anchors, engine = state
    today = date.fromisoformat(as_of)
    rules = load_rules()
    for acct in world["accounts"]:
        A = acct["id"]
        ctx = Ctx(world, A, acct, engine["accounts"][A], as_of, rules, {}, anchors.get(A))
        deadlines = {}
        for x in deadline_dates(ctx):
            if x["deal"] and x["date"] and x["date"] > today:
                deadlines[x["deal"]] = min(deadlines.get(x["deal"], x["date"]), x["date"])
        moves = engine["answers"][A]["q3"]["moves"]
        assert [m["rank"] for m in moves] == list(range(1, len(moves) + 1))
        for m in moves:
            due = date.fromisoformat(m["due"])
            assert m["owner"] and m["deal_id"], m
            assert due >= today, m
            if m["deal_id"] in deadlines:
                assert due < deadlines[m["deal_id"]], (m["text"], m["due"], deadlines[m["deal_id"]])


def test_book_follows_the_engine_decisions(state):
    _, _, _, engine = state
    deals = {x["deal_id"]: x for a in engine["answers"].values() for x in a["q1"]["deals"]}
    assert deals["A/a_renewal"]["backed"] and deals["A/a_renewal"]["value"] == 350000  # full worth, not prorated
    assert not deals["B/b_sister"]["backed"]  # its worth is a contradicted claim
    a = engine["answers"]["A"]["q1"]
    assert a["agreed_no_order"] and all(x["deal_id"] is None for x in a["agreed_no_order"])
    assert a["backed_total"] == sum(x["value"] for x in a["deals"] if x["backed"])  # won offers stay out of totals
    risks = engine["answers"]["A"]["q4"]["risks"]
    assert any(r["kind"] == "idle_docks" for r in risks), "the part tied to non-flying docks is a Q4 risk"


def test_inferences_are_gaps_never_facts(state):
    _, _, _, engine = state
    for a in engine["answers"].values():
        for g in a["q5"]["gaps"]:
            if g["text"].startswith("Confirm:"):
                assert g["status"] == "inference"


def test_state_api(client):
    s = client.get("/api/state").json()
    assert s["as_of"] and set(s["accounts"]) >= {"A", "B"}
    assert s["dropped"] == []

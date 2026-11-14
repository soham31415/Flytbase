"""Step 3a: the store loaded from data/raw must match data/dump/index.md exactly."""

import json
from urllib.parse import quote

from app.store import Store
from tests.conftest import RAW_DIR, index_md_counts, index_md_keys, needs_snapshots

pytestmark = needs_snapshots


def test_import_is_idempotent(tmp_path):
    store = Store(tmp_path / "crm.db")
    first = store.import_snapshots(RAW_DIR)
    assert first > 0
    assert store.import_snapshots(RAW_DIR) == 0
    store.rebuild()
    counts = store.counts()
    store.rebuild()
    assert store.counts() == counts, "rebuild must be idempotent"


def test_id_index_matches_index_md(tmp_path):
    store = Store(tmp_path / "crm.db")
    store.import_snapshots(RAW_DIR)
    store.rebuild()
    keys = {r["key"] for r in store.query("SELECT key FROM id_index")}
    expected = index_md_keys()
    assert keys == expected, (
        f"store vs index.md differ (re-run scripts/dump.py?): only in store {sorted(keys - expected)[:10]}, "
        f"only in index.md {sorted(expected - keys)[:10]}"
    )


def test_counts_match_index_md(tmp_path):
    store = Store(tmp_path / "crm.db")
    store.import_snapshots(RAW_DIR)
    store.rebuild()
    q = store.query
    by_account = lambda table: {r["account"]: r["n"] for r in q(f"SELECT account, COUNT(*) n FROM {table} GROUP BY account")}
    stakeholders = {r["account"]: r["n"] for r in q("SELECT account, COUNT(*) n FROM people WHERE origin = 'stakeholder' GROUP BY account")}
    tables = {"stakeholders": stakeholders, "facts": by_account("facts"), "opportunities": by_account("opportunities"),
              "offers": by_account("offers"), "files": by_account("files"), "signals": by_account("signals")}
    paperwork = {(r["account"], r["kind"]): r["n"] for r in q("SELECT account, kind, COUNT(*) n FROM paperwork GROUP BY account, kind")}
    rows = {r["paperwork_key"]: r["n"] for r in q("SELECT paperwork_key, COUNT(*) n FROM paperwork_rows GROUP BY paperwork_key")}
    shared = {"get_flytbase_team.people": "team", "get_feature_requests.requests": "feature_requests", "list_notes.notes": "notes"}

    checked = 0
    for (account, entity), n in index_md_counts().items():
        if entity in tables:
            got = tables[entity].get(account, 0)
        elif entity.startswith("facts["):
            got = len(q("SELECT 1 FROM facts WHERE account = ? AND status = ?", (account, entity[6:-1])))
        elif entity.startswith("opportunities["):
            got = len(q("SELECT 1 FROM opportunities WHERE account = ? AND stage = ?", (account, entity[14:-1])))
        elif entity.startswith("paperwork:"):
            got = paperwork.get((account, entity[len("paperwork:"):]), 0)
        elif entity.startswith("rows in "):
            got = rows.get(f"{account}/{entity[len('rows in '):]}", 0)
        elif entity in shared:
            got = store.counts()[shared[entity]]
        else:
            continue  # deadlines: index.md counts deadlines[]; the store also keys opportunity deadlines
        assert got == n, f"{account} {entity}: store {got}, index.md {n}"
        checked += 1
    assert checked > 50


def test_every_index_md_id_resolves(client):
    for key in sorted(index_md_keys()):
        r = client.get(f"/api/record/{quote(key, safe='/')}")
        assert r.status_code == 200, key
        assert r.json()["key"] == key


def test_record_shapes(client):
    fact = client.get("/api/record/A/km_usage_1").json()
    assert fact["kind"] == "fact" and fact["record"]["status"] == "verified"
    deadline = client.get("/api/record/" + quote("B/b_idle#deadline", safe="/")).json()
    assert deadline["kind"] == "deadline" and deadline["record"]["opportunity"] == "B/b_idle"
    paper = client.get("/api/record/A/emails/2026-08-11_karen_batteries.txt").json()
    assert paper["kind"] == "paperwork:email" and "batteries" in paper["record"]["text"]
    assert client.get("/api/record/A/does_not_exist").status_code == 404
    assert client.get("/api/record/A/(none)").status_code == 404, "placeholders are never ids"


def test_signal_rows_keep_their_source(tmp_path):
    store = Store(tmp_path / "crm.db")
    store.import_snapshots(RAW_DIR)
    store.rebuild()
    sources = {r["source"] for r in store.query("SELECT source FROM paperwork_rows WHERE paperwork_key = 'B/usage_weekly.csv'")}
    assert "get_all_paperwork_B" in sources
    signal_ids = {f"{s['account']}/{s['id']}" for s in json.loads((RAW_DIR / "get_signals.json").read_text(encoding="utf-8"))["signals"]}
    if "B/usage_drop" in signal_ids:
        assert "B/usage_drop" in sources
    if "A/urgent_request" in signal_ids:
        row = store.record("A/FB-11206")
        assert row["record"]["source"] == "A/urgent_request"


def test_names_resolve_and_today_moves_with_signals(client):
    h = client.get("/api/health").json()
    assert h["unresolved"]["said_by"] == 0 and h["unresolved"]["pitch_to"] == 0 and h["unresolved"]["evidence"] == 0
    latest_signal = max(s["dated"] for s in json.loads((RAW_DIR / "get_signals.json").read_text(encoding="utf-8"))["signals"])
    assert h["as_of"] >= latest_signal
    assert {"A", "B"} <= set(h["accounts"])


def test_reload_picks_up_new_snapshots(monkeypatch, tmp_path):
    import shutil

    from tests.conftest import make_client

    snap = tmp_path / "snap"
    shutil.copytree(RAW_DIR, snap, ignore=shutil.ignore_patterns("_history", "_http"))
    with make_client(monkeypatch, tmp_path / "db", snap) as c:
        before = c.get("/api/health").json()["counts"]["raw_payloads"]
        signals = json.loads((snap / "get_signals.json").read_text(encoding="utf-8"))
        signals["signals"].append({"id": "test_new", "account": "A", "dated": "2099-01-01", "title": "t", "summary": "s", "records": []})
        (snap / "get_signals.json").write_text(json.dumps(signals), encoding="utf-8")
        r = c.post("/api/reload").json()
        assert r["snapshots_added"] == 1 and r["as_of"] == "2099-01-01"
        assert c.get("/api/record/A/test_new").status_code == 200
        assert c.get("/api/health").json()["counts"]["raw_payloads"] == before + 1

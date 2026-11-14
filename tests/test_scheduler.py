"""Scheduler tests with a mocked MCP client. No real calls are ever made."""

import copy
import json

import pytest

import mcp_client
from app import scheduler as sched_mod
from app.scheduler import Scheduler, desired_notes, keys_in_notes
from tests.conftest import RAW_DIR, make_client, needs_snapshots


class FakeClock:
    def __init__(self):
        self.t = 1_000_000.0

    def __call__(self):
        return self.t


class FakeMcp:
    """Answers from a dict of tool -> payload; records every call."""

    def __init__(self, answers):
        self.answers, self.calls = answers, []

    def __call__(self, tool, args):
        self.calls.append((tool, args))
        if tool not in self.answers:
            raise mcp_client.McpError(f"no fake answer for {tool}")
        a = self.answers[tool]
        if isinstance(a, Exception):
            raise a
        return copy.deepcopy(a)


def load(name):
    return json.loads((RAW_DIR / f"{name}.json").read_text(encoding="utf-8"))


@pytest.fixture
def app_and_store(monkeypatch, tmp_path):
    with make_client(monkeypatch, tmp_path, RAW_DIR) as c:
        from app.main import refresh_state
        from app.config import get_settings

        app = c.app
        store, settings = app.state.store, get_settings()
        assert app.state.scheduler is None  # no token in tests -> no live scheduler
        yield app, store, lambda: refresh_state(app, store, settings), settings


def make(app_and_store, answers, writes=False, monkeypatch=None):
    app, store, refresh, settings = app_and_store
    if monkeypatch:
        monkeypatch.setenv("WRITE_NOTES", "true" if writes else "false")
    clock, fake = FakeClock(), FakeMcp(answers)
    return Scheduler(app, store, settings, refresh, call=fake, clock=clock), fake, clock


@needs_snapshots
def test_new_signal_queues_followup_and_recomputes(app_and_store, monkeypatch):
    signals = load("get_signals")
    new = copy.deepcopy(signals)
    new["signals"].append({"id": "test_signal", "account": "B", "dated": "2026-12-02", "title": "Test", "summary": "x",
                           "records": []})
    s, fake, clock = make(app_and_store, {"get_signals": new}, monkeypatch=monkeypatch)
    before = app_and_store[0].state.engine
    job = s.next_job()
    assert job[0] == "periodic"
    s.run_job(("periodic", "get_signals", "get_signals", {}))
    assert fake.calls == [("get_signals", {})]
    assert [f[0] for f in s.followups] == ["get_all_paperwork_B"]
    assert app_and_store[0].state.engine is not before  # recomputed
    assert any("test_signal" in c["text"] for c in s.changes)
    assert s.next_job()[:2] == ("followup", "get_all_paperwork_B")


@needs_snapshots
def test_unchanged_payload_does_not_recompute(app_and_store, monkeypatch):
    s, fake, _ = make(app_and_store, {"get_signals": load("get_signals")}, monkeypatch=monkeypatch)
    before = app_and_store[0].state.engine
    s.run_job(("periodic", "get_signals", "get_signals", {}))
    assert app_and_store[0].state.engine is before and not s.followups


@needs_snapshots
def test_gap_is_persisted_and_rate_limit_backs_off(app_and_store, monkeypatch):
    s, fake, clock = make(app_and_store, {"get_signals": mcp_client.McpError("Rate limit exceeded")}, monkeypatch=monkeypatch)
    s.run_job(("periodic", "get_signals", "get_signals", {}))
    assert s.next_call_at() == clock.t + sched_mod.RATE_LIMIT_BACKOFF_S
    # A restarted scheduler reads last_call_at back from the store.
    s2, _, _ = make(app_and_store, {}, monkeypatch=monkeypatch)
    assert s2.last_call_at == clock.t


@needs_snapshots
def test_periodic_order_and_spacing(app_and_store, monkeypatch):
    s, fake, clock = make(app_and_store, {t: load(t) for t in ("get_signals", "whoami", "get_my_accounts")},
                          monkeypatch=monkeypatch)
    seen = []
    for _ in range(3):
        job = s.next_job()
        seen.append(job[1])
        s.run_job(job)
        clock.t += sched_mod.GAP_S
    assert sorted(seen) == ["get_my_accounts", "get_signals", "whoami"]
    # 3 calls x 62s = 186s: only get_signals (every 180s) is due again, not whoami or get_my_accounts.
    assert s.next_job()[1] == "get_signals"


@needs_snapshots
def test_writes_off_by_default(app_and_store, monkeypatch):
    s, _, _ = make(app_and_store, {}, writes=False, monkeypatch=monkeypatch)
    s.plan_writes()
    assert s.writes == []


@needs_snapshots
def test_write_back_is_deduped_and_cites_sources(app_and_store, monkeypatch):
    s, fake, clock = make(app_and_store, {"list_notes": {"notes": []}, "write_note": {"ok": True}}, writes=True,
                          monkeypatch=monkeypatch)
    s.plan_writes()
    assert s.writes and all("Sources:" in n["text"] and f"[crm:{n['key']}]" in n["text"] for n in s.writes)
    total = len(s.writes)

    job = s.next_job()
    assert job[1] == "list_notes"  # read what's on the server before the first write
    s.run_job(job)
    clock.t += sched_mod.GAP_S
    job = s.next_job()
    assert job[0] == "write"
    s.run_job(job)
    assert fake.calls[-1][0] == "write_note" and len(s.writes) == total - 1
    clock.t += sched_mod.GAP_S
    assert s.next_job() is None or s.next_job()[0] != "write"  # at most one write per 5 min

    s.plan_writes()  # the written note is never queued again
    assert len(s.writes) == total - 1


@needs_snapshots
def test_notes_already_on_server_are_skipped(app_and_store, monkeypatch):
    s, _, _ = make(app_and_store, {}, writes=True, monkeypatch=monkeypatch)
    answers = app_and_store[0].state.engine["answers"]
    first = desired_notes(answers)[0]
    s.store.add_payload("list_notes", {"notes": []})
    s.run_job_payload = None
    fake = FakeMcp({"list_notes": {"notes": [{"text": f"old note [crm:{first['key']}]"}]}})
    s.call = fake
    s.plan_writes()
    s.run_job(("read", "list_notes", "list_notes", {}))
    assert first["key"] not in {n["key"] for n in s.writes}
    assert keys_in_notes({"notes": [{"text": "a [crm:abc123] b"}]}) == {"abc123"}


def test_bootstrap_on_empty_store(monkeypatch, tmp_path):
    (tmp_path / "empty").mkdir()
    with make_client(monkeypatch, tmp_path, tmp_path / "empty") as c:
        from app.config import get_settings

        s = Scheduler(c.app, c.app.state.store, get_settings(), lambda: None, call=FakeMcp({}), clock=FakeClock())
        job = s.next_job()
        assert job[0] == "bootstrap" and job[1] == "whoami"
        s.run_job(job)  # fails (no fake answer); after two failures bootstrap moves on
        s.run_job(s.next_job())
        assert s.next_job()[1] != "whoami"


def test_status_never_has_token(monkeypatch, tmp_path):
    (tmp_path / "empty").mkdir()
    with make_client(monkeypatch, tmp_path, tmp_path / "empty") as c:
        from app.config import get_settings

        monkeypatch.setenv("AQ_TOKEN", "tok-should-not-leak")
        err = mcp_client.McpError("bad tok-should-not-leak")
        s = Scheduler(c.app, c.app.state.store, get_settings(), lambda: None, call=FakeMcp({"whoami": err}), clock=FakeClock())
        s.run_job(s.next_job())
        assert "tok-should-not-leak" not in json.dumps(s.status())

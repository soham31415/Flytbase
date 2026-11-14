"""The scheduler: the ONLY code in the app allowed to call the MCP (Step 5).

An asyncio background task, started on app startup only when LIVE_MODE=true and AQ_TOKEN is set.
It makes at most one call per GAP_S seconds (one loop, persisted last_call_at, so a restart still
respects the gap) and picks one job per tick, in this order:

  1. bootstrap: any base source the store has never seen (empty volume)
  2. list_notes once per run, before the first write (so we never repeat a note already written)
  3. one pending write_note (at most one per WRITE_GAP_S, and only if WRITE_NOTES=true)
  4. follow-ups queued by a change (new signal, new account, new file), from app.sync_plan
  5. periodic: get_signals every 3 min, whoami every 10 min, get_my_accounts every 30 min

Every response goes to raw_payloads; a new payload triggers a rebuild and an engine recompute.
"""

import asyncio
import hashlib
import json
import logging
import os
import time
from collections import deque
from datetime import datetime, timezone

import mcp_client
from app.sync_plan import drop_id, file_step, followups, new_account_files, new_account_step, paperwork_step

log = logging.getLogger("scheduler")

GAP_S = 62  # seconds between any two calls (the token allows 1/min, writes included)
RATE_LIMIT_BACKOFF_S = 90
WRITE_GAP_S = 300  # writes don't starve signal polling
PERIODIC = [  # (source, tool, every_s)
    ("get_signals", "get_signals", 180),
    ("whoami", "whoami", 600),
    ("get_my_accounts", "get_my_accounts", 1800),
]
SHARED_SOURCES = ["whoami", "get_data_format", "get_my_accounts", "get_flytbase_team", "get_feature_requests",
                  "get_release_notes", "list_notes", "get_signals"]
MOVES_PER_ACCOUNT = 3
RISKS_PER_ACCOUNT = 3  # red risks only


def _iso(ts):
    return datetime.fromtimestamp(ts, timezone.utc).isoformat(timespec="seconds") if ts else None


def note_key(account, kind, item):
    """Stable key for a note: the same move or risk keeps its key even when its text's numbers change."""
    basis = json.dumps([account, kind, item.get("kind"), item.get("deal_id"), sorted(item.get("sources") or [])])
    return hashlib.sha256(basis.encode()).hexdigest()[:12]


def desired_notes(answers):
    """The notes we want on the server: top moves (next_step) and top red risks (risk) per account."""
    out = []
    for account, a in sorted(answers.items()):
        moves = (a.get("q3") or {}).get("moves") or []
        risks = [r for r in (a.get("q4") or {}).get("risks") or [] if r.get("severity") == "red"]
        for item in moves[:MOVES_PER_ACCOUNT]:
            owner = item.get("owner") or "unassigned"
            text = (f"Move #{item.get('rank')}: {item['text']}. Owner: {owner}. Due: {item.get('due') or 'not set'}. "
                    f"Moves deal: {item.get('deal_id') or 'none'}.")
            out.append(_note(account, "next_step", item, f"Move #{item.get('rank')}: {item['text']}"[:120], text))
        for item in risks[:RISKS_PER_ACCOUNT]:
            text = f"{item['text']}. Turns bad on: {item.get('turns_bad_on') or 'unknown'}. Deal: {item.get('deal_id') or 'none'}."
            out.append(_note(account, "risk", item, f"Risk: {item['text']}"[:120], text))
    return out


def _note(account, kind, item, title, text):
    key = note_key(account, kind, item)
    sources = ", ".join(item.get("sources") or [])
    return {"key": key, "account": account, "kind": kind, "title": title,
            "text": f"{text} Sources: {sources}. [crm:{key}]"}


def keys_in_notes(payload):
    """Note keys already on the server (we tag every note we write with [crm:<key>])."""
    text = json.dumps(payload, ensure_ascii=False)
    keys, start = set(), 0
    while (i := text.find("[crm:", start)) != -1:
        end = text.find("]", i)
        if end == -1:
            break
        keys.add(text[i + 5:end])
        start = end
    return keys


class Scheduler:
    def __init__(self, app, store, settings, refresh, call=None, clock=time.time):
        self.app, self.store, self.settings, self.refresh = app, store, settings, refresh
        self.call = call or mcp_client.call_tool
        self.clock = clock
        self.write_enabled = os.environ.get("WRITE_NOTES", "false").strip().lower() in ("1", "true", "yes", "on")
        self.followups = []  # [(source, tool, args)]
        self.writes = []  # [note dict]
        self.recent = deque(maxlen=20)  # recent calls
        self.changes = deque(maxlen=50)  # what changed, newest first
        self.last_fetch = {}  # source -> time fetched in this run
        self.notes_checked = False
        self.failures = {}  # source -> failed calls in this run (bootstrap gives up after 2)
        self.last_write_at = 0.0
        self.not_before = 0.0  # set by a rate-limit back-off
        meta = store.meta()
        self.last_call_at = float(meta.get("last_call_at") or 0)
        self.sent = set(json.loads(meta.get("notes_sent") or "[]"))
        self.task = None

    # ---------- job selection ----------

    def missing_sources(self):
        """Base sources the store has never seen: the bootstrap pull (and any account or file added later)."""
        latest = self.store.latest_payloads()
        have = set(latest)
        steps = [(s, s, {}) for s in SHARED_SOURCES]
        export = latest.get("get_my_accounts") or {}
        for a in export.get("accounts", []) if isinstance(export, dict) else []:
            if a.get("id"):
                steps.append(paperwork_step(a["id"]))
        for f in export.get("files", []) if isinstance(export, dict) else []:
            if f.get("id"):
                steps.append(file_step(f["id"]))
        who = latest.get("whoami") or {}
        for entry in who.get("new_accounts") or [] if isinstance(who, dict) else []:
            acct = drop_id(entry)
            if acct:
                steps += [new_account_step(acct), paperwork_step(acct)]
                if new_account_step(acct)[0] in latest:
                    steps += new_account_files(acct, latest[new_account_step(acct)[0]])
        return [s for s in steps if s[0] not in have and self.failures.get(s[0], 0) < 2]

    def next_job(self):
        """The one job to run now, as (kind, source, tool, args), or None."""
        now = self.clock()
        missing = self.missing_sources()
        if missing:
            return ("bootstrap",) + missing[0]
        if self.write_enabled and self.writes and not self.notes_checked:
            return ("read", "list_notes", "list_notes", {})
        if self.write_enabled and self.writes and now - self.last_write_at >= WRITE_GAP_S:
            note = self.writes[0]
            return ("write", f"write_note:{note['key']}", "write_note",
                    {"text": note["text"], "account": note["account"], "kind": note["kind"], "title": note["title"]})
        if self.followups:
            return ("followup",) + self.followups[0]
        due = [(now - self.last_fetch.get(src, 0) - every, src, tool) for src, tool, every in PERIODIC
               if now - self.last_fetch.get(src, 0) >= every]
        if due:
            _, src, tool = max(due)  # the most overdue first
            return ("periodic", src, tool, {})
        return None

    def next_call_at(self):
        return max(self.last_call_at + GAP_S, self.not_before)

    # ---------- running one job ----------

    def mark_call(self):
        self.last_call_at = self.clock()
        self.store.set_meta("last_call_at", str(self.last_call_at))

    def run_job(self, job):
        """Make the one MCP call for `job` and apply the result. Synchronous (run it in a thread)."""
        kind, source, tool, args = job
        self.mark_call()
        try:
            payload = self.call(tool, args)
        except mcp_client.McpError as e:
            self.recent.appendleft({"at": _iso(self.clock()), "tool": tool, "source": source, "ok": False,
                                    "error": mcp_client.redact(str(e))[:200]})
            if e.is_rate_limit:
                self.not_before = self.clock() + RATE_LIMIT_BACKOFF_S
                return False
            self.failures[source] = self.failures.get(source, 0) + 1
            if kind == "followup":
                self.followups.pop(0)  # don't retry a bad follow-up forever
            elif kind == "write":
                self.last_write_at = self.clock()  # retry, but not before the next write slot
                if self.failures[source] >= 3:
                    self.writes.pop(0)
            elif kind == "periodic":
                self.last_fetch[source] = self.clock()
            return False
        self.recent.appendleft({"at": _iso(self.clock()), "tool": tool, "source": source, "ok": True})

        if kind == "write":
            self.writes.pop(0)
            self.last_write_at = self.clock()
            self.sent.add(job[1].split(":", 1)[1])
            self.store.set_meta("notes_sent", json.dumps(sorted(self.sent)))
            self.changes.appendleft({"at": _iso(self.clock()), "text": f"wrote note {args['title']!r} ({args['account']})"})
            return True

        if kind == "followup":
            self.followups.pop(0)
        self.last_fetch[source] = self.clock()
        old = self.store.latest_payloads().get(source)
        is_new = self.store.add_payload(source, payload)
        if source == "list_notes":
            self.notes_checked = True
            self.sent |= keys_in_notes(payload)
            self.writes = [n for n in self.writes if n["key"] not in self.sent]
        if not is_new:
            return True

        steps, notes = followups(source, old, payload)
        queued = {s[0] for s in self.followups}
        self.followups += [s for s in steps if s[0] not in queued]
        for n in notes:
            self.changes.appendleft({"at": _iso(self.clock()), "text": n})
        self.recompute()
        return True

    def recompute(self):
        self.refresh()
        self.plan_writes()

    def plan_writes(self):
        """Queue the notes we want and haven't written yet (deduped by key)."""
        if not self.write_enabled:
            return
        answers = self.app.state.engine.get("answers") or {}
        queued = {n["key"] for n in self.writes}
        self.writes += [n for n in desired_notes(answers) if n["key"] not in self.sent and n["key"] not in queued]

    def sync_now(self):
        """The UI's "sync now": re-check the cheap sources at the next free slot."""
        for src, _, _ in PERIODIC:
            self.last_fetch[src] = 0

    # ---------- the loop ----------

    def start(self):
        self.plan_writes()
        self.task = asyncio.create_task(self.loop())

    async def stop(self):
        if self.task:
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass

    async def loop(self):
        log.info("scheduler started (writes %s)", "on" if self.write_enabled else "off")
        while True:
            wait = self.next_call_at() - self.clock()
            if wait > 0:
                await asyncio.sleep(min(wait, 5))
                continue
            job = self.next_job()
            if job is None:
                await asyncio.sleep(5)
                continue
            try:
                await asyncio.to_thread(self.run_job, job)
            except Exception:  # never let one bad payload kill the poller
                log.exception("scheduler job %s failed", job[1])
                if job[0] == "followup" and self.followups and self.followups[0][0] == job[1]:
                    self.followups.pop(0)

    def status(self):
        job = self.next_job()
        return {
            "running": bool(self.task and not self.task.done()),
            "writes_enabled": self.write_enabled,
            "next_call_at": _iso(self.next_call_at()),
            "next_job": job and {"kind": job[0], "source": job[1]},
            "queue": {"followups": [s[0] for s in self.followups], "writes": [n["title"] for n in self.writes]},
            "periodic": {src: _iso(self.last_fetch.get(src)) for src, _, _ in PERIODIC},
            "notes_sent": len(self.sent),
            "recent_calls": list(self.recent),
            "changes": list(self.changes),
        }

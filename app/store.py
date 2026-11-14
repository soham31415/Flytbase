"""SQLite store at $DATA_DIR/crm.db.

raw_payloads is the append-only history of every MCP response (one row per distinct payload
per source). Everything else is derived from the latest payload of each source by rebuild(),
using app.world and app.ids, the same code scripts/dump.py uses for data/dump/index.md.
"""

import hashlib
import json
import re
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

from app.ids import build as build_ids, deadline_key
from app.world import assemble, csv_rows, in_game_today

SCHEMA = """
CREATE TABLE IF NOT EXISTS raw_payloads (
    id INTEGER PRIMARY KEY,
    source TEXT NOT NULL,
    account TEXT,
    fetched_at TEXT NOT NULL,
    hash TEXT NOT NULL,
    payload TEXT NOT NULL,
    UNIQUE (source, hash)
);
CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT);

CREATE TABLE IF NOT EXISTS accounts (key TEXT PRIMARY KEY, id TEXT, name TEXT, has_sheet INTEGER,
    official_story TEXT, sheet_source TEXT, drop_json TEXT);
CREATE TABLE IF NOT EXISTS people (key TEXT PRIMARY KEY, account TEXT, id TEXT, name TEXT, role TEXT,
    relationship TEXT, available INTEGER, origin TEXT);
CREATE TABLE IF NOT EXISTS facts (key TEXT PRIMARY KEY, account TEXT, id TEXT, text TEXT, said_by TEXT,
    said_by_key TEXT, status TEXT, verified_by_key TEXT, learned_at_minute REAL);
CREATE TABLE IF NOT EXISTS opportunities (key TEXT PRIMARY KEY, account TEXT, id TEXT, title TEXT, stage TEXT,
    worth_up_to REAL, size TEXT, blocker_text TEXT, blocker_status TEXT, deadline_key TEXT);
CREATE TABLE IF NOT EXISTS opportunity_evidence (opportunity_key TEXT, evidence_key TEXT, resolved INTEGER,
    PRIMARY KEY (opportunity_key, evidence_key));
CREATE TABLE IF NOT EXISTS offers (key TEXT PRIMARY KEY, account TEXT, id TEXT, title TEXT, value REAL,
    pitch_to TEXT, pitch_to_key TEXT, status TEXT);
CREATE TABLE IF NOT EXISTS deadlines (id INTEGER PRIMARY KEY, key TEXT, account TEXT, text TEXT, minute REAL,
    passed INTEGER, opportunity_key TEXT, payload TEXT);
CREATE TABLE IF NOT EXISTS files (key TEXT PRIMARY KEY, account TEXT, id TEXT, title TEXT, path TEXT,
    source TEXT, content TEXT);
CREATE TABLE IF NOT EXISTS paperwork (key TEXT PRIMARY KEY, account TEXT, path TEXT, kind TEXT, source TEXT,
    text TEXT, data TEXT);
CREATE TABLE IF NOT EXISTS paperwork_rows (paperwork_key TEXT, row_no INTEGER, row TEXT, source TEXT,
    PRIMARY KEY (paperwork_key, row_no));
CREATE TABLE IF NOT EXISTS signals (key TEXT PRIMARY KEY, account TEXT, id TEXT, dated TEXT, title TEXT,
    summary TEXT, set_off_at TEXT, payload TEXT);
CREATE TABLE IF NOT EXISTS team (key TEXT PRIMARY KEY, id TEXT, name TEXT, role TEXT);
CREATE TABLE IF NOT EXISTS feature_requests (key TEXT PRIMARY KEY, id TEXT, title TEXT, status TEXT, payload TEXT);
CREATE TABLE IF NOT EXISTS notes (id INTEGER PRIMARY KEY, payload TEXT);
CREATE TABLE IF NOT EXISTS id_index (key TEXT PRIMARY KEY, kind TEXT, account TEXT, label TEXT, source TEXT,
    location TEXT, record TEXT);
"""

DERIVED_TABLES = [
    "accounts", "people", "facts", "opportunities", "opportunity_evidence", "offers", "deadlines", "files",
    "paperwork", "paperwork_rows", "signals", "team", "feature_requests", "notes", "id_index",
]
HISTORY_STAMP = re.compile(r"^(\d{8})T(\d{6})$")


def payload_hash(payload):
    text = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def source_account(source):
    """The account a source belongs to, from its pull name (get_all_paperwork_A -> A)."""
    for prefix in ("get_all_paperwork_", "get_new_account_file_", "get_new_account_"):
        if source.startswith(prefix):
            return source[len(prefix):].split("_")[0]
    return None


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _json(v):
    return None if v is None else json.dumps(v, ensure_ascii=False)


class Store:
    def __init__(self, db_path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        # One connection shared by the app's threads; every access goes through the lock.
        self.conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.lock = threading.RLock()
        with self.lock:
            self.conn.executescript(SCHEMA)

    # ---------- raw payloads ----------

    def add_payload(self, source, payload, fetched_at=None):
        """Store one MCP response. Returns True if it is new.

        An identical payload is stored once; seeing it again only moves its fetched_at forward,
        so a source that reverts to an earlier version still reads as its latest.
        """
        h = payload_hash(payload)
        with self.lock, self.conn:
            seen = self.conn.execute("SELECT 1 FROM raw_payloads WHERE source = ? AND hash = ?", (source, h)).fetchone()
            self.conn.execute(
                "INSERT INTO raw_payloads (source, account, fetched_at, hash, payload) VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT (source, hash) DO UPDATE SET fetched_at = MAX(fetched_at, excluded.fetched_at)",
                (source, source_account(source), fetched_at or now_iso(), h, _json(payload)),
            )
            return seen is None

    def import_snapshots(self, raw_dir):
        """Load data/raw/*.json and data/raw/_history/<source>/*.json. Idempotent. Returns how many were new."""
        raw_dir = Path(raw_dir)
        if not raw_dir.is_dir():
            return 0
        added = 0
        for path in sorted((raw_dir / "_history").glob("*/*.json")):
            m = HISTORY_STAMP.match(path.stem)
            # pull.py stamps history files with local time; store UTC like everything else.
            fetched = (datetime.strptime("".join(m.groups()), "%Y%m%d%H%M%S").astimezone(timezone.utc).isoformat(timespec="seconds")
                       if m else _mtime(path))
            added += self.add_payload(path.parent.name, _load(path), fetched)
        for path in sorted(raw_dir.glob("*.json")):
            if not path.stem.startswith("_"):
                added += self.add_payload(path.stem, _load(path), _mtime(path))
        return added

    def latest_payloads(self):
        with self.lock:
            rows = self.conn.execute(
                "SELECT source, payload FROM raw_payloads r WHERE id = "
                "(SELECT id FROM raw_payloads WHERE source = r.source ORDER BY fetched_at DESC, id DESC LIMIT 1)"
            ).fetchall()
        return {r["source"]: json.loads(r["payload"]) for r in rows}

    # ---------- derived tables ----------

    def rebuild(self, as_of_override=None):
        """Re-derive every table from the latest payloads. Idempotent."""
        raw = self.latest_payloads()
        world = assemble(raw)
        idx = build_ids(world)
        as_of, as_of_source = in_game_today(world, as_of_override)

        with self.lock, self.conn:
            for table in DERIVED_TABLES:
                self.conn.execute(f"DELETE FROM {table}")
            self._write_accounts(world, idx)
            self._write_paperwork(world)
            self._write_shared(world)
            self.conn.executemany(
                "INSERT INTO id_index VALUES (?, ?, ?, ?, ?, ?, ?)",
                [(e["key"], e["kind"], e["scope"], e["label"], e["file"], e["where"], _json(e["record"])) for e in idx.entries.values()],
            )
            for k, v in {"as_of": as_of, "as_of_source": as_of_source, "rebuilt_at": now_iso()}.items():
                self.conn.execute("INSERT OR REPLACE INTO meta VALUES (?, ?)", (k, v))
        return world, idx

    def _write_accounts(self, world, idx):
        ex = self.conn.execute
        for a in world["accounts"]:
            A = a["id"]
            ex("INSERT INTO accounts VALUES (?, ?, ?, ?, ?, ?, ?)",
               (f"{A}/{A}", A, a.get("name"), int(a["has_sheet"]), a.get("official_story"), a.get("sheet_source"), _json(a.get("drop"))))
            names = {}
            for s in a.get("stakeholders", []):
                names[s["name"]] = f"{A}/{s['id']}"
                ex("INSERT INTO people VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                   (f"{A}/{s['id']}", A, s["id"], s["name"], s.get("role"), s.get("relationship"), _bool(s.get("available")), "stakeholder"))
            for f in a.get("facts", []):
                vb = f.get("verified_by")
                ex("INSERT INTO facts VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                   (f"{A}/{f['id']}", A, f["id"], f.get("text"), f.get("said_by"), names.get(f.get("said_by")), f.get("status"),
                    f"{A}/{vb}" if vb and idx.get(A, vb) else None, f.get("learned_at_minute")))
            for o in a.get("opportunities", []):
                key = f"{A}/{o['id']}"
                blocker = o.get("blocker") or {}
                ex("INSERT INTO opportunities VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                   (key, A, o["id"], o.get("title"), o.get("stage"), o.get("worth_up_to"), o.get("size"),
                    blocker.get("text"), blocker.get("status"), f"{A}/{deadline_key(o['id'])}" if o.get("deadline") else None))
                for e in o.get("evidence", []):
                    ex("INSERT OR IGNORE INTO opportunity_evidence VALUES (?, ?, ?)", (key, f"{A}/{e}", int(idx.get(A, e) is not None)))
                if o.get("deadline"):
                    d = o["deadline"]
                    ex("INSERT INTO deadlines (key, account, text, minute, passed, opportunity_key, payload) VALUES (?, ?, ?, ?, ?, ?, ?)",
                       (f"{A}/{deadline_key(o['id'])}", A, d.get("text"), d.get("minute"), _bool(d.get("passed")), key, _json(d)))
            # deadlines[] entries are cited through the opportunity with the same text; unmatched ones get no key.
            opp_texts = {(o.get("deadline") or {}).get("text") for o in a.get("opportunities", []) if o.get("deadline")}
            for d in a.get("deadlines", []):
                if d.get("text") not in opp_texts:
                    ex("INSERT INTO deadlines (key, account, text, minute, passed, opportunity_key, payload) VALUES (NULL, ?, ?, ?, ?, NULL, ?)",
                       (A, d.get("text"), d.get("minute"), _bool(d.get("passed")), _json(d)))
            for o in a.get("offers", []):
                ex("INSERT INTO offers VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                   (f"{A}/{o['id']}", A, o["id"], o.get("title"), o.get("value"), o.get("pitch_to"), names.get(o.get("pitch_to")), o.get("status")))
        for f in world["files"]:
            content = f.get("content")
            text = content.get("text") if isinstance(content, dict) else content
            ex("INSERT OR IGNORE INTO files VALUES (?, ?, ?, ?, ?, ?, ?)",
               (f"{f.get('account')}/{f['id']}", f.get("account"), f["id"], f.get("title"), f.get("path"), f.get("source"), text))

    def _write_paperwork(self, world):
        ex = self.conn.execute
        for A, records in world["paperwork"].items():
            for r in records:
                key = f"{A}/{r['path']}"
                cur = ex("INSERT OR IGNORE INTO paperwork VALUES (?, ?, ?, ?, ?, ?, ?)",
                         (key, A, r["path"], r.get("kind"), r.get("source"), r.get("text"), _json(r.get("data"))))
                if cur.rowcount != 1 or not isinstance(r.get("text"), str) or not r["path"].endswith(".csv"):
                    continue
                rows = r.get("rows") or [{"row": row, "source": r["source"]} for row in csv_rows(r["text"])]
                self.conn.executemany("INSERT INTO paperwork_rows VALUES (?, ?, ?, ?)",
                                      [(key, i, _json(x["row"]), x["source"]) for i, x in enumerate(rows)])
            # People known only from profile.json (e.g. a drop account with no sheet).
            for r in records:
                if r.get("kind") == "profile" and isinstance(r.get("data"), dict):
                    for c in r["data"].get("contacts_on_file", []):
                        if c.get("id"):
                            ex("INSERT OR IGNORE INTO people VALUES (?, ?, ?, ?, ?, NULL, NULL, 'contact')",
                               (f"{A}/{c['id']}", A, c["id"], c.get("name"), c.get("role")))

    def _write_shared(self, world):
        ex = self.conn.execute
        for s in world["signals"]:
            ex("INSERT OR IGNORE INTO signals VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
               (f"{s.get('account')}/{s['id']}", s.get("account"), s["id"], s.get("dated"), s.get("title"), s.get("summary"), s.get("set_off_at"), _json(s)))
        for p in world["team"]:
            ex("INSERT OR IGNORE INTO team VALUES (?, ?, ?, ?)", (f"team/{p['id']}", p["id"], p.get("name"), p.get("role")))
        for fr in world["feature_requests"]:
            ex("INSERT OR IGNORE INTO feature_requests VALUES (?, ?, ?, ?, ?)",
               (f"feature_requests/{fr['id']}", fr["id"], fr.get("title"), fr.get("status"), _json(fr)))
        for n in world["notes"]:
            ex("INSERT INTO notes (payload) VALUES (?)", (_json(n),))

    def anchors(self, world, as_of):
        """In-game date each drop account's sheet first appeared: its deadline minutes count from there.

        Stored once in meta, so later signals moving today forward don't move those deadlines.
        """
        out = {}
        with self.lock, self.conn:
            for a in world["accounts"]:
                if (a.get("sheet_source") or "").startswith("get_new_account_"):
                    key = f"anchor:{a['id']}"
                    row = self.conn.execute("SELECT v FROM meta WHERE k = ?", (key,)).fetchone()
                    if row is None and as_of:
                        self.conn.execute("INSERT INTO meta VALUES (?, ?)", (key, as_of))
                    out[a["id"]] = row["v"] if row else as_of
        return out

    # ---------- reads ----------

    def record(self, key):
        with self.lock:
            row = self.conn.execute("SELECT * FROM id_index WHERE key = ?", (key,)).fetchone()
        if row is None:
            return None
        return {"key": row["key"], "kind": row["kind"], "account": row["account"], "label": row["label"],
                "source": row["source"], "location": row["location"], "record": json.loads(row["record"]) if row["record"] else None}

    def counts(self):
        with self.lock:
            return {t: self.conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in ["raw_payloads"] + DERIVED_TABLES}

    def meta(self):
        with self.lock:
            out = {r["k"]: r["v"] for r in self.conn.execute("SELECT k, v FROM meta")}
            out["last_sync"] = self.conn.execute("SELECT MAX(fetched_at) FROM raw_payloads").fetchone()[0]
            out["accounts"] = [r[0] for r in self.conn.execute("SELECT id FROM accounts ORDER BY id")]
            out["unresolved"] = {
                "said_by": self.conn.execute("SELECT COUNT(*) FROM facts WHERE said_by IS NOT NULL AND said_by_key IS NULL").fetchone()[0],
                "pitch_to": self.conn.execute("SELECT COUNT(*) FROM offers WHERE pitch_to IS NOT NULL AND pitch_to_key IS NULL").fetchone()[0],
                "evidence": self.conn.execute("SELECT COUNT(*) FROM opportunity_evidence WHERE resolved = 0").fetchone()[0],
                "deadlines_without_opportunity": self.conn.execute("SELECT COUNT(*) FROM deadlines WHERE key IS NULL").fetchone()[0],
            }
        return out

    def set_meta(self, k, v):
        with self.lock, self.conn:
            self.conn.execute("INSERT OR REPLACE INTO meta VALUES (?, ?)", (k, v))

    def query(self, sql, params=()):
        with self.lock:
            return [dict(r) for r in self.conn.execute(sql, params).fetchall()]


def _bool(v):
    return None if v is None else int(bool(v))


def _load(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _mtime(path):
    return datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat(timespec="seconds")

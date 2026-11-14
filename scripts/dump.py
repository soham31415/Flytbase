"""Turn data/raw/*.json into readable markdown in data/dump/. Makes NO network calls.

Built on the real export schema (get_data_format -> data/dump/schema.md):
accounts[] with stakeholders, facts, opportunities, offers, deadlines; top-level files[];
paperwork records {path, kind, text | data}.

Citable ids are scoped by account. Paperwork records are cited as "<account>/<path>"
(e.g. "A/contract.txt"), the same form the signals use, because the same path exists
in both accounts.

    python scripts/dump.py
    python scripts/dump.py --raw some/dir --out other/dir

Each run also writes changes.md (what changed since the previous dump, from _state.json)
and deletes .md files in the output folder that this run did not produce.
"""

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.ids import build as build_ids, deadline_key  # noqa: E402
from app.world import assemble, csv_rows, is_csv  # noqa: E402
from common import DUMP_DIR, RAW_DIR, load_json  # noqa: E402

LONG_TEXT = 160  # cells longer than this switch a table to one block per record
DATE_IN_PATH = re.compile(r"(\d{4}-\d{2}-\d{2})")

# Paperwork kinds in reading order (unknown kinds go last, alphabetically).
KIND_ORDER = [
    "profile", "deal_history", "contract", "invoices", "calls_index", "call", "email", "mail_log",
    "tickets", "requests", "internal_notes", "users", "devices", "usage", "flights", "device_events",
]

# ---------- generic rendering ----------

def is_scalar(v):
    return v is None or isinstance(v, (str, int, float, bool))


def fmt(v):
    """One-line string for a value."""
    if v is None:
        return ""
    if isinstance(v, str):
        return v
    if isinstance(v, (int, float, bool)):
        return json.dumps(v)
    if isinstance(v, list) and all(is_scalar(x) for x in v):
        return ", ".join(fmt(x) for x in v)
    return json.dumps(v, ensure_ascii=False)


def cell(v):
    return fmt(v).replace("|", "\\|").replace("\r", "").replace("\n", " <br> ")


def table(rows, cols=None):
    if not rows:
        return ["_(none)_", ""]
    if cols is None:
        cols = []
        for r in rows:
            cols += [k for k in r if k not in cols]
    out = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    out += ["| " + " | ".join(cell(r.get(c)) for c in cols) + " |" for r in rows]
    return out + [""]


def code(text, lang="text"):
    return [f"```{lang}", text.rstrip(), "```", ""]


def heading(level, text):
    return [f"{'#' * min(level, 6)} {text}", ""]


def simple_value(v):
    return is_scalar(v) or (isinstance(v, list) and all(is_scalar(x) for x in v))


def render_fields(obj, level):
    out = []
    for k, v in obj.items():
        if isinstance(v, str) and ("\n" in v or len(v) > LONG_TEXT):
            out += [f"- **{k}**:", ""] + code(v.strip())
        elif simple_value(v):
            out.append(f"- **{k}**: {fmt(v)}")
        else:
            out += ["", f"**{k}**:", ""] + render_any(v, level)
    return out + [""]


def render_any(obj, level=3):
    """Render any JSON value as markdown lines. Lists of flat dicts become tables."""
    if isinstance(obj, str):
        return code(obj) if "\n" in obj else [obj, ""]
    if is_scalar(obj):
        return [fmt(obj), ""]
    if isinstance(obj, list):
        if not obj:
            return ["_(empty)_", ""]
        if all(isinstance(x, dict) for x in obj):
            flat = all(simple_value(v) and len(fmt(v)) <= LONG_TEXT for x in obj for v in x.values())
            if flat:
                return table(obj)
            out = []
            for i, x in enumerate(obj):
                label = next((str(x[k]) for k in ("id", "number", "thread", "path", "date") if is_scalar(x.get(k)) and x.get(k)), str(i + 1))
                title = x.get("title") or x.get("subject") or ""
                out += heading(level, f"{label} · {title}" if title and title != label else label)
                out += render_fields(x, level + 1)
            return out
        if all(is_scalar(x) for x in obj):
            return [f"- {fmt(x)}" for x in obj] + [""]
        out = []
        for x in obj:
            out += render_any(x, level)
        return out
    out = render_fields({k: v for k, v in obj.items() if simple_value(v)}, level)
    for k, v in obj.items():
        if not simple_value(v):
            out += heading(level, k) + render_any(v, level + 1)
    return out


# ---------- account sheets ----------

def deadline_opportunity(account, deadline):
    """The id of the opportunity whose deadline.text matches this deadlines[] entry, or None."""
    for o in account.get("opportunities", []):
        if o.get("deadline") and o["deadline"].get("text") == deadline.get("text"):
            return o["id"]
    return None


def by_name(stakeholders):
    return {s["name"]: s["id"] for s in stakeholders}


def render_account(a, files, paperwork, signals, file_contents, idx):
    A = a["id"]
    names = by_name(a.get("stakeholders", []))
    out = [f"# Account {A}: {a.get('name', '')}", ""]

    out += heading(2, "Official story (account sheet, unverified)")
    out += [f"> {a.get('official_story', '_(none)_')}", ""]

    out += heading(2, f"People / stakeholders ({len(a.get('stakeholders', []))})")
    out += table(a.get("stakeholders", []), ["id", "name", "role", "relationship", "available"])

    facts = sorted(a.get("facts", []), key=lambda f: f.get("learned_at_minute", 0))
    out += heading(2, f"Facts ({len(facts)}; " + ", ".join(f"{s} {n}" for s, n in sorted(Counter(f["status"] for f in facts).items())) + ")")
    out += table(
        [{**f, "said_by_id": names.get(f.get("said_by"), "**no stakeholder match**")} for f in facts],
        ["id", "status", "said_by", "said_by_id", "verified_by", "learned_at_minute", "text"],
    )

    opps = a.get("opportunities", [])
    out += heading(2, f"Deals / opportunities ({len(opps)})")
    out += table(
        [{
            "id": o["id"], "title": o["title"], "stage": o["stage"], "worth_up_to": o.get("worth_up_to"),
            "size": o.get("size"),
            "blocker": f"[{o['blocker']['status']}] {o['blocker']['text']}" if o.get("blocker") else "",
            "deadline": (o.get("deadline") or {}).get("text", ""),
        } for o in opps],
        ["id", "title", "stage", "worth_up_to", "size", "blocker", "deadline"],
    )
    for o in opps:
        out += heading(3, f"{o['id']}: {o['title']}")
        out.append(f"- **stage**: {o['stage']} · **worth_up_to**: {fmt(o.get('worth_up_to')) or 'null'} · **size**: {fmt(o.get('size')) or 'null'}")
        if o.get("blocker"):
            out.append(f"- **blocker** ({o['blocker'].get('status')}): {o['blocker'].get('text')}")
        if o.get("deadline"):
            out.append(f"- **deadline**: {fmt(o['deadline'])}")
        out.append("- **evidence**:")
        out += [f"  - {idx.label(A, e)}" for e in o.get("evidence", [])]
        out.append("")

    offers = a.get("offers", [])
    out += heading(2, f"Offers ({len(offers)})")
    out += table(
        [{**o, "pitch_to_id": names.get(o.get("pitch_to"), "**no stakeholder match**")} for o in offers],
        ["id", "title", "value", "status", "pitch_to", "pitch_to_id"],
    )

    out += heading(2, f"Deadlines ({len(a.get('deadlines', []))})")
    out += ["_Deadlines have no id. Cite one through its opportunity as `<account>/<opportunity_id>#deadline`._", ""]
    rows = []
    for d in a.get("deadlines", []):
        opp = deadline_opportunity(a, d)
        rows.append({**d, "cite_as": f"{A}/{deadline_key(opp)}" if opp else "**no matching opportunity**"})
    out += table(rows)

    mine = [f for f in files if f.get("account") == A]
    out += heading(2, f"Files ({len(mine)})")
    out += table(mine, ["id", "title", "path", "account"])
    for f in mine:
        out += heading(3, f"File {f['id']}")
        content = file_contents.get(f["id"])
        if content is None:
            out += [f"_Content not pulled. Run `python scripts/pull.py --only get_file_{f['id']}`._", ""]
        else:
            out += render_file_content(content)

    out += heading(2, f"Signals ({len(signals)})")
    for s in sorted(signals, key=lambda s: s.get("dated", "")):
        out += heading(3, f"{s['id']} · {s.get('dated', '')} · {s.get('title', '')}")
        out += [s.get("summary", ""), "", f"_set_off_at: {s.get('set_off_at', '')}_", ""]
        for r in s.get("records", []):
            out += heading(4, f"{r['path']} ({r['kind']})")
            if r.get("note"):
                out += [f"_{r['note']}_", ""]
            out += render_record_body(r)
    if not signals:
        out += ["_(none yet)_", ""]

    out += heading(2, "Paperwork")
    if paperwork is None:
        out += [f"_(not pulled: run `python scripts/pull.py --only get_all_paperwork_{A}`)_", ""]
    else:
        out += render_paperwork(A, paperwork)
    return out


def render_file_content(content):
    if isinstance(content, str):
        return code(content)
    if isinstance(content, dict):
        out = render_fields({k: v for k, v in content.items() if simple_value(v) and k not in ("text", "content")}, 4)
        body = content.get("text") or content.get("content")
        if isinstance(body, str):
            out += (csv_summary(body) if is_csv(content.get("name"), body) else []) + code(body)
        for k, v in content.items():
            if not simple_value(v):
                out += heading(4, k) + render_any(v, 5)
        return out
    return render_any(content, 4)


# ---------- paperwork ----------

def record_date(rec):
    m = DATE_IN_PATH.search(rec.get("path", ""))
    return m.group(1) if m else ""


def csv_summary(text):
    """Describe a CSV: rows, date range, and counts for low-cardinality columns."""
    rows = csv_rows(text)
    if not rows:
        return ["_(empty CSV)_", ""]
    cols = list(rows[0].keys())
    out = [f"_CSV: {len(rows)} rows; columns: {', '.join(cols)}_", ""]
    date_col = next((c for c in cols if c in ("date", "week_start", "opened")), None)
    if date_col:
        dates = sorted(r[date_col] for r in rows if r.get(date_col))
        if dates:
            out.append(f"- **{date_col} range**: {dates[0]} to {dates[-1]}")
    for c in cols:
        values = Counter(r.get(c, "") for r in rows)
        if c != date_col and 1 < len(values) <= 40:
            out.append(f"- **{c}**: " + ", ".join(f"{v or '(blank)'} {n}" for v, n in values.most_common()))
    return out + [""]


def render_record_body(r):
    out = []
    if isinstance(r.get("text"), str):
        if is_csv(r.get("path"), r["text"]):
            out += csv_summary(r["text"])
        out += code(r["text"])
    if "data" in r:
        out += render_any(r["data"], 5)
    return out


def render_paperwork(A, payload):
    records = payload.get("records", [])
    by_kind = defaultdict(list)
    for r in records:
        by_kind[r["kind"]].append(r)
    order = [k for k in KIND_ORDER if k in by_kind] + sorted(k for k in by_kind if k not in KIND_ORDER)
    out = ["Counts: " + ", ".join(f"{k} {len(by_kind[k])}" for k in order), ""]
    for k in order:
        rs = sorted(by_kind[k], key=lambda r: (record_date(r), r["path"]))
        out += heading(3, f"{k} ({len(rs)})")
        for r in rs:
            out += heading(4, f"{A}/{r['path']}")
            out += render_record_body(r)
    return out


# ---------- main ----------

def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--raw", default=str(RAW_DIR))
    parser.add_argument("--out", default=str(DUMP_DIR))
    args = parser.parse_args()
    raw_dir, out_dir = Path(args.raw), Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    raw = {p.stem: load_json(p) for p in sorted(raw_dir.glob("*.json")) if not p.stem.startswith("_")}
    if "get_my_accounts" not in raw:
        sys.exit(f"no get_my_accounts.json in {raw_dir}; run scripts/pull.py first")
    errors = sorted(p.name for p in raw_dir.glob("*.error.txt"))
    written = []

    def write(name, lines):
        (out_dir / name).write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
        written.append(name)

    export = raw["get_my_accounts"]
    world = assemble(raw)
    files = world["files"]
    paperwork = {A: (f"get_all_paperwork_{A}", {"records": recs}) for A, recs in world["paperwork"].items()}
    file_contents = {f["id"]: f["content"] for f in files if f["content"] is not None}
    signals = world["signals"]
    idx = build_ids(world)

    # schema.md
    if "get_data_format" in raw:
        write("schema.md", ["# Data format (get_data_format)", "", "The schema of record. Use these field names.", ""] + code(json.dumps(raw["get_data_format"], indent=2), "json"))

    # accounts_meta.md: export-level fields (game clock etc.)
    meta = {k: v for k, v in export.items() if k not in ("accounts", "files")}
    lines = ["# Export metadata (get_my_accounts)", ""] + render_fields(meta, 2)
    if "whoami" in raw:
        lines += heading(2, "whoami") + render_any(raw["whoami"], 3)
    write("accounts_meta.md", lines)

    # account_<id>.md for every account the data knows about, with or without a sheet.
    sheets = []
    for a in world["accounts"]:
        if not a["has_sheet"]:
            drop = a.get("drop")
            note = (f"_No account sheet yet. {a['id']} arrived as a new-account drop (whoami.new_accounts, path {drop.get('path')}); "
                    f"run `python scripts/pull.py --refresh` to fetch it with get_new_account._" if isinstance(drop, dict)
                    else f"_No account sheet: {a['id']} is in no export._")
            a = {**a, "name": a.get("name") or "(no account sheet)", "official_story": note}
        sheets.append(a)
    for a in sheets:
        A = a["id"]
        pw = paperwork.get(A, (None, None))[1]
        write(f"account_{A}.md", render_account(a, files, pw, [s for s in signals if s.get("account") == A], file_contents, idx))

    # shared sources
    for out_name, src, title in [
        ("team.md", "get_flytbase_team", "FlytBase team"),
        ("feature_requests.md", "get_feature_requests", "Feature requests"),
        ("release_notes.md", "get_release_notes", "Release notes"),
        ("signals.md", "get_signals", "Signals (all accounts)"),
        ("notes.md", "list_notes", "Notes written back"),
    ]:
        if src in raw:
            write(out_name, [f"# {title} ({src})", ""] + render_any(raw[src], 2))

    write("index.md", render_index(raw, sheets, files, paperwork, signals, file_contents, idx, errors))

    # changes.md: compare with the previous run, then save this run's state for next time.
    state_path = out_dir / "_state.json"
    previous = load_json(state_path) if state_path.exists() else None
    state = {
        "dumped_at": datetime.now().isoformat(timespec="seconds"),
        "raw": {name: hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest() for name, payload in raw.items()},
        "ids": {key: e["label"] for key, e in idx.entries.items()},
    }
    write("changes.md", render_changes(previous, state))
    state_path.write_text(json.dumps(state, indent=1, ensure_ascii=False), encoding="utf-8")

    # Remove stale outputs (e.g. an account that no longer exists).
    for old in out_dir.glob("*.md"):
        if old.name not in written:
            old.unlink()
            print(f"removed stale {old.name}")

    print(f"wrote {len(written)} file(s) to {out_dir}: {', '.join(written)}")
    print(f"{len(idx.entries)} ids indexed, {len(idx.collisions)} collision(s), {len(errors)} error file(s)")


def render_changes(previous, state):
    lines = ["# Changes since the previous dump", ""]
    if previous is None:
        return lines + ["_First dump with change tracking; nothing to compare yet._", ""]
    lines += [f"Previous dump: {previous['dumped_at']}. This dump: {state['dumped_at']}.", ""]

    old_raw, new_raw = previous.get("raw", {}), state["raw"]
    raw_rows = [{"source": n, "change": "new"} for n in sorted(set(new_raw) - set(old_raw))]
    raw_rows += [{"source": n, "change": "removed"} for n in sorted(set(old_raw) - set(new_raw))]
    raw_rows += [{"source": n, "change": "changed"} for n in sorted(set(new_raw) & set(old_raw)) if new_raw[n] != old_raw[n]]
    lines += heading(2, f"Raw sources ({len(raw_rows)} changed)") + (table(raw_rows, ["source", "change"]) if raw_rows else ["_(none)_", ""])

    old_ids, new_ids = previous.get("ids", {}), state["ids"]
    added = sorted(set(new_ids) - set(old_ids))
    removed = sorted(set(old_ids) - set(new_ids))
    relabelled = sorted(k for k in set(new_ids) & set(old_ids) if new_ids[k] != old_ids[k])
    lines += heading(2, f"Ids added ({len(added)})") + ([f"- `{k}`: {new_ids[k]}" for k in added] or ["_(none)_"]) + [""]
    lines += heading(2, f"Ids removed ({len(removed)})") + ([f"- `{k}`: {old_ids[k]}" for k in removed] or ["_(none)_"]) + [""]
    lines += heading(2, f"Ids whose status, stage or label changed ({len(relabelled)})")
    lines += [f"- `{k}`: {old_ids[k]} -> {new_ids[k]}" for k in relabelled] or ["_(none)_"]
    return lines + [""]


def render_index(raw, accounts, files, paperwork, signals, file_contents, idx, errors):
    lines = ["# Index", "", f"Raw files: {', '.join(sorted(raw))}", ""]
    if errors:
        lines += [f"**Errors on disk** (not pulled): {', '.join(errors)}", ""]
    missing_files = [f["id"] for f in files if f["id"] not in file_contents]
    if missing_files:
        lines += [f"**File contents not pulled yet**: {', '.join(missing_files)}", ""]

    # counts
    rows = []
    for a in accounts:
        A = a["id"]
        for key in ("stakeholders", "facts", "opportunities", "offers", "deadlines"):
            rows.append({"account": A, "entity": key, "count": len(a.get(key, []))})
        for status, n in sorted(Counter(f["status"] for f in a.get("facts", [])).items()):
            rows.append({"account": A, "entity": f"facts[{status}]", "count": n})
        for stage, n in sorted(Counter(o["stage"] for o in a.get("opportunities", [])).items()):
            rows.append({"account": A, "entity": f"opportunities[{stage}]", "count": n})
        rows.append({"account": A, "entity": "files", "count": sum(1 for f in files if f.get("account") == A)})
        rows.append({"account": A, "entity": "signals", "count": sum(1 for s in signals if s.get("account") == A)})
        pw = paperwork.get(A, (None, {}))[1] or {}
        for kind, n in sorted(Counter(r["kind"] for r in pw.get("records", [])).items()):
            rows.append({"account": A, "entity": f"paperwork:{kind}", "count": n})
        for r in pw.get("records", []):
            if is_csv(r.get("path"), r.get("text")):
                rows.append({"account": A, "entity": f"rows in {r['path']}", "count": len(csv_rows(r["text"]))})
    for src, key in [("get_flytbase_team", "people"), ("get_feature_requests", "requests"), ("get_release_notes", "releases"), ("list_notes", "notes")]:
        rows.append({"account": "-", "entity": f"{src}.{key}", "count": len((raw.get(src) or {}).get(key, []))})
    lines += heading(2, "Counts per entity type per account") + table(rows, ["account", "entity", "count"])

    kinds = Counter((e["scope"], e["kind"]) for e in idx.entries.values())
    lines += heading(2, "Indexed ids per kind")
    lines += table([{"scope": a, "kind": k, "ids": n} for (a, k), n in sorted(kinds.items())], ["scope", "kind", "ids"])
    lines += [f"Total: {len(idx.entries)} ids. Every key is `<scope>/<id>`: scope is the account, or `team` / `feature_requests` for shared sources.", ""]

    lines += heading(2, "Every id")
    entries = sorted(idx.entries.values(), key=lambda e: (e["scope"], e["kind"], e["id"]))
    lines += table(entries, ["key", "kind", "label", "file", "where"])

    # reference checks
    problems = []
    for a in accounts:
        A = a["id"]
        names = by_name(a.get("stakeholders", []))
        for o in a.get("opportunities", []):
            problems += [f"{A} {o['id']} evidence `{e}` not found" for e in o.get("evidence", []) if not idx.get(A, e)]
        for f in a.get("facts", []):
            if f.get("verified_by") and not idx.get(A, f["verified_by"]):
                problems.append(f"{A} {f['id']} verified_by `{f['verified_by']}` not found")
            if f.get("said_by") not in names:
                problems.append(f"{A} {f['id']} said_by '{f.get('said_by')}' is not a stakeholder name")
        for o in a.get("offers", []):
            if o.get("pitch_to") and o["pitch_to"] not in names:
                problems.append(f"{A} {o['id']} pitch_to '{o['pitch_to']}' is not a stakeholder name")
        for d in a.get("deadlines", []):
            if not deadline_opportunity(a, d):
                problems.append(f"{A} deadline '{d.get('text')}' matches no opportunity deadline")
        # docks named in CSVs vs docks registered in devices.json
        pw = paperwork.get(A, (None, {}))[1] or {}
        registered = {e["id"] for e in idx.entries.values() if e["scope"] == A and e["kind"] == "dock"}
        for r in pw.get("records", []):
            if is_csv(r.get("path"), r.get("text")):
                named = {row["dock_id"] for row in csv_rows(r["text"]) if row.get("dock_id")}
                for d in sorted(named - registered):
                    problems.append(f"{A}/{r['path']} names dock `{d}` not in devices.json")
    lines += heading(2, "Reference checks")
    lines += [f"- {p}" for p in problems] or ["All opportunity evidence, verified_by, said_by, pitch_to, deadlines and CSV dock ids resolve."]
    lines.append("")
    if idx.collisions:
        lines += heading(2, "Ids listed in more than one place (first one is the primary entry)")
        lines += [f"- `{key}`: kept {k1}, also {k2} at {w}" for key, k1, k2, w in idx.collisions] + [""]
    return lines


if __name__ == "__main__":
    main()

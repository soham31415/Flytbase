"""Merge raw MCP payloads into one view of everything we hold. Pure logic, no I/O, no MCP.

Input: {source_name: payload}, where source names are the pull names (get_my_accounts,
get_all_paperwork_A, get_file_<id>, get_new_account_K, get_new_account_file_K_<id>,
get_signals, whoami, ...). Used by scripts/dump.py and app/store.py, so both see the
same accounts, files, paperwork and ids.
"""

import csv
import io
import re

DATE_RE = re.compile(r"\b(\d{4}-\d{2}-\d{2})\b")


def csv_rows(text):
    return list(csv.DictReader(io.StringIO(text)))


def is_csv(name, text):
    """A CSV is a text whose file name ends in .csv. Content alone can't tell: transcripts have commas too."""
    return isinstance(text, str) and str(name or "").lower().endswith(".csv")


def drop_id(entry):
    if isinstance(entry, str):
        return entry
    if isinstance(entry, dict):
        return entry.get("id") or entry.get("account")
    return None


def assemble(raw):
    """Return {accounts, files, paperwork, signals, team, feature_requests, release_notes, notes, drops}.

    accounts: list of account sheets, each with `has_sheet`, `sheet_source`, and `drop` (whoami entry or None).
    files: list of {id, title, path, account, source, content, content_source}.
    paperwork: {account: [record]}; each record has `source`, and CSV records merged from
    signals carry `rows` = [{"row": {...}, "source": ...}].
    """
    drops = {drop_id(e): e for e in (raw.get("whoami") or {}).get("new_accounts") or [] if drop_id(e)}

    # Account sheets: the morning export, then each drop account's own sheet.
    accounts, files = [], []
    sheet_sources = [("get_my_accounts", raw.get("get_my_accounts"))]
    sheet_sources += [(n, p) for n, p in sorted(raw.items()) if n.startswith("get_new_account_") and not n.startswith("get_new_account_file_")]
    for source, export in sheet_sources:
        if not isinstance(export, dict):
            continue
        for a in export.get("accounts", []):
            if a.get("id") and a["id"] not in {x["id"] for x in accounts}:
                accounts.append({**a, "has_sheet": True, "sheet_source": source, "drop": drops.get(a["id"])})
        default_account = source[len("get_new_account_"):] if source.startswith("get_new_account_") else None
        for f in export.get("files", []):
            if not f.get("id"):
                continue
            acct = f.get("account") or default_account
            if source == "get_my_accounts":
                content_source = f"get_file_{f['id']}"
            else:
                content_source = f"get_new_account_file_{acct}_{f['id']}"
            files.append({**f, "account": acct, "source": source, "content_source": content_source, "content": raw.get(content_source)})

    # Paperwork, by account.
    paperwork = {}
    for name, payload in raw.items():
        if name.startswith("get_all_paperwork_") and isinstance(payload, dict):
            acct = name[len("get_all_paperwork_"):]
            paperwork[acct] = [{**r, "source": name} for r in payload.get("records", [])]

    signals = [s for s in (raw.get("get_signals") or {}).get("signals", []) if isinstance(s, dict)]
    for s in sorted(signals, key=lambda s: (s.get("dated", ""), s.get("id", ""))):
        merge_signal(paperwork, s)

    # Accounts known only from a drop, paperwork or signals get an empty sheet.
    known = {a["id"] for a in accounts}
    others = (set(drops) | set(paperwork) | {s.get("account") for s in signals if s.get("account")}) - known
    for acct in sorted(others):
        drop = drops.get(acct)
        accounts.append({
            "id": acct, "name": (drop or {}).get("name") if isinstance(drop, dict) else None,
            "official_story": None, "stakeholders": [], "facts": [], "opportunities": [], "offers": [], "deadlines": [],
            "has_sheet": False, "sheet_source": None, "drop": drop,
        })

    return {
        "accounts": accounts,
        "files": files,
        "paperwork": paperwork,
        "signals": signals,
        "drops": drops,
        "team": (raw.get("get_flytbase_team") or {}).get("people", []),
        "feature_requests": (raw.get("get_feature_requests") or {}).get("requests", []),
        "release_notes": (raw.get("get_release_notes") or {}).get("releases", []),
        "notes": (raw.get("list_notes") or {}).get("notes", []),
    }


def merge_signal(paperwork, signal):
    """Add a signal's records to its account's paperwork (CLAUDE.md: Signals).

    A CSV record for a file we already hold is merged row by row; each row keeps its source.
    Anything else becomes its own record, sourced to the signal.
    """
    acct = signal.get("account")
    if not acct:
        return
    source = f"{acct}/{signal.get('id')}"
    records = paperwork.setdefault(acct, [])
    for rec in signal.get("records", []):
        path = rec.get("path", "")
        path = path[len(acct) + 1:] if path.startswith(f"{acct}/") else path
        existing = next((r for r in records if r.get("path") == path), None)
        text = rec.get("text")
        if existing and is_csv(path, text) and is_csv(existing["path"], existing.get("text")):
            merge_csv_rows(existing, text, source)
        elif existing is None:
            records.append({**rec, "path": path, "source": source})
        else:
            # Same path but not mergeable: keep both, the signal's copy under its own source.
            records.append({**rec, "path": path, "source": source, "replaces": existing.get("source")})


def merge_csv_rows(existing, text, source):
    if "rows" not in existing:
        existing["rows"] = [{"row": row, "source": existing["source"]} for row in csv_rows(existing["text"])]
    seen = {tuple(sorted(r["row"].items())) for r in existing["rows"]}
    for row in csv_rows(text):
        key = tuple(sorted(row.items()))
        if key not in seen:
            seen.add(key)
            existing["rows"].append({"row": row, "source": source})
    header = list(existing["rows"][0]["row"].keys()) if existing["rows"] else []
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=header, lineterminator="\n")
    writer.writeheader()
    for r in existing["rows"]:
        writer.writerow(r["row"])
    existing["text"] = buf.getvalue()


def in_game_today(world, override=None):
    """(date string, source) for today: max of paperwork dates and signal `dated` (CLAUDE.md: Time)."""
    if override:
        return override, "AS_OF"
    best, best_src = "", None
    for s in world["signals"]:
        d = s.get("dated") or ""
        if d > best:
            best, best_src = d, f"{s.get('account')}/{s.get('id')}"
    # Only activity dates count (things that happened), never term ends or renewal dates.
    for acct, records in world["paperwork"].items():
        for r in records:
            latest = max(_activity_dates(r), default="")
            if latest > best:
                best, best_src = latest, f"{acct}/{r.get('path')}"
    return best or None, best_src


ACTIVITY_KEYS = ("last_seen_online", "last_sign_in", "date", "opened", "closed", "issued", "paid", "week_start", "dated")


def _activity_dates(record):
    """Dates of things that happened in a record (not future dates like term ends or renewals)."""
    out = []
    data = record.get("data")

    def visit(v, key=None):
        if isinstance(v, dict):
            for k, x in v.items():
                visit(x, k)
        elif isinstance(v, list):
            for x in v:
                visit(x, key)
        elif isinstance(v, str) and key in ACTIVITY_KEYS and DATE_RE.fullmatch(v[:10]):
            out.append(v[:10])

    if data is not None:
        visit(data)
    if is_csv(record.get("path"), record.get("text")):
        for row in csv_rows(record["text"]):
            for k in ("date", "week_start", "opened", "last_update"):
                if row.get(k) and DATE_RE.fullmatch(row[k][:10]):
                    out.append(row[k][:10])
    return out

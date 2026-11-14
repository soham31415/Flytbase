"""Decide what to re-pull after a refresh call. Pure logic: this module never calls the MCP.

Used by scripts/pull.py --refresh now, and by the scheduler (and its "sync now" button) later.
A refresh always starts with REFRESH_HEAD; each response is compared with the previous copy
and may queue follow-up pulls.
"""

import hashlib
import json

# Cheap calls that reveal whether anything changed. Always made on a refresh.
REFRESH_HEAD = [
    ("whoami", "whoami", {}),
    ("get_my_accounts", "get_my_accounts", {}),
    ("get_signals", "get_signals", {}),
]


def payload_hash(payload):
    """Stable fingerprint of a payload, so unchanged responses can be recognised."""
    text = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def paperwork_step(account):
    return (f"get_all_paperwork_{account}", "get_all_paperwork", {"account": account})


def file_step(file_id):
    return (f"get_file_{file_id}", "get_file", {"id": file_id})


def new_account_step(account):
    """A drop account's sheet (same shape as get_my_accounts), from whoami new_accounts."""
    return (f"get_new_account_{account}", "get_new_account", {"account": account})


def new_account_file_step(account, file_id):
    return (f"get_new_account_file_{account}_{file_id}", "get_new_account_file", {"account": account, "file_id": file_id})


def drop_id(entry):
    """The account id of a whoami new_accounts entry (a plain id or {id, name, path})."""
    if isinstance(entry, str):
        return entry
    if isinstance(entry, dict):
        return entry.get("id") or entry.get("account")
    return None


def new_account_files(account, payload):
    """File steps listed in a get_new_account payload."""
    files = payload.get("files", []) if isinstance(payload, dict) else []
    return [new_account_file_step(account, f["id"]) for f in files if isinstance(f, dict) and f.get("id")]


def _ids(items, key="id"):
    return {str(x[key]) for x in items or [] if isinstance(x, dict) and x.get(key)}


def followups(name, old, new):
    """Compare a fresh payload with the previous one.

    Returns (steps, notes): steps to pull next as (name, tool, args), and human-readable
    notes on what changed. `old` is None when there was no previous copy.
    """
    steps, notes = [], []
    if old is not None and payload_hash(old) == payload_hash(new):
        return steps, notes

    if name == "whoami" and isinstance(new, dict):
        old = old or {}
        old_drops = {drop_id(e) for e in old.get("new_accounts") or []}
        for entry in new.get("new_accounts") or []:
            acct = drop_id(entry)
            if acct and acct not in old_drops:
                notes.append(f"whoami: new account {entry}")
                steps += [new_account_step(acct), paperwork_step(acct)]
        was_open = {p.get("account") for p in old.get("pack", []) if p.get("open")}
        for p in new.get("pack", []):
            if p.get("open") and p.get("account") not in was_open:
                notes.append(f"whoami: account {p.get('account')} is open (new or newly opened)")
                steps.append(paperwork_step(p["account"]))
        if (new.get("signals") or 0) > (old.get("signals") or 0):
            notes.append(f"whoami: signals {old.get('signals', 0)} -> {new.get('signals')}")

    elif name == "get_my_accounts" and isinstance(new, dict):
        old = old or {}
        old_accounts = {a.get("id"): a for a in old.get("accounts", [])}
        for a in new.get("accounts", []):
            if a.get("id") not in old_accounts:
                notes.append(f"get_my_accounts: new account {a.get('id')} ({a.get('name')})")
                steps.append(paperwork_step(a["id"]))
            elif payload_hash(a) != payload_hash(old_accounts[a["id"]]):
                notes.append(f"get_my_accounts: account {a['id']} changed")
        for fid in sorted(_ids(new.get("files")) - _ids(old.get("files"))):
            notes.append(f"get_my_accounts: new file {fid}")
            steps.append(file_step(fid))

    elif name.startswith("get_new_account_") and not name.startswith("get_new_account_file_") and isinstance(new, dict):
        account = name[len("get_new_account_"):]
        old_files = _ids((old or {}).get("files"))
        for step in new_account_files(account, new):
            if step[2]["file_id"] not in old_files:
                notes.append(f"{name}: new file {step[2]['file_id']}")
                steps.append(step)
        if old is not None:
            notes.append(f"{name}: changed")

    elif name == "get_signals" and isinstance(new, dict):
        old_ids = _ids((old or {}).get("signals"))
        for s in new.get("signals", []):
            if s.get("id") and s["id"] not in old_ids:
                notes.append(f"get_signals: new signal {s['id']} for account {s.get('account')} ({s.get('dated')})")
                # A signal may come with paperwork updates beyond its inline records.
                if s.get("account"):
                    steps.append(paperwork_step(s["account"]))

    else:
        notes.append(f"{name}: {'new' if old is None else 'changed'}")

    return _dedupe(steps), notes


def _dedupe(steps):
    seen, out = set(), []
    for step in steps:
        if step[0] not in seen:
            seen.add(step[0])
            out.append(step)
    return out

"""Helpers shared by pull.py and dump.py. No network access here."""

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RAW_DIR = ROOT / "data" / "raw"
DUMP_DIR = ROOT / "data" / "dump"

DEFAULT_ACCOUNTS = ["A", "B"]

# Keys that might hold an account's identifier, most specific first.
ACCOUNT_ID_KEYS = ["account", "account_id", "accountId", "code", "letter", "key", "id", "slug"]


def load_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def account_list(payload):
    """Find the list of account objects inside a get_my_accounts payload."""
    if isinstance(payload, list):
        return [a for a in payload if isinstance(a, dict)]
    if isinstance(payload, dict):
        for key, value in payload.items():
            if "account" in key.lower() and isinstance(value, list):
                return [a for a in value if isinstance(a, dict)]
            if "account" in key.lower() and isinstance(value, dict) and all(
                isinstance(v, dict) for v in value.values()
            ):
                return [{"_key": k, **v} for k, v in value.items()]
        # A dict keyed by account id, e.g. {"A": {...}, "B": {...}}
        if payload and all(isinstance(v, dict) for v in payload.values()):
            return [{"_key": k, **v} for k, v in payload.items()]
        # A single account object
        return [payload]
    return []


def find_accounts(payload):
    """Return ([(account_id, account_obj)], note) from a get_my_accounts payload.

    Prefers a key whose values are exactly A/B; otherwise the first identifier-like key
    present on every account. Falls back to A/B with a note when nothing fits.
    """
    accounts = account_list(payload)
    if not accounts:
        return [(a, {}) for a in DEFAULT_ACCOUNTS], "no accounts found in payload; using A/B"

    keys = ["_key"] + ACCOUNT_ID_KEYS
    common = [k for k in keys if all(_scalar(a.get(k)) for a in accounts)]

    for k in common:
        if {str(a[k]) for a in accounts} == set(DEFAULT_ACCOUNTS):
            return [(str(a[k]), a) for a in accounts], f"account ids are A/B (field '{k}')"

    for k in common:
        ids = [str(a[k]) for a in accounts]
        if len(set(ids)) == len(ids):
            return [(i, a) for i, a in zip(ids, accounts)], (
                f"account ids are NOT A/B; using field '{k}': {ids}"
            )

    return [(a, {}) for a in DEFAULT_ACCOUNTS], "could not identify account ids; using A/B"


def _scalar(v):
    return isinstance(v, (str, int)) and not isinstance(v, bool) and str(v) != ""

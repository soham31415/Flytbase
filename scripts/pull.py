"""One-time pull of every MCP source into data/raw/.

Pulls the Step 1 sources plus get_file for each file in get_my_accounts.
One call at a time, at least 65s apart (the token allows 1 call/min, writes included).
Resumable: files already in data/raw/ are skipped unless --force <name>.

    python scripts/pull.py                         # pull everything missing
    python scripts/pull.py --only get_all_paperwork_A
    python scripts/pull.py --force get_signals --only get_signals
    python scripts/pull.py --accounts A,B          # override account ids
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from dotenv import load_dotenv  # noqa: E402

import mcp_client  # noqa: E402
from common import DEFAULT_ACCOUNTS, RAW_DIR, ROOT, find_accounts, load_json  # noqa: E402

GAP_S = 65  # seconds between calls
RATE_LIMIT_WAIT_S = 70  # wait before the single retry after a rate-limit error
HTTP_DIR = RAW_DIR / "_http"
LAST_CALL_FILE = RAW_DIR / "_last_call_at.txt"  # so a quick re-run still respects the gap

# (file name, tool, args) before and after the per-account paperwork calls.
HEAD = [
    ("whoami", "whoami", {}),
    ("get_data_format", "get_data_format", {}),
    ("get_my_accounts", "get_my_accounts", {}),
]
TAIL = [
    ("get_flytbase_team", "get_flytbase_team", {}),
    ("get_feature_requests", "get_feature_requests", {}),
    ("get_release_notes", "get_release_notes", {}),
    ("list_notes", "list_notes", {}),
    ("get_signals", "get_signals", {}),
]


def log(msg):
    print(f"{datetime.now():%H:%M:%S}  {msg}", flush=True)


def paperwork_steps(accounts):
    return [(f"get_all_paperwork_{a}", "get_all_paperwork", {"account": a}) for a in accounts]


def file_steps():
    """One get_file per file listed in get_my_accounts (the files that back verified facts)."""
    path = RAW_DIR / "get_my_accounts.json"
    if not path.exists():
        return []
    export = load_json(path)
    files = export.get("files", []) if isinstance(export, dict) else []
    return [(f"get_file_{f['id']}", "get_file", {"id": f["id"]}) for f in files if f.get("id")]


def accounts_from_disk():
    path = RAW_DIR / "get_my_accounts.json"
    if not path.exists():
        return None, None
    found, note = find_accounts(load_json(path))
    return [a for a, _ in found], note


def json_path(name):
    return RAW_DIR / f"{name}.json"


def error_path(name):
    return RAW_DIR / f"{name}.error.txt"


def top_level_keys(payload):
    if isinstance(payload, dict):
        return ",".join(list(payload.keys())[:12]) or "-"
    if isinstance(payload, list):
        return f"[list of {len(payload)}]"
    return f"[{type(payload).__name__}]"


class Throttle:
    """Sleeps so that consecutive calls are at least `gap` seconds apart, across runs too."""

    def __init__(self):
        self.last = 0.0
        if LAST_CALL_FILE.exists():
            try:
                self.last = float(LAST_CALL_FILE.read_text().strip())
            except ValueError:
                pass

    def wait(self, gap):
        delay = self.last + gap - time.time()
        if delay > 0:
            log(f"waiting {delay:.0f}s for the rate limit")
            time.sleep(delay)

    def mark(self):
        self.last = time.time()
        LAST_CALL_FILE.write_text(str(self.last))


def call_once(throttle, tool, args, gap):
    throttle.wait(gap)
    try:
        payload, raw = mcp_client.call_tool_raw(tool, args)
        return payload, raw, None
    except mcp_client.McpError as e:
        return None, e.raw_body, e
    finally:
        throttle.mark()


def pull_one(throttle, name, tool, args):
    """Pull one source with a single retry on rate limit. Returns True on success."""
    payload, raw, err = call_once(throttle, tool, args, GAP_S)
    if err is not None and err.is_rate_limit:
        log(f"{name:<22} rate limited; retrying once in {RATE_LIMIT_WAIT_S}s")
        payload, raw, err = call_once(throttle, tool, args, RATE_LIMIT_WAIT_S)

    if raw is not None:
        (HTTP_DIR / f"{name}.txt").write_text(raw, encoding="utf-8")
    size = len(raw.encode("utf-8")) if raw else 0

    if err is not None:
        error_path(name).write_text(str(err), encoding="utf-8")
        log(f"{name:<22} ERROR  {size:>8} B  {str(err)[:150]}")
        return False

    json_path(name).write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    if error_path(name).exists():
        error_path(name).unlink()
    log(f"{name:<22} ok     {size:>8} B  keys: {top_level_keys(payload)}")
    return True


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--only", action="append", default=[], metavar="NAME", help="pull only these sources (repeatable)")
    parser.add_argument("--force", action="append", default=[], metavar="NAME", help="re-pull even if the file exists (repeatable)")
    parser.add_argument("--accounts", help="comma-separated account ids, overrides get_my_accounts")
    args = parser.parse_args()

    load_dotenv(ROOT / ".env")
    if not os.environ.get("AQ_TOKEN", "").strip():
        sys.exit("AQ_TOKEN is not set. Put it in .env (see .env.example).")

    RAW_DIR.mkdir(parents=True, exist_ok=True)
    HTTP_DIR.mkdir(parents=True, exist_ok=True)

    if args.accounts:
        accounts, note = [a.strip() for a in args.accounts.split(",") if a.strip()], "from --accounts"
    else:
        accounts, note = accounts_from_disk()
        if accounts is None:
            accounts, note = list(DEFAULT_ACCOUNTS), "default (get_my_accounts not pulled yet)"
    log(f"accounts: {accounts} ({note})")

    def wanted(name):
        if _would_pull(name, args):
            return True
        if not args.only or name in args.only:
            log(f"{name:<22} skip   (exists; use --force {name} to re-pull)")
        return False

    def is_paperwork(name):
        return name.startswith(("get_all_paperwork_", "get_file_"))

    # Validate names up front so a typo doesn't cost a minute.
    known = {n for n, _, _ in HEAD + TAIL + paperwork_steps(accounts) + file_steps()}
    for name in args.only + args.force:
        if name not in known and not is_paperwork(name):
            sys.exit(f"unknown source '{name}'. Known: {', '.join(sorted(known))}")

    # Estimate with the current account list (it may change after get_my_accounts).
    plan = [s for s in HEAD + paperwork_steps(accounts) + TAIL + file_steps() if _would_pull(s[0], args)]
    if not plan:
        log("nothing to pull; everything is on disk.")
        return
    minutes = max(len(plan) - 1, 0) * GAP_S / 60
    log(f"{len(plan)} call(s) to make, about {minutes:.1f} min (one call per {GAP_S}s)")

    throttle = Throttle()
    failed = []

    for name, tool, tool_args in HEAD:
        if wanted(name) and not pull_one(throttle, name, tool, tool_args):
            failed.append(name)
        if name == "get_my_accounts" and not args.accounts:
            found, note = accounts_from_disk()
            if found and found != accounts:
                log(f"switching accounts {accounts} -> {found} ({note})")
                accounts = found
            elif found:
                log(f"accounts confirmed: {found} ({note})")

    for name, tool, tool_args in paperwork_steps(accounts) + TAIL + file_steps():
        if wanted(name) and not pull_one(throttle, name, tool, tool_args):
            failed.append(name)

    if failed:
        log(f"done with errors: {', '.join(failed)}. See data/raw/<name>.error.txt; re-run to retry.")
    else:
        log("done. Next: python scripts/dump.py")


def _would_pull(name, args):
    if args.only and name not in args.only:
        return False
    return not json_path(name).exists() or name in args.force


if __name__ == "__main__":
    main()

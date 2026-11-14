# Questathon CRM: project rules

## What this is
A live CRM for FlytBase's customer accounts (drone docks). It reads data from a hackathon MCP server and, for every account, answers five questions:
1. What is the book worth? (each deal at a value we can back, with the backing ids; unbacked values shown apart)
2. Who holds the power? (per deal: decider, payer, user, blocker, champion, and stance with us)
3. What do we do first? (top 3 moves, ranked, each with owner, date, and the deal it moves)
4. What is about to go wrong? (deadlines at risk, people going cold, stuck deals, and when each turns bad)
5. What do we still not know? (gaps the plan rests on, why they matter, who could fill each)
Judges score: every number cites its source id, no invented ids, ranked/owned/dated moves, honest gaps, and it stays live.

The account list comes from the data, never from code: get_my_accounts `accounts[]`, whoami `new_accounts` and `pack`, paperwork, and signals (today: A, B, K). Every account always gets all five answers. Where data is missing the answer says so, and says who to ask (e.g. "Decider: unknown, ask Dana Whitfield").

## Data semantics
- The schema of record is data/dump/schema.md (from get_data_format). Use real field names from it; never guess.
- get_my_accounts returns the export: `accounts[]` (each `id, name, official_story, stakeholders, facts, opportunities, offers, deadlines`), top-level `files[]`, and `minute` (the game clock).
- People = `stakeholders[]`: `id, name, role, relationship, available`. `relationship` is guarded | neutral | warm | ally. Only people we have met.
- Facts = `facts[]`: `id, text, said_by, status, verified_by?, learned_at_minute`. Status: claim (may be wrong) < told (a person said it) < verified (`verified_by` names a file we hold).
- Deals = `opportunities[]`: `id, title, stage, worth_up_to, size, blocker {text, status: open|cleared}, evidence [fact and file ids], deadline {text, ...} | null`. Stages: identified < qualified < cleared (blocker gone) < agreed. `worth_up_to` is null until qualified.
- Offers = `offers[]`: `id, title, value, pitch_to, status` (open | won | declined).
- `said_by` and `pitch_to` hold a stakeholder's NAME, not an id. Resolve each to a stakeholder id by exact name match within the account. Flag any that don't resolve; never guess a match.
- Files: top-level `files[]` (`id, title, path, account`). Map each file to its account via `account`. Content comes from get_file(id).
- Paperwork: get_all_paperwork returns `records[]` of `{path, kind, text | data}` (calls, emails, contract, tickets, invoices, users, devices, flights/usage/device_events CSVs, ...). It shows symptoms; mismatches with the official story are important findings.
- "Official story" = `official_story`, the account sheet, unverified.
- Accounts without a sheet: a new account can arrive as a drop (whoami `new_accounts[]` = `{id, name, path: /drops/<id>}`). It is not in get_my_accounts, so it has no official story, stakeholders, facts, opportunities or offers. Its paperwork comes from get_all_paperwork; its people come from `profile.json` `contacts_on_file` (kind `contact`, relationship unknown).
- Signals: each signal's `records[]` are paperwork for its account. A record whose path matches an existing file and whose note says "Rows to add" is merged into that file (e.g. `B/usage_weekly.csv`, `A/requests.csv`). Each merged row keeps the signal as its source.

## Ids and citations
- Every id is account-scoped: the key is `<account>/<id>` everywhere (store, id_index, `sources`, UI). E.g. `A/km_usage_1`, `B/insurer_premium_letter`. The same raw id can exist in both accounts (`U01`, `T001`, `contract.txt`).
- Paperwork records: `<account>/<path>`, e.g. `A/emails/2026-10-28_renewal.txt`. Signal records already carry this form.
- Rows inside paperwork use their own id field, account-scoped: ticket `id` (`A/FB-10412`), invoice `number`, dock `dock_id`, user `user_id` (`A/U01`), mail `thread`, requests.csv `request_id`.
- Shared sources are scoped by source: `team/<id>` (get_flytbase_team), `feature_requests/<id>`. Signals use their `account`: `A/champion_leaves`.
- People in `profile.json` `contacts_on_file` are `<account>/<id>` (kind `contact`), e.g. `K/k_dana_15x`. When the same id is also a stakeholder, the stakeholder is the primary entry.
- Placeholders are never ids: a `request_id` of "(none)" or an empty cell gets no id. Cite that row through its file (`A/requests.csv`).
- Deadlines have no id. Cite a deadline through its opportunity: `<account>/<opportunity_id>#deadline`. An entry in `deadlines[]` is cited through the opportunity whose `deadline.text` matches it. Never make up a standalone deadline id; flag a deadline that matches no opportunity.

## Time
- Game time is in minutes: `minute` on the export, `learned_at_minute` on facts, `deadline.minute` when present. The schema requires `deadlines[].minute`, but the data can omit it, and deadlines may carry extra fields (`soon`, `met`, `moves_left`). Handle both.
- The in-game date comes from the paperwork (latest dated records, e.g. devices `last_seen_online`, users `last_sign_in`) and moves forward with signals (`dated`). AS_OF overrides it.

## Engine decisions (from the analyst; follow them in Steps 3 to 5)
- Backed worth: keep the full `worth_up_to` when verified evidence backs the deal (e.g. `A/a_renewal` 350,000). Where only part of it is backed (docks that never fly), show that part as a Q4 risk citing the flight-data ids. Don't prorate.
- A worth equal to a claim that the paperwork contradicts is unbacked (claim), even with verified evidence attached (`B/b_sister`).
- Offers won with no order on file: excluded from both totals, shown on their own line as "Agreed, no order yet".
- Manual maps in config/rules.yaml (roles, backing, extra evidence such as `A/rc_stadium_0` on `A/a_westmere`) are minimal. Derive from data wherever possible. Every manual entry cites ids and renders as "analyst judgment".
- Inferences (e.g. `A/FB-10412` is Fatima Osei's request) render labelled "inference" with their supporting ids, and are also listed as Q5 gaps to confirm. Never render an inference as a fact. "B's patrols stopped because the deputy left" stays an inference unless a record proves it.
- Thresholds live in rules.yaml: customer waiting on us amber 7 / red 14 days; cold contact amber 30 / red 60 days.

## Hard rules
- RATE LIMIT: one MCP call per minute per token, writes included. Inside the app, only the scheduler module may call the MCP. Outside the app, only scripts/pull.py, run by hand. Nothing else calls it, ever.
- Never run scripts/pull.py while a live app (LIVE_MODE=true) is running anywhere with the same token: they share the budget.
- Local development runs on snapshots in data/raw/ with LIVE_MODE=false. Claude may make live READ calls through scripts/pull.py whenever useful (standing approval; the rate limit still applies), but never while a live app runs with the same token. Writes (write_note) only come from the scheduler in Step 5.
- The token lives only in the AQ_TOKEN env var. Never log it, print it, commit it, or send it to the browser.
- Never invent an id, a fact, or a person. Every engine output item has a `sources: [ids]` list, and a validator drops any item citing an id not in the store.
- data/ and .env are gitignored. The repo is public.
- Keep code simple and readable. Python 3.11, FastAPI, SQLite, Jinja2 + vanilla JS. No frontend build step.
- Run uvicorn with exactly 1 worker (one poller only).

## MCP call shape
POST https://questathon.aihackerscollective.com/api/public/mcp
headers: content-type: application/json; accept: application/json, text/event-stream; authorization: Bearer $AQ_TOKEN
body: {"jsonrpc":"2.0","id":<int>,"method":"tools/call","params":{"name":<tool>,"arguments":{...}}}
Response may be JSON or SSE (parse `data:` lines). If error or result.isError, the error text is in result.content[0].text. Success payload = json.loads(result.content[0].text).

Pull script (scripts/pull.py), one call at a time, at least 65s apart:
- no flags: pull everything missing; `--only NAME`, `--force NAME`: one source.
- `--refresh`: whoami + get_my_accounts + get_signals, then only what changed (new or opened account, new file, new signal). The old copy of a changed payload moves to data/raw/_history/.
- `--list-tools`: JSON-RPC `tools/list`, flags tools not listed below. `--search Q --account X`: search_paperwork.
- Then `python scripts/dump.py` (offline) rewrites data/dump/, including changes.md and index.md.

Tools: whoami, get_my_accounts, get_file(id), list_paperwork(account?), get_all_paperwork(account), search_paperwork(q, account?), get_paperwork_record(account, path), get_feature_requests, get_release_notes, get_flytbase_team, get_signals, get_data_format, list_notes, write_note(text, account?, kind?, title?) where kind is note|task|risk|next_step.

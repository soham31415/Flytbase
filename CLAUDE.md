# Questathon CRM: project rules

## What this is
A live CRM for two customer accounts (A and B) of FlytBase (drone docks). It reads data from a hackathon MCP server and, for each account, answers five questions:
1. What is the book worth? (each deal at a value we can back, with the backing ids; unbacked values shown apart)
2. Who holds the power? (per deal: decider, payer, user, blocker, champion, and stance with us)
3. What do we do first? (top 3 moves, ranked, each with owner, date, and the deal it moves)
4. What is about to go wrong? (deadlines at risk, people going cold, stuck deals, and when each turns bad)
5. What do we still not know? (gaps the plan rests on, why they matter, who could fill each)
Judges score: every number cites its source id, no invented ids, ranked/owned/dated moves, honest gaps, and it stays live.

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

## Ids and citations
- Every id is account-scoped: the key is `<account>/<id>` everywhere (store, id_index, `sources`, UI). E.g. `A/km_usage_1`, `B/insurer_premium_letter`. The same raw id can exist in both accounts (`U01`, `T001`, `contract.txt`).
- Paperwork records: `<account>/<path>`, e.g. `A/emails/2026-10-28_renewal.txt`. Signal records already carry this form.
- Rows inside paperwork use their own id field, account-scoped: ticket `id` (`A/FB-10412`), invoice `number`, dock `dock_id`, user `user_id` (`A/U01`), mail `thread`, requests.csv `request_id`.
- Shared sources are scoped by source: `team/<id>` (get_flytbase_team), `feature_requests/<id>`. Signals use their `account`: `A/champion_leaves`.
- Deadlines have no id. Cite a deadline through its opportunity: `<account>/<opportunity_id>#deadline`. An entry in `deadlines[]` is cited through the opportunity whose `deadline.text` matches it. Never make up a standalone deadline id; flag a deadline that matches no opportunity.

## Time
- Game time is in minutes: `minute` on the export, `learned_at_minute` on facts, `deadline.minute` when present. The schema requires `deadlines[].minute`, but the data can omit it, and deadlines may carry extra fields (`soon`, `met`, `moves_left`). Handle both.
- The in-game date comes from the paperwork (latest dated records, e.g. devices `last_seen_online`, users `last_sign_in`) and moves forward with signals (`dated`). AS_OF overrides it.

## Hard rules
- RATE LIMIT: one MCP call per minute per token, writes included. Only the scheduler module may call the MCP. Nothing else calls it, ever.
- Local development runs on snapshots in data/raw/ with LIVE_MODE=false. Never call the live API during development unless I explicitly ask.
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

Tools: whoami, get_my_accounts, get_file(id), list_paperwork(account?), get_all_paperwork(account), search_paperwork(q, account?), get_paperwork_record(account, path), get_feature_requests, get_release_notes, get_flytbase_team, get_signals, get_data_format, list_notes, write_note(text, account?, kind?, title?) where kind is note|task|risk|next_step.

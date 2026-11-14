# Questathon CRM: Step-by-Step Build Plan for Claude Code

How to use this:
1. Do **Step 0** first. It creates `CLAUDE.md`, which Claude Code reads every session, so later prompts can stay short.
2. Paste one step prompt at a time. Don't move on until that step's **Done when** list is all true.
3. Commit after every step (`git add -A && git commit -m "step N"`).
4. Step 2 is your thinking, not Claude Code's. The quality of the five answers comes from it.

## Timeline (IST, ends 03:47)

| Time | Step | Output |
|---|---|---|
| 14:45 | 0 + 1 | Repo, CLAUDE.md, data pulled to disk |
| 15:00 | 2 | You understand both accounts, findings file written |
| 16:15 | 3 | Store + engine answering the five questions from snapshots |
| 18:30 | 4 | UI with source tracing |
| 19:45 | 5 | Live poller (signals, write_note) |
| 20:30 | 6 | Deployed on Railway, tested in a private window |
| 21:00 | 7 | **v1 handed in (all 4 links)** |
| 22:00 | 8 | Above and beyond |
| 02:30 | 9 | Final video, doc, README, re-hand-in by 03:15 |

---

## Step 0: CLAUDE.md (project rules)

````text
Create CLAUDE.md in the repo root with exactly this content, then stop.

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
- Every person, fact, deal and file has an id. Cite ids everywhere.
- Fact status: claim (may be wrong) < told (a person said it) < verified (backed by a file we hold).
- People: only ones met; stance guarded | neutral | warm | ally.
- Deal stages: identified < qualified < cleared (blocker gone) < agreed. Worth is only shown once qualified.
- "Official story" = the account sheet, unverified. Paperwork (calls, emails, contracts, tickets, flights, devices, logins, invoices) shows symptoms; mismatches with the official story are important findings.
- The schema of record is data/dump/schema.md (from get_data_format). Use real field names from it; never guess.

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
````

---

## Step 1: Scaffold + one-time data pull

````text
Step 1. Scaffold the repo and write the one-time pull. Don't build the web app yet. Follow CLAUDE.md.

1. Repo setup
- venv, requirements.txt with httpx, python-dotenv (FastAPI etc. come later).
- Structure:
  scripts/pull.py, scripts/dump.py, mcp_client.py
  data/raw/, data/dump/ (gitignored)
  .env (gitignored), .env.example (AQ_TOKEN=), .gitignore (.env, data/, venv/, __pycache__/), README.md stub
- git init, and confirm data/ and .env are ignored before the first commit.

2. mcp_client.py
- call_tool(name, args) -> parsed payload. Implements the call shape in CLAUDE.md, handles JSON and SSE, timeout 60s, raises McpError(text) on error. Reused later by the scheduler.

3. scripts/pull.py
- One call at a time, 65s sleep between calls, in this order (tool -> file name):
  whoami -> whoami; get_data_format -> get_data_format; get_my_accounts -> get_my_accounts;
  get_all_paperwork {account:"A"} -> get_all_paperwork_A; get_all_paperwork {account:"B"} -> get_all_paperwork_B;
  get_flytbase_team; get_feature_requests; get_release_notes; list_notes; get_signals.
- After get_my_accounts: if the account identifiers are clearly not "A"/"B", use the real ones and log it.
- Save the payload to data/raw/<file>.json and the raw HTTP body to data/raw/_http/<file>.txt. Print one line per call: time, tool, ok/error, bytes, top-level keys.
- On error, save data/raw/<file>.error.txt. On a rate-limit error, wait 70s and retry once, then move on. No tight loops.
- Resumable: skip files that already exist unless --force <name>. Support --only <name>. Print the expected total time at the start.

4. scripts/dump.py (offline, makes NO network calls)
- Reads data/raw/*.json and writes markdown to data/dump/:
  schema.md (pretty get_data_format);
  account_<id>.md: official story, people (id, name, role, stance), facts (id, text, status, source), deals (id, stage, worth, linked people/facts), offers, deadlines, files, and paperwork grouped by type and sorted by date with ids/paths. Discover field names from the JSON; don't assume them.
  team.md, feature_requests.md, release_notes.md, signals.md, notes.md;
  index.md: counts per entity type per account, plus every id found anywhere and the file it came from.

When done, show me the commands to run and stop. Do NOT run pull.py yourself.
````

**Done when:** `data/raw/` has 10 JSON files, `data/dump/` has readable markdown, and `git status` shows no data or .env files.

**If paperwork is locked** (whoami says it isn't open yet), rerun `python scripts/pull.py --only get_all_paperwork_A` later.

---

## Step 2: Understand the accounts (you + Claude Code as an assistant)

First, have Claude Code draft the findings:

````text
Step 2. Read everything in data/dump/ (start with schema.md and index.md). For EACH account, write data/analysis/findings_<id>.md with these sections. Cite ids for every line, and if something isn't in the data, write "UNKNOWN" instead of guessing.

1. Deals: id, what it is, stage, worth, and the fact/file ids behind the worth, with the status of each (claim/told/verified).
2. People: id, name, title, stance, and which role they play on which deal (decider/payer/user/blocker/champion). List roles nobody fills.
3. Official story vs paperwork: every place the paperwork (flights, logins, devices, tickets, invoices, emails, calls, contracts) contradicts or doesn't support the official story or a told/claim fact. Quote record ids/paths.
4. Deadlines and offers: id, date, what's at stake, linked deal, and whether the blocker is cleared.
5. Engagement: the last contact date per person from paperwork, and anyone who looks cold.
6. Open questions: what the plan depends on that the data doesn't settle, and who (person id or FlytBase team member) could answer it.
7. The in-game "today" date, and where it comes from.

Also write data/analysis/rules_draft.md: concrete rules that would reproduce your conclusions from the data (thresholds, weights, how to detect each contradiction type). Do not write app code.
````

**Then you:** read both findings files against the dump and correct them. Edit `rules_draft.md` until you agree with every rule. This is what the judges are really scoring.

**Done when:** you could explain each account's five answers out loud, with ids.

---

## Step 3a: Store + API skeleton

````text
Step 3a. Build the data layer. Add fastapi, uvicorn, jinja2, pytest to requirements.

- app/store.py: SQLite at $DATA_DIR/crm.db (default ./data).
  - raw_payloads(source TEXT, account TEXT NULL, fetched_at TEXT, payload JSON): append-only history of every MCP response.
  - Normalized tables built from the latest payloads, using the real schema in data/dump/schema.md: people, facts, deals, deal links (deal-person-role, deal-fact), deadlines, offers, files, paperwork records, signals, notes.
  - id_index(id TEXT PRIMARY KEY, kind, account, source, record JSON): every id we hold. Used by the validator and the trace view.
  - rebuild(): re-derives the normalized tables and id_index from the latest raw payloads. Idempotent.
  - bootstrap_from_snapshots(): if the DB is empty and data/raw/ exists, load those files as raw_payloads and rebuild.
- app/main.py: FastAPI app. On startup it bootstraps and rebuilds.
  - GET /api/health -> {ok, as_of, counts per table, last_sync}
  - GET /api/record/{id} -> the full record for any id from id_index (404 if unknown)
- tests/test_store.py: loads the snapshots, asserts counts match data/dump/index.md, and checks that every id in index.md resolves via /api/record.

Run the tests. LIVE_MODE stays false; no MCP calls.
````

**Done when:** tests pass and `/api/record/<any id>` returns the record.

## Step 3b: The engine (five answers)

````text
Step 3b. Build the engine using data/analysis/rules_draft.md and findings_*.md as the spec. Follow CLAUDE.md.

- config/rules.yaml: every threshold and weight, so I can tune without code changes (stage weights e.g. identified .1, qualified .3, cleared .6, agreed .9; urgency curve; cold-contact days; deadline-risk window; etc.).
- Item shape (every output item): {id: stable hash, text, value: number|null, status: verified|told|claim|derived, severity?: red|amber|green, due?: date, owner?: str, deal_id?: str, sources: [ids], why: short string}
- app/engine/q1_book.py: per deal, worth only if stage >= qualified. "backed" only if supported by a verified fact or a file; otherwise put it in an "unbacked" list labelled told or claim. Totals for backed and unbacked are kept separate, each listing its source ids.
- app/engine/q2_power.py: per deal, decider/payer/user/blocker/champion -> person id, stance, and what they're waiting on if known. Empty roles are emitted as gaps for q5.
- app/engine/q3_moves.py: score = worth (backed first, else unbacked x 0.5) x stage weight x urgency(days to the nearest linked deadline) + the risk it defuses. Top 3 per account, each with owner (FlytBase team member or "me"), a date strictly before the linked deadline, and the deal it moves. Show the score breakdown in `why`.
- app/engine/q4_risks.py: deadline within window and blocker not cleared; key person guarded or no contact in N days; deal stuck at one stage; official story or claim contradicted by paperwork. Each risk has severity and a "turns bad on" date.
- app/engine/q5_gaps.py: empty roles, moves or worth that rest only on claim/told facts, and unexplained contradictions. Each has why it matters and who to ask (person id or team member).
- app/engine/validate.py: drops any item with empty sources or any source not in id_index, and logs what it dropped.
- app/engine/run.py: compute(as_of) -> {as_of, accounts: {id: {q1..q5}}, dropped: [...]}. as_of = in-game today (from data), overridable by env.
- GET /api/state returns the computed result, cached and recomputed after every rebuild().
- tests/test_engine.py: both accounts have all five answers non-empty, every source resolves, every q3 date is before its deadline, and spot-checks against findings_*.md.
````

**Done when:** `/api/state` gives all five answers for both accounts, and you agree with them after comparing against your findings.

---

## Step 4: UI

````text
Step 4. Build the UI: Jinja2 + vanilla JS, served by FastAPI at "/". No build step.

- Header: account tabs (A, B), in-game date, "last synced X min ago", and a live-events count.
- Per account, five sections in question order. Use the question text as the heading, followed by a one-line answer.
  - Q1: table of deals (deal, stage, backed worth, unbacked worth + status badge) with totals shown apart. Unbacked rows are visually distinct.
  - Q2: one card per deal with role -> person, stance chip, waiting-on.
  - Q3: a numbered list of 3 moves: action, owner, date, deal, and the score shown on hover.
  - Q4: a risk list with red/amber/green, "turns bad on" date, sorted by date.
  - Q5: gaps with why it matters and who to ask.
- Every number and every item has its source ids as small chips. Clicking a chip opens a side drawer with GET /api/record/{id} rendered readably (paperwork shown as the record text). This trace is the core feature.
- Right rail: live events feed (signals, newest first, with what changed because of each) and notes written back.
- JS polls GET /api/state every 15s and re-renders only if the data changed. Briefly highlight new items.
- Clean, dense, readable. Should read like a manager's Monday brief, not a raw data dump.
````

**Done when:** a stranger could open it and answer the five questions for both accounts, and click through to the evidence.

---

## Step 5: Stay live (scheduler + write-back)

````text
Step 5. Build app/scheduler.py: the ONLY code that calls the MCP. Follow CLAUDE.md.

- Runs as an asyncio background task started on app startup, only when LIVE_MODE=true.
- Budget: at most 1 call per 62s, enforced by a single lock and a persisted last_call_at (survives restarts). On a rate-limit error, back off 90s.
- Priority queue, picking one job per tick:
  1. bootstrap: if the store has no raw payloads, run the Step 1 sequence (whoami, get_data_format, get_my_accounts, get_all_paperwork per account, get_flytbase_team, get_feature_requests, get_release_notes, list_notes, get_signals).
  2. pending write_note jobs.
  3. follow-ups triggered by new signals (e.g. get_paperwork_record / get_file / re-pull get_my_accounts or get_all_paperwork for the affected account; decide from the signal content).
  4. get_signals every 3 min.
  5. whoami every 10 min (watch for newly opened content).
  6. get_my_accounts refresh every 30 min.
- Every response is stored in raw_payloads, then rebuild() and an engine recompute run.
- New signal -> store it, enqueue follow-ups, recompute, and record which answers changed (shown in the UI feed).
- Write-back: after each recompute, for each account write the top 3 moves as kind=next_step and red risks as kind=risk, with title + text including source ids. Dedupe by a stable key against list_notes and against our own sent log, so we never write the same note twice. Max 1 write per 5 min so writes don't starve signal polling.
- GET /api/health also shows: queue contents, last 20 calls (time, tool, ok), next scheduled call.
- tests: simulate a new signal from a fixture and assert the follow-up is queued and the state recomputes. Mock the MCP client; no real calls.
````

**Done when:** with a mocked signal, the UI updates by itself and a note is queued.

---

## Step 6: Deploy (Railway)

````text
Step 6. Make the app deployable on Railway.

- Dockerfile (python:3.11-slim). CMD: uvicorn app.main:app --host 0.0.0.0 --port $PORT --workers 1
- DATA_DIR=/data (a Railway volume mounted at /data). The DB lives there.
- On an empty volume with LIVE_MODE=true, the scheduler's bootstrap job pulls everything (about 11 min). The UI shows "syncing: N of 10 sources loaded" until then instead of empty answers.
- Env vars: AQ_TOKEN, LIVE_MODE=true, DATA_DIR=/data. Optional: AS_OF override.
- README "Deploy" section with exact Railway steps.
- Double-check: no token in any file, data/ not in git, /api/* never returns the token.
````

Then do it yourself:
1. Push to a **public** GitHub repo.
2. In Railway: new project from the repo, add a volume at `/data`, set the env vars, deploy.
3. **Stop any local live runs**, since they share the token's budget.
4. Open the URL in a private window. Wait for sync, then check `/api/health`.

**Done when:** the hosted link shows both accounts in a private window, and `/api/health` shows `get_signals` polling.

---

## Step 7: Hand in v1 (by about 21:00)

````text
Step 7. Write README.md for a judge: what it is (2 lines), the live link, how it works (diagram in text: MCP -> scheduler (1 call/min) -> SQLite -> engine (rules.yaml) -> validator -> UI), how each of the five questions is computed, how to run locally from snapshots (LIVE_MODE=false) and live, env vars, and project layout. No token, no account data.
````

You write these yourself. Use plain language; this is where your own voice counts.
- **Thinking doc (one page, Google Doc):** what it does and for whom; how it decides (the rules, with the key thresholds); what it doesn't do yet; how you used AI (Claude for the plan, scaffolding and the data analysis draft; you for verifying findings and the rules).
- **Video (3 min, Drive):**
  - 0:00 to 1:30: the five answers for one account.
  - 1:30 to 2:00: click one number and trace it to its source record.
  - 2:00 to 2:30: show a live event arriving and what changed.
  - 2:30 to 3:00: a quick look at the code (scheduler, engine, validator).
- Submit all four links. You can keep updating them.

---

## Step 8: Above and beyond (pick one or two, do them well)

````text
Step 8a. Contradiction detector: a dedicated module that cross-checks each told/claim fact and the official story against the paperwork (flight counts vs claimed usage, logins vs claimed adoption, invoices vs claimed spend, tickets vs claimed satisfaction, device status vs claimed deployment). Each finding: claim id, evidence ids, a plain-English mismatch, and its impact on deal worth or risk. Feed findings into q1 (downgrade worth), q4 and q5. Add a "Story vs evidence" section to the UI.
````

````text
Step 8b. Power map with waiting-on: per deal, a small graph (people as nodes, role labels, stance colour), plus for each person what they're waiting on from us and since when (from paperwork). Flags a decider or payer we have never met or who has gone cold.
````

````text
Step 8c. Draft writer: for each top-3 move, generate a draft (customer email or internal request with owner + date) using an LLM, given ONLY the cited records. Post-validate: any id in the draft must be in id_index, otherwise reject and regenerate. Show the drafts with their source chips. The LLM key is a server env var.
````

````text
Step 8d. "Monday brief" export: a print-friendly one-page view per account (the five answers + ids) at /brief/<account>.
````

---

## Step 9: Final pass (02:30 to 03:15)

- [ ] Hosted link works in a private window, both accounts, all five answers
- [ ] Every number has an id chip that resolves
- [ ] `/api/health` shows recent `get_signals` calls and written notes
- [ ] The latest live events show up in the feed
- [ ] Repo is public, README is accurate, `git log -p | grep -i bearer` finds nothing
- [ ] Doc reflects the final rules and what's not done
- [ ] Video re-recorded with the final version and a real live event
- [ ] All four links re-submitted **before 03:30**

Be ready for a live walkthrough: lead with account A's Q3 (what you do Monday), then trace one number to its source.
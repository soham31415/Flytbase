# Questathon CRM

A live CRM for two FlytBase customer accounts. For each account it answers: what the book is worth, who holds the power, what to do first, what is about to go wrong, and what we still don't know. Every answer cites the source ids it rests on.

Work in progress. See `CLAUDE.md` for project rules.

## Setup

```bash
python -m venv venv
venv\Scripts\activate          # Windows (source venv/bin/activate elsewhere)
pip install -r requirements.txt
cp .env.example .env           # then set AQ_TOKEN
```

## One-time data pull

The MCP server allows one call per minute per token, so the pull takes about 10 minutes.

```bash
python scripts/pull.py          # writes data/raw/*.json (resumable)
python scripts/pull.py --refresh   # later: re-check the server, pull only what changed
python scripts/dump.py          # offline: writes readable markdown to data/dump/
```

`data/` and `.env` are gitignored.

## Run locally

```bash
uvicorn app.main:app --reload       # http://localhost:8000
pytest                              # run the tests
```

Locally, keep `LIVE_MODE=false` so the app works from the snapshots in `data/raw/` and makes no MCP calls.
On startup the app adds any snapshot payloads its database doesn't hold yet (`data/crm.db`) and rebuilds.
After a new pull, `POST /api/reload` does the same without a restart.

API so far:
- `GET /api/health`: in-game date, accounts, last sync, table counts, unresolved references.
- `GET /api/record/{id}`: the full record behind any cited id, e.g. `/api/record/A/km_usage_1`. A deadline id contains `#`; send it as `%23` (`/api/record/B/b_idle%23deadline`).
- `POST /api/reload` (snapshot mode only): re-read `data/raw/` and rebuild.
In production run exactly one worker (`--workers 1`), because only one poller may exist.

## Environment variables

| Variable | Default | Meaning |
|---|---|---|
| `AQ_TOKEN` | (none) | MCP token. Used only by the scheduler; never logged or sent to the browser. |
| `LIVE_MODE` | `false` | `true` starts the scheduler that polls the MCP (1 call/min). |
| `DATA_DIR` | `./data` | Where the SQLite DB lives (`/data` on Railway, a mounted volume). |
| `SNAPSHOT_DIR` | `$DATA_DIR/raw` | Snapshots loaded at startup and on `/api/reload`. |
| `AS_OF` | (from data) | Optional override for the in-game "today" date. |
| `PORT` | `8000` | Port for the Docker image. |

Values in `.env` are loaded automatically; real env vars take precedence.

## Docker

```bash
docker build -t questathon-crm .
docker run -p 8000:8000 -e LIVE_MODE=false questathon-crm
```

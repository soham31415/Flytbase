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
python scripts/dump.py          # offline: writes readable markdown to data/dump/
```

`data/` and `.env` are gitignored.

## Run locally

```bash
uvicorn app.main:app --reload       # http://localhost:8000
pytest                              # run the tests
```

Locally, keep `LIVE_MODE=false` so the app works from the snapshots in `data/raw/` and makes no MCP calls.
In production run exactly one worker (`--workers 1`), because only one poller may exist.

## Environment variables

| Variable | Default | Meaning |
|---|---|---|
| `AQ_TOKEN` | (none) | MCP token. Used only by the scheduler; never logged or sent to the browser. |
| `LIVE_MODE` | `false` | `true` starts the scheduler that polls the MCP (1 call/min). |
| `DATA_DIR` | `./data` | Where the SQLite DB lives (`/data` on Railway, a mounted volume). |
| `AS_OF` | (from data) | Optional override for the in-game "today" date. |
| `PORT` | `8000` | Port for the Docker image. |

Values in `.env` are loaded automatically; real env vars take precedence.

## Docker

```bash
docker build -t questathon-crm .
docker run -p 8000:8000 -e LIVE_MODE=false questathon-crm
```

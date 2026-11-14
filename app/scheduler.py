"""The scheduler: the ONLY code allowed to call the MCP (Step 5).

Planned role: an asyncio background task, started only when LIVE_MODE=true,
that makes at most one call per 62s (single lock, persisted last_call_at) and
picks one job per tick from a priority queue: bootstrap pull, pending
write_note jobs, signal follow-ups, get_signals every 3 min, whoami every
10 min, get_my_accounts every 30 min. Every response goes to raw_payloads,
then the store rebuilds and the engine recomputes.
"""

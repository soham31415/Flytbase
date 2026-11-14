"""Minimal client for the Questathon MCP server.

Only scripts/pull.py (one-time pull) and, later, the scheduler may use this.
The token is read from the AQ_TOKEN env var and is never logged or returned.
"""

import itertools
import json
import os

import httpx

MCP_URL = "https://questathon.aihackerscollective.com/api/public/mcp"
TIMEOUT_S = 60

_request_ids = itertools.count(1)


class McpError(Exception):
    """An MCP call failed. str(err) is the server's error text (token redacted)."""

    def __init__(self, text, raw_body=None, status_code=None):
        super().__init__(text)
        self.text = text
        self.raw_body = raw_body
        self.status_code = status_code

    @property
    def is_rate_limit(self):
        if self.status_code == 429:
            return True
        t = self.text.lower()
        return (
            ("rate" in t and "limit" in t)
            or "too many requests" in t
            or "retry after" in t
            or "429" in t
        )


def _token():
    token = os.environ.get("AQ_TOKEN", "").strip()
    if not token:
        raise McpError("AQ_TOKEN is not set")
    return token


def redact(text):
    """Remove the token from any string before it is printed or saved."""
    token = os.environ.get("AQ_TOKEN", "").strip()
    if token and text:
        text = text.replace(token, "***")
    return text


def parse_body(body):
    """Return the JSON-RPC message from a JSON or SSE response body."""
    body = body.strip()
    if not body:
        raise McpError("empty response body")
    if body.startswith("{") or body.startswith("["):
        return json.loads(body)

    # SSE: events are separated by blank lines; an event's data may span several `data:` lines.
    messages = []
    for event in body.replace("\r\n", "\n").split("\n\n"):
        data_lines = [
            line[5:].lstrip(" ") if line.startswith("data:") else None
            for line in event.split("\n")
        ]
        data = "\n".join(d for d in data_lines if d is not None).strip()
        if not data:
            continue
        try:
            messages.append(json.loads(data))
        except json.JSONDecodeError:
            continue
    # The last message carrying a result or error is the answer to our call.
    for msg in reversed(messages):
        if isinstance(msg, dict) and ("result" in msg or "error" in msg):
            return msg
    raise McpError("no JSON-RPC result in SSE body")


def _content_text(result):
    content = (result or {}).get("content") or []
    if content and isinstance(content[0], dict):
        return content[0].get("text", "")
    return ""


def call_tool_raw(name, args=None):
    """Call one MCP tool. Returns (payload, raw_http_body). Raises McpError."""
    headers = {
        "content-type": "application/json",
        "accept": "application/json, text/event-stream",
        "authorization": f"Bearer {_token()}",
    }
    body = {
        "jsonrpc": "2.0",
        "id": next(_request_ids),
        "method": "tools/call",
        "params": {"name": name, "arguments": args or {}},
    }
    try:
        resp = httpx.post(MCP_URL, headers=headers, json=body, timeout=TIMEOUT_S)
    except httpx.HTTPError as e:
        raise McpError(redact(f"{type(e).__name__}: {e}")) from None

    raw = redact(resp.text)
    try:
        msg = parse_body(raw)
    except (McpError, json.JSONDecodeError):
        if resp.status_code >= 400:
            raise McpError(f"HTTP {resp.status_code}: {raw[:500]}", raw, resp.status_code) from None
        raise McpError(f"unparseable response: {raw[:500]}", raw, resp.status_code) from None

    if msg.get("error"):
        err = msg["error"]
        text = err.get("message", str(err)) if isinstance(err, dict) else str(err)
        raise McpError(text, raw, resp.status_code)

    result = msg.get("result") or {}
    text = _content_text(result)
    if result.get("isError"):
        raise McpError(text or "tool returned isError", raw, resp.status_code)
    if resp.status_code >= 400:
        raise McpError(f"HTTP {resp.status_code}: {text or raw[:500]}", raw, resp.status_code)

    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        payload = text  # some tools may answer in plain text
    return payload, raw


def call_tool(name, args=None):
    """Call one MCP tool and return its parsed payload. Raises McpError."""
    payload, _ = call_tool_raw(name, args)
    return payload

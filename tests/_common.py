"""Shared helpers for the Phase 0 checks."""

import json
import os
import socket

PORT = int(os.environ.get("DTH_HOUDINI_MCP_PORT", "8911"))


def token_path():
    override = os.environ.get("DTH_HOUDINI_MCP_TOKEN_FILE")
    if override:
        return override
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    return os.path.join(base, "dth-houdini-mcp", "token")


def read_token():
    with open(token_path(), "r", encoding="utf-8") as handle:
        return handle.read().strip()


def call(cmd, args=None, port=PORT, token=None, include_token=True):
    """One request, one response. Pass include_token=False to test rejection."""
    request = {"id": "t", "cmd": cmd, "args": args or {}}
    if include_token:
        request["token"] = token if token is not None else read_token()

    conn = socket.create_connection(("127.0.0.1", port), timeout=15)
    try:
        conn.sendall((json.dumps(request) + "\n").encode("utf-8"))
        buffer = b""
        while b"\n" not in buffer:
            chunk = conn.recv(65536)
            if not chunk:
                raise RuntimeError("listener closed before replying")
            buffer += chunk
    finally:
        conn.close()
    return json.loads(buffer.split(b"\n", 1)[0].decode("utf-8"))

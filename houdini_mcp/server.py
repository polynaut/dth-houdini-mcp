"""DTH Houdini MCP server -- PHASE 0 SPIKE.

A stdio MCP server the client launches. It owns no Houdini state: it forwards
JSON commands to the listener running inside a live Houdini session
(`houdini_side/dth_listener.py`) over a loopback socket.

Phase 0 exposes exactly one tool, `houdini_ping`, whose only job is to prove
the round trip: MCP client -> stdio server -> socket -> Houdini main thread
-> back.

Verified against mcp 2.0.0 (`MCPServer`, `.tool()`, `.run("stdio")`); that
release has no `mcp.server.fastmcp`.
"""

import json
import os
import socket
import time

from mcp.server import MCPServer

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = int(os.environ.get("DTH_HOUDINI_MCP_PORT", "8911"))
CONNECT_TIMEOUT = float(os.environ.get("DTH_HOUDINI_MCP_TIMEOUT", "15"))


def token_path():
    override = os.environ.get("DTH_HOUDINI_MCP_TOKEN_FILE")
    if override:
        return override
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    return os.path.join(base, "dth-houdini-mcp", "token")


def read_token():
    """Read the token the listener wrote at start().

    Read per request, not cached at import: the listener issues a fresh token
    every run, so restarting Houdini must not require restarting this server.
    """
    path = token_path()
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return handle.read().strip()
    except OSError:
        raise ListenerError(
            "no listener token at %s.\n"
            "The listener writes it when start() runs inside Houdini -- "
            "start it, or point DTH_HOUDINI_MCP_TOKEN_FILE at the right path." % path
        )

mcp = MCPServer(
    name="dth-houdini",
    version="0.0.1-phase0",
    instructions=(
        "Inspect a LIVE Houdini session for the DazToHue (DTH) pipeline. "
        "Requires the DTH listener to be started inside Houdini first "
        "(Python Shell: exec the dth_listener.py file, then call start()). "
        "Phase 0 is read-only and exposes only houdini_ping."
    ),
)


class ListenerError(RuntimeError):
    pass


def call_listener(cmd, args=None, host=DEFAULT_HOST, port=DEFAULT_PORT, timeout=CONNECT_TIMEOUT):
    """One request, one response, one connection.

    Phase 0 keeps connections short-lived rather than pooling: a Houdini that
    restarts mid-session must not leave a dead socket that looks alive.
    """
    request = {
        "id": "%d" % time.time_ns(),
        "cmd": cmd,
        "args": args or {},
        "token": read_token(),
    }
    payload = (json.dumps(request) + "\n").encode("utf-8")

    try:
        conn = socket.create_connection((host, port), timeout=timeout)
    except OSError as exc:
        raise ListenerError(
            "cannot reach the Houdini listener at %s:%s (%s).\n"
            "Is Houdini running, and was the listener started?\n"
            "  In Houdini's Python Shell:\n"
            "    exec(open(r'<repo>/houdini_side/dth_listener.py').read())\n"
            "    start()" % (host, port, exc)
        )

    try:
        conn.settimeout(timeout)
        conn.sendall(payload)
        buffer = b""
        while b"\n" not in buffer:
            chunk = conn.recv(65536)
            if not chunk:
                raise ListenerError("listener closed the connection before replying")
            buffer += chunk
    except socket.timeout:
        raise ListenerError(
            "listener did not reply within %.0fs -- Houdini's main thread is "
            "likely busy cooking or showing a modal dialog" % timeout
        )
    finally:
        conn.close()

    response = json.loads(buffer.split(b"\n", 1)[0].decode("utf-8"))
    if not response.get("ok"):
        error = response.get("error") or {}
        raise ListenerError("%s: %s" % (error.get("type", "Error"), error.get("message", "unknown")))
    return response["result"]


@mcp.tool(
    name="houdini_ping",
    description=(
        "Round-trip check against the live Houdini session. Returns the Houdini "
        "version, the currently open .hip, and proof of which thread the call ran "
        "on. Read-only: it does not modify or save the scene."
    ),
)
def houdini_ping(message: str = "") -> dict:
    """Ping the live Houdini session.

    Args:
        message: Optional text echoed back, to prove the payload survives the round trip.
    """
    started = time.time()
    result = call_listener("ping", {"message": message})
    result["client_round_trip_ms"] = round((time.time() - started) * 1000.0, 2)
    return result


if __name__ == "__main__":
    mcp.run("stdio")

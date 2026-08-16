"""Phase 0 check D: can HTTP-shaped traffic reach the command dispatcher?

The listener speaks newline-delimited JSON, not HTTP. That is sometimes assumed
to put it out of a browser's reach. It does not: a browser can POST to
127.0.0.1 with a text/plain body it controls verbatim, and a line-oriented
parser that TOLERATES a malformed line will skip the HTTP request line and
headers, then happily dispatch the attacker's body line.

That was measured against this listener on 2026-08-16, before the fix: the
embedded ping executed and returned real Houdini data. Two defenses now apply,
and this check exercises BOTH -- independently, because defense in depth is
only real if each layer holds on its own:

  1. token       -- every request must carry the listener's per-run secret
  2. fail-closed -- the FIRST malformed line drops the connection, so the body
                    line is never reached even WITH a valid token

Exit code 0 = both attacks blocked.
"""

import json
import os
import socket
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _common import PORT, read_token  # noqa: E402

failures = []


def http_attack(label, body_obj):
    """Send exactly the bytes a browser fetch(..., {mode:'no-cors'}) would send."""
    body = json.dumps(body_obj) + "\n"
    request = (
        "POST / HTTP/1.1\r\n"
        "Host: 127.0.0.1:%d\r\n"
        "Connection: close\r\n"
        # text/plain is a CORS-"simple" content type: no preflight, body verbatim.
        "Content-Type: text/plain;charset=UTF-8\r\n"
        "Content-Length: %d\r\n"
        "\r\n"
        "%s" % (PORT, len(body), body)
    )

    conn = socket.create_connection(("127.0.0.1", PORT), timeout=15)
    try:
        conn.sendall(request.encode("utf-8"))
        conn.shutdown(socket.SHUT_WR)
        chunks = []
        while True:
            chunk = conn.recv(65536)
            if not chunk:
                break
            chunks.append(chunk)
    finally:
        conn.close()

    raw = b"".join(chunks).decode("utf-8", "replace")

    executed = False
    for line in raw.splitlines():
        if not line.strip():
            continue
        try:
            response = json.loads(line)
        except ValueError:
            continue
        result = response.get("result")
        if response.get("ok") and isinstance(result, dict) and result.get("echo") == "cross-protocol":
            executed = True
            break

    print("--- %s ---" % label)
    print("listener said: " + (raw.strip()[:400] or "(nothing)"))
    if executed:
        print("RESULT: VULNERABLE -- the HTTP body line reached the dispatcher and ran.\n")
        failures.append(label)
    else:
        print("RESULT: BLOCKED\n")


# 1. The plain attack: no token. Blocked by auth (and by fail-closed).
http_attack(
    "attack 1: HTTP body carrying a command, NO token",
    {"id": "evil", "cmd": "ping", "args": {"message": "cross-protocol"}},
)

# 2. The stronger attack: assume the token leaked. Auth no longer helps, so this
#    isolates the fail-closed parser. If this one passes, layer 2 holds alone.
http_attack(
    "attack 2: HTTP body carrying a command WITH a valid token",
    {"id": "evil", "cmd": "ping", "args": {"message": "cross-protocol"}, "token": read_token()},
)

if failures:
    print("FAILED: " + ", ".join(failures))
    sys.exit(1)
print("Both cross-protocol attacks blocked.")

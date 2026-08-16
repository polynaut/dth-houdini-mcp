"""Phase 0 check A: the socket transport, without any MCP involvement."""

import json
import socket
import sys

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8911


def call(cmd, args=None):
    conn = socket.create_connection(("127.0.0.1", PORT), timeout=15)
    try:
        conn.sendall((json.dumps({"id": "t1", "cmd": cmd, "args": args or {}}) + "\n").encode())
        buffer = b""
        while b"\n" not in buffer:
            chunk = conn.recv(65536)
            if not chunk:
                raise RuntimeError("closed early")
            buffer += chunk
    finally:
        conn.close()
    return json.loads(buffer.split(b"\n", 1)[0].decode())


print("--- ping ---")
print(json.dumps(call("ping", {"message": "hello from the raw socket"}), indent=2))

print("--- unknown command (must fail cleanly) ---")
bad = call("definitely_not_a_command")
print("ok:", bad["ok"], "|", bad["error"]["type"], "-", bad["error"]["message"])

print("--- bad arg type (must fail cleanly) ---")
bad2 = call("ping", {"message": 42})
print("ok:", bad2["ok"], "|", bad2["error"]["type"], "-", bad2["error"]["message"])

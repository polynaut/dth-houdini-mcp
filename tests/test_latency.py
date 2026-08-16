"""Phase 0 check C: how long the main-thread hop actually costs, over 30 calls."""

import json
import socket
import statistics
import sys

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8911


def call(cmd, args=None):
    conn = socket.create_connection(("127.0.0.1", PORT), timeout=15)
    try:
        conn.sendall((json.dumps({"id": "l", "cmd": cmd, "args": args or {}}) + "\n").encode())
        buffer = b""
        while b"\n" not in buffer:
            buffer += conn.recv(65536)
    finally:
        conn.close()
    return json.loads(buffer.split(b"\n", 1)[0].decode())["result"]


waits = []
for _ in range(30):
    waits.append(call("ping", {"message": "x"})["main_thread_wait_ms"])

print("mechanism  ", call("ping")["dispatch_mechanism"])
print("samples    ", len(waits))
print("min ms     ", round(min(waits), 2))
print("median ms  ", round(statistics.median(waits), 2))
print("mean ms    ", round(statistics.mean(waits), 2))
print("max ms     ", round(max(waits), 2))

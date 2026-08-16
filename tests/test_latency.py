"""Phase 0 check C: how long the main-thread hop actually costs, over 30 calls."""

import os
import statistics
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _common import PORT, call  # noqa: E402


def ping(message="x"):
    response = call("ping", {"message": message}, port=PORT)
    if not response.get("ok"):
        raise SystemExit("ping failed: %s" % response.get("error"))
    return response["result"]


waits = [ping()["main_thread_wait_ms"] for _ in range(30)]

print("mechanism  ", ping()["dispatch_mechanism"])
print("samples    ", len(waits))
print("min ms     ", round(min(waits), 2))
print("median ms  ", round(statistics.median(waits), 2))
print("mean ms    ", round(statistics.mean(waits), 2))
print("max ms     ", round(max(waits), 2))

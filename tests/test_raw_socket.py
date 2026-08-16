"""Phase 0 check A: the socket transport and its auth, without any MCP involvement.

Exit code 0 = all checks passed.
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _common import PORT, call, read_token  # noqa: E402

failures = []


def expect(label, condition, detail=""):
    print(("PASS  " if condition else "FAIL  ") + label + (" -- " + detail if detail else ""))
    if not condition:
        failures.append(label)


print("--- authenticated ping ---")
ok = call("ping", {"message": "hello from the raw socket"}, port=PORT)
print(json.dumps(ok, indent=2))
expect("authenticated ping succeeds", ok.get("ok") is True)
expect("payload echoes back", (ok.get("result") or {}).get("echo") == "hello from the raw socket")

print()
print("--- auth checks ---")
missing = call("ping", include_token=False, port=PORT)
expect(
    "request with NO token is rejected",
    missing.get("ok") is False and missing["error"]["type"] == "Unauthorized",
    missing.get("error", {}).get("message", ""),
)

wrong = call("ping", token="not-the-real-token", port=PORT)
expect(
    "request with a WRONG token is rejected",
    wrong.get("ok") is False and wrong["error"]["type"] == "Unauthorized",
    wrong.get("error", {}).get("message", ""),
)

expect("token file is readable", bool(read_token()))

print()
print("--- input validation ---")
unknown = call("definitely_not_a_command", port=PORT)
expect(
    "unknown command rejected cleanly",
    unknown.get("ok") is False and unknown["error"]["type"] == "KeyError",
    unknown.get("error", {}).get("message", ""),
)

bad_type = call("ping", {"message": 42}, port=PORT)
expect(
    "wrong arg type rejected cleanly",
    bad_type.get("ok") is False and bad_type["error"]["type"] == "ValueError",
    bad_type.get("error", {}).get("message", ""),
)

print()
if failures:
    print("FAILED: " + ", ".join(failures))
    sys.exit(1)
print("All checks passed.")

# dth-houdini-mcp

An MCP server exposing a **live Houdini session** for inspecting and debugging the
DazToHue (DTH) pipeline.

**Currently at Phase 0 — a transport spike only.** One read-only tool
(`houdini_ping`). See [PHASE0.md](PHASE0.md) for what has been verified, how, and
what has not.

## Layout

```
houdini_side/dth_listener.py   runs INSIDE Houdini (started manually)
houdini_mcp/server.py          stdio MCP server (launched by the MCP client)
tests/                         the Phase 0 verification checks
logs/                          audit log, one JSON line per executed command
```

## Setup

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

## Run

**1. Start the listener inside Houdini** (Python Shell, or a shelf tool):

```python
exec(open(r"D:\Development\dth-houdini-mcp\houdini_side\dth_listener.py").read())
start()          # binds 127.0.0.1:8911
status()         # inspect
stop()           # shut down
```

**2. Register the MCP server with your client:**

```json
{
  "mcpServers": {
    "dth-houdini": {
      "command": "D:\\Development\\dth-houdini-mcp\\.venv\\Scripts\\python.exe",
      "args": ["D:\\Development\\dth-houdini-mcp\\houdini_mcp\\server.py"]
    }
  }
}
```

Port is configurable on both sides via `DTH_HOUDINI_MCP_PORT` (default `8911`).

## Safety

Phase 0 is read-only: a fixed one-entry command table, loopback-only binding, and
nothing that saves or writes the `.hip`. Every command executed is appended to
`logs/listener-<pid>.jsonl` with its arguments, outcome and duration.

**Requests are authenticated.** `start()` writes a per-run token to
`%LOCALAPPDATA%\dth-houdini-mcp\token`; every request must carry it, and it is
checked before the command table is consulted. The token is regenerated each run
and deleted on `stop()`.

Loopback binding is *not* a security boundary — a line-oriented parser is
reachable from HTTP-shaped traffic, which was measured against an earlier build
of this listener and is now blocked two ways (token, plus dropping the
connection on the first malformed line). Both are covered by
`tests/test_crossprotocol.py`. The full account is in
[PHASE0.md §7](PHASE0.md).

## License

[MIT](LICENSE) © Polynaut

Not affiliated with SideFX or with the DazToHue toolset.

# Phase 0 — transport spike

**Status: complete and measured. No Phase 1 code written.**

The goal was narrow: prove one trivial round trip from an MCP client, through a
stdio MCP server, over a loopback socket, into a live Houdini session, executing
on Houdini's main thread, and back. That now works and is measured below.

Everything in this document was either introspected on this machine or read out
of the shipped source of the installed build. **No SideFX web documentation was
fetched** — every API claim below cites either a command I ran or a file/line in
`C:\Program Files\Side Effects Software\Houdini 22.0.368\houdini\python3.13libs\`.
Where something is unverified it is listed under *Not verified*, not asserted.

---

## 1. Environment (measured)

| Thing | Value | How |
| --- | --- | --- |
| Houdini (GUI) | `22.0.368`, product `hindie` | `hou.applicationVersionString()` / `hou.applicationName()` via the listener |
| Houdini (headless) | `22.0.368`, product `hython` | same, under `hython.exe` |
| Houdini Python | `3.13.10` | `sys.version` under hython |
| Other install present | Houdini `20.5.864` | `Get-ChildItem 'C:\Program Files\Side Effects Software'` — **not touched** |
| Host Python (MCP server) | `3.12.10` | `python --version` |
| MCP SDK | `mcp` **2.0.0** | `importlib.metadata.version('mcp')` |
| `uv` / `uvx` | not installed | `Get-Command` |

### MCP SDK note — this matters

`mcp` 2.0.0 has **no `mcp.server.fastmcp` module**. Importing it raises
`ModuleNotFoundError`. The current entry point is `mcp.server.MCPServer`:

```
INIT (self, name=None, title=None, description=None, instructions=None, ... version='', ...)
TOOL (self, name=None, title=None, description=None, annotations=None, ...)
RUN  (self, transport: Literal['stdio','sse','streamable-http'] = 'stdio', **kwargs)
```

*(source: `inspect.signature` on the installed package)*

The client-side result models are **snake_case** (`server_info`, `is_error`,
`structured_content`), not the camelCase names used in older examples. I hit
this twice writing the test harness.

---

## 2. The main-thread question — answered

### Mechanism

`hou.ui.addEventLoopCallback(callback)` — verified to exist **in this build** by
reading the shipped `hou.py`:

- `class ui(object):` — `hou.py` line **102053**
- `def addEventLoopCallback(self, callback)` — line **105344**
- `def removeEventLoopCallback(self, callback)` — line **105373**
- `def postEventCallback(self, callback)` — line **105389**
- `def removePostedEventCallback(self, callback)` — line **105407**
- `def eventLoopCallbacks(self)` — line **105420**

A `Select-String` for `^class ` over lines 102053–105500 returns **only**
`class ui`, so all five are `hou.ui` members and not some neighbouring class.

Its own docstring, verbatim from that file:

> Register a Python callback to be called whenever Houdini's event loop is idle.
> This callback is called approximately every 50ms, unless Houdini is busy
> processing events.

`postEventCallback` is documented as "called next in Houdini's event loop. This
will be called only once."

### Why one persistent callback, not one post per request

The listener registers **a single** `addEventLoopCallback(_pump)` for its whole
lifetime; `_pump` drains a `queue.Queue` of jobs. The alternative —
`postEventCallback` per request — means a register/unregister cycle on every
call. Both ride the same event loop, so the latency floor is the same. The
queue-drain has one registration to reason about and one to clean up.

### `hou.ui` does not exist in hython

```
isUIAvailable False
hou.ui MISSING
```

*(source: `hython probe_01_symbols.py`)*

So the deferral mechanism **cannot be tested from hython at all** — which is why
this spike launched a real GUI session. The listener detects this and falls back
to running inline on the socket thread, labelling the result
`inline-no-event-loop` so a headless result can never be mistaken for a
main-thread one.

### The measurement (GUI Houdini, `hindie`)

```json
{
  "houdini_product": "hindie",
  "ui_available": true,
  "dispatch_mechanism": "hou.ui.addEventLoopCallback",
  "executed_on_main_thread": true,
  "executed_thread_ident": 59140,
  "listener_thread_ident": 60864,
  "main_thread_wait_ms": 0.89
}
```

The two thread idents differ (**60864** received the socket request, **59140**
executed the `hou` calls) and `threading.current_thread() is
threading.main_thread()` evaluated **True** inside the job. That is the proof
that work genuinely crossed onto the main thread rather than merely appearing to.

### Cost of the hop — 30 consecutive calls, idle GUI

```
mechanism   hou.ui.addEventLoopCallback
samples     30
min ms      25.89
median ms   35.27
mean ms     35.22
max ms      51.07
```

This tracks the documented ~50 ms idle tick: a request arriving at a random
point in the cycle waits, on average, most of a tick. Individual observed
samples ranged from **0.89 ms** (arrived just before a tick) to **47.95 ms**.

**Phase 1 design consequence:** every main-thread hop costs ~25–50 ms, so a tool
must do *one* hop and gather everything inside it. A `scene_summary` that
defers per node would cost 50 ms × node count. Batch inside the job; never loop
across jobs.

---

## 3. `hwebserver` vs a raw socket — findings

I checked, as asked. **`hwebserver` does not solve the thread problem, so it
does not displace the need for the pump above.**

Measured facts:

1. It exists and imports: `hwebserver.py` (144 KB) in `python3.13libs`, with
   `run` and `apiFunction` present; the public stop is `requestShutdown()`
   (line 3455). *(source: `hython probe_02_hwebserver.py` + grep)*
2. `hwebserver.py` line **1673**: `run_in_thread = kwargs.get("in_background", isUIAvailable())`
   — **in a GUI session the server runs in a background thread by default.**
3. Its own `apiFunction` docstring, lines **629–632**, verbatim:

   > NOTE
   >     There is no guarantee which thread the server will call the API
   >     function from, so you cannot rely on thread-specific results such as
   >     hou.frame.

That third point is decisive: an `hwebserver` API function is *explicitly not*
guaranteed to be on the main thread, so it would still have to hand work to
`hou.ui.addEventLoopCallback`. Adopting it would add Houdini's asyncio stack
(`haio.HoudiniEventLoopPolicy`, a singleton `HoudiniEventLoop` shared across
threads — `haio.py` line 3069) and HTTP framing, while leaving the actual
hazard exactly where it was.

**Recommendation: stay on the raw loopback socket.** It is ~80 lines, has no
dependency on Houdini's web stack, and the thread discipline is explicit and
visible in one function. Reconsider only if we later want multiple simultaneous
clients or browser access — neither is a DTH debugging requirement.

*Caveat: I did not run an `hwebserver` server and time it. The recommendation
rests on the source and its docstring, not on a bake-off.*

---

## 4. What Phase 0 actually ships

Two processes, as specified. The MCP server never launches Houdini.

```
MCP client ──stdio──▶ houdini_mcp/server.py ──TCP 127.0.0.1:8911──▶ dth_listener.py
                       (host py 3.12, mcp 2.0.0)                     (inside Houdini)
                                                                          │
                                                        hou.ui.addEventLoopCallback
                                                                          ▼
                                                              Houdini main thread
```

Wire protocol — newline-delimited JSON:

```
→ {"id": "...", "cmd": "ping", "args": {"message": "..."}}
← {"id": "...", "ok": true,  "result": {...}}
← {"id": "...", "ok": false, "error": {"type": "...", "message": "...", "traceback": "..."}}
```

### Files

| Path | Role |
| --- | --- |
| `houdini_side/dth_listener.py` | Runs inside Houdini. Socket server + main-thread pump + audit log. One command: `ping`. |
| `houdini_mcp/server.py` | stdio MCP server. One tool: `houdini_ping`. |
| `tests/_common.py` | Shared test client (reads the token, one call per connection). |
| `tests/test_raw_socket.py` | Check A — socket transport + auth, no MCP. |
| `tests/test_mcp_roundtrip.py` | Check B — real MCP client → stdio server → Houdini. |
| `tests/test_latency.py` | Check C — main-thread hop latency, n=30. |
| `tests/test_crossprotocol.py` | Check D — cross-protocol injection; regression test for §7. |
| `logs/listener-<pid>.jsonl` | Audit log. |

### Containment as built

- **Nothing saves.** A grep for `save|autosave|backup|hipFile\.(save|clear|load|merge)`
  over the listener returns only the docstring and the read-only
  `hou.hipFile.hasUnsavedChanges()`.
- **Fixed command table.** `COMMANDS = {"ping": ...}`. An unknown command is a
  clean error naming what is available; there is no dynamic dispatch to
  arbitrary attributes.
- **Token required on every request**, checked before the command table — see §7.
- **Fail closed.** A malformed or non-object request line drops the connection
  rather than being skipped — see §7.
- **Loopback enforced in code.** `start()` raises on any non-loopback host.
- **No address reuse.** `allow_reuse_address = False` — on Windows `SO_REUSEADDR`
  lets a second bind steal a live port; a loud bind failure is preferable.
- **Bounded wait.** A request gives up after 10 s rather than hanging the client
  if the main thread never pumps.
- **Every payload is logged.** `logs/listener-<pid>.jsonl`, one JSON line per
  command: timestamp, peer, cmd, args, ok, error, duration. The GUI run produced
  36 entries; the headless run 6.

---

## 5. Verification log — what was run

| # | Check | Result |
| --- | --- | --- |
| 1 | `hython` version probe | 22.0.368 / py 3.13.10, `hou.ui` absent |
| 2 | Read-only symbol probe (`hipFile.name/path/hasUnsavedChanges`, `node`, `nodeType`, `getenv`) | all present |
| 3 | Listener under hython + raw socket | round trip OK, correctly labelled `inline-no-event-loop` |
| 4 | Unknown command | clean `KeyError`, listener survives |
| 5 | Wrong arg type (`message: 42`) | clean `ValueError`, listener survives |
| 6 | Full MCP round trip, headless | OK, 24.01 ms client round trip |
| 7 | **GUI Houdini + raw socket** | **`executed_on_main_thread: true`, distinct thread idents** |
| 8 | **GUI Houdini + full MCP round trip** | OK, 68.55 ms client round trip |
| 9 | Latency, n=30, GUI idle | median 35.27 ms, max 51.07 ms |
| 10 | Audit log | 36 entries GUI / 6 headless, args + outcome + duration |
| 11 | `hwebserver` import + API | `run`, `apiFunction`, `requestShutdown` present |
| 12 | **Cross-protocol injection, before fix** | **VULNERABLE — HTTP body line dispatched and ran** |
| 13 | Cross-protocol injection, after fix | blocked, both with and without a valid token |
| 14 | Request with no token / wrong token | rejected `Unauthorized`, connection closed |
| 15 | Token in audit log | absent (searched the log for the live token) |
| 16 | Rejected attempts audited | 4 entries tagged `unauthorized` / `closed-on-malformed-line` |
| 17 | Browser-origin `fetch` from Chrome | **inconclusive** — blocked, but the harness ran at `origin: null`; see §7 |

The GUI session was launched with a temporary `456.py` on `HOUDINI_SCRIPT_PATH`
purely as a test rig, so the verification could run unattended. **The shipped
path is a manual start** from the Python Shell or a shelf tool, as specified.
That Houdini instance was an empty untitled scene and was closed afterwards.

### Reproduce

```powershell
# 1. In Houdini's Python Shell:
#    exec(open(r"D:\Development\dth-houdini-mcp\houdini_side\dth_listener.py").read())
#    start()

# 2. Then:
D:\Development\dth-houdini-mcp\.venv\Scripts\python.exe D:\Development\dth-houdini-mcp\tests\test_raw_socket.py 8911
D:\Development\dth-houdini-mcp\.venv\Scripts\python.exe D:\Development\dth-houdini-mcp\tests\test_mcp_roundtrip.py
D:\Development\dth-houdini-mcp\.venv\Scripts\python.exe D:\Development\dth-houdini-mcp\tests\test_latency.py 8911
```

---

## 6. Not verified — do not assume these

1. **Behaviour while Houdini is busy.** The pump only runs when the event loop
   is idle. I did *not* test a long cook or an open modal dialog. The expected
   result is the 10 s timeout, but that is **inference, not measurement**.
2. **Surviving a scene load.** The listener was never exercised across a
   `hipFile.load`. `start()` is idempotent, but whether the event-loop callback
   survives a scene change is untested.
3. **`postEventCallback` latency.** Never measured; the ~35 ms median above is
   `addEventLoopCallback` only. If Phase 1 wants lower latency this is the first
   thing to measure — though batching makes it mostly moot.
4. **Houdini 20.5.864.** Untouched. Every measurement here is 22.0.368.
5. **macOS / Linux.** Windows only. The loopback socket should be portable; the
   paths are not.
6. **Concurrency.** `ThreadingTCPServer` accepts parallel connections and the
   queue serialises them onto the main thread, but I never drove two clients at
   once.
7. **`hwebserver` performance.** Source-read only, see §3.
8. **Whether a real web page can reach the listener.** Two Chrome attempts were
   blocked, but the harness ran at `origin: null`, so this is unproven in both
   directions. See §7 — it does not affect the conclusion.
9. **Main-thread latency after the security fix.** The §2 numbers were measured
   before it. The fix touches the request parser, not the pump, so they should
   still hold — but that is inference; the post-fix latency run was headless
   (`inline-no-event-loop`, median 0.04 ms) and says nothing about the GUI hop.

---

## 7. Security — a measured vulnerability, and the fix

This started as a design note about missing authentication. It was then
**measured, and it was worse than the note implied.**

### What was measured (2026-08-16, before the fix)

The listener speaks newline-delimited JSON, not HTTP. That was assumed to put it
out of a browser's reach. It did not, for a reason that has nothing to do with
HTTP: the handler read the socket **line by line and tolerated a malformed
line**. So an HTTP request could carry a command in its body —

```
POST / HTTP/1.1                 <- fails json.loads, error returned, loop continues
Host: 127.0.0.1:8911            <- fails, continues
Content-Type: text/plain        <- fails, continues
                                <- blank, skipped
{"cmd": "ping", "args": {...}}  <- VALID JSON. Dispatched. Executed.
```

Run against the live listener, this returned a real result including
`houdini_version: 22.0.368`. Structurally, *any* protocol that lets an attacker
place one attacker-controlled line on the socket could drive the dispatcher.
With Phase 2's `execute_python`, that is arbitrary code execution as the user.

The reproduction is committed as `tests/test_crossprotocol.py`.

### Two fixes, each of which holds alone

1. **Token.** `start()` generates `secrets.token_urlsafe(32)`, writes it to
   `%LOCALAPPDATA%\dth-houdini-mcp\token`, and every request must carry it.
   Checked with `secrets.compare_digest` **before the command table is
   consulted**. Fresh per run, deleted on `stop()`, and never written to the
   audit log (verified by searching the log for the live token). A browser
   cannot read a local file, so the blind attack dies.
2. **Fail closed.** The first malformed line now **drops the connection**
   instead of being skipped. This is the layer that actually kills the class of
   attack: it does not depend on the token staying secret.

`tests/test_crossprotocol.py` exercises both **independently** — attack 2 sends
the HTTP-shaped payload carrying a *valid* token, so it isolates the parser
defense. Both are blocked, and all four rejected attempts appear in the audit
log tagged `unauthorized` / `closed-on-malformed-line`.

### The browser vector specifically — inconclusive, and it does not matter

I tried to reproduce this from a real Chrome page (`https://example.com` and
`http://neverssl.com`) via `fetch(..., {mode: 'no-cors'})`. Both attempts
failed with `TypeError: Failed to fetch` and **nothing reached the listener**.
That is *not* a clean result: the injected script reported `origin: "null"`,
so it ran in an isolated world rather than as the page, which is not a faithful
reproduction of an attacker page. Chrome's Private Network Access restrictions
are a plausible cause but were not confirmed.

So: **whether a web page can reach it is unproven in both directions.** It also
does not change the conclusion, because the browser is only one delivery
mechanism. The local-process vector needs no browser at all, is fully proven,
and is the realistic threat on a dev machine — a malicious postinstall script, a
compromised CLI tool, anything running as the user.

### Still true

The token is a secret on disk readable by any process running as this user. It
raises the bar from "any local process" to "any local process that can read the
user's files" — worthwhile, but it is not a sandbox. Phase 2's `execute_python`
should additionally be off unless explicitly enabled, as originally specified.

`stop()` deletes the token file, but a hard kill (Houdini crash, `Stop-Process`)
leaves it behind — observed. It authenticates nothing once the listener is gone,
and the next `start()` overwrites it, so this is untidy rather than unsafe.

---

## 8. Proposed Phase 1 shape (for review — not built)

Given the ~35 ms hop, the tool surface you specified maps to **one main-thread
job per tool call**, each returning structured JSON:

- `scene_summary` — walk once, count by context, collect cook times in the same job
- `node_info(path)` — parms + values + expressions + errors, one traversal
- `node_type_info(name)` — parm template layout
- `find_nodes(pattern, type_filter)` — glob/regex over a single recursive walk
- `geometry_summary(sop_path)` — counts + attribute *metadata* only, never point dumps
- `kinefx_skeleton(path)` — joint names, hierarchy, transforms

None of the `hou` symbols these need have been verified yet beyond
`hou.node`/`hou.nodeType`. Phase 1 starts by introspecting each one against this
build, the same way this document did — in particular the KineFX and geometry
attribute APIs, which I will not guess at.

**Awaiting your review before proceeding.**

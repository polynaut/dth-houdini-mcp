"""DTH Houdini MCP listener -- PHASE 0 SPIKE.

Runs INSIDE a live Houdini session. Start it from the Python Shell or a shelf
tool; it is never launched by the MCP server.

Phase 0 scope: prove the transport. The command table below contains exactly
one command ("ping") and it is read-only. There is no mutation, no eval, no
`hipFile.save`, no autosave toggle -- nothing in this file writes to the scene
or to disk except the audit log.

Protocol: newline-delimited JSON over a loopback TCP socket.
    request   {"id": "<str>", "cmd": "<str>", "args": {...}}
    response  {"id": "<str>", "ok": true,  "result": {...}}
              {"id": "<str>", "ok": false, "error": {"type": "...", "message": "..."}}

Usage inside Houdini's Python Shell:

    exec(open(r"D:\\Development\\dth-houdini-mcp\\houdini_side\\dth_listener.py").read())
    start()

    stop()   # when done
"""

import json
import os
import queue
import socket
import socketserver
import sys
import threading
import time
import traceback

import hou

PROTOCOL_VERSION = 0
DEFAULT_PORT = 8911
DEFAULT_HOST = "127.0.0.1"

# How long a request may wait for Houdini's main thread before we give up. The
# event loop only pumps when Houdini is idle, so a modal dialog or a long cook
# legitimately blocks us; failing loudly beats hanging the MCP client forever.
MAIN_THREAD_TIMEOUT = 10.0

_server = None
_server_thread = None
_pump_installed = False
_jobs = queue.Queue()
_log_lock = threading.Lock()


# --------------------------------------------------------------------------
# Audit log
# --------------------------------------------------------------------------

def _log_dir():
    override = os.environ.get("DTH_HOUDINI_MCP_LOG_DIR")
    if override:
        return override
    try:
        # Normal case: loaded from a file inside the project.
        return os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "logs")
    except NameError:
        # Pasted straight into the Python Shell -- no __file__.
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
        return os.path.join(base, "dth-houdini-mcp", "logs")


def _log_path():
    return os.path.join(_log_dir(), "listener-%d.jsonl" % os.getpid())


def _audit(entry):
    """Append one JSON line. Every payload the listener executes lands here."""
    entry = dict(entry)
    entry["ts"] = time.time()
    entry["ts_iso"] = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(entry["ts"]))
    entry["pid"] = os.getpid()
    try:
        directory = _log_dir()
        if not os.path.isdir(directory):
            os.makedirs(directory)
        line = json.dumps(entry, default=repr)
        with _log_lock:
            with open(_log_path(), "a", encoding="utf-8") as handle:
                handle.write(line + "\n")
    except Exception:
        # An audit-log failure must never take the listener down, but it must
        # be visible in the Houdini console rather than swallowed.
        sys.stderr.write("[dth-listener] audit log write failed:\n%s\n" % traceback.format_exc())


# --------------------------------------------------------------------------
# Main-thread dispatch
#
# `hou` is not thread safe. The socket handler runs on a worker thread, so
# every `hou.*` touch is queued and executed by a callback registered on
# Houdini's event loop -- which runs on the main thread.
#
# Mechanism (verified against this build's houdini/python3.13libs/hou.py):
#   hou.ui.addEventLoopCallback(callback)  -- class ui, line 105344
#     "Register a Python callback to be called whenever Houdini's event loop
#      is idle. This callback is called approximately every 50ms, unless
#      Houdini is busy processing events."
# One persistent registration drains the queue, rather than one
# postEventCallback per request, so there is no register/unregister churn.
# --------------------------------------------------------------------------

class _Job(object):
    __slots__ = ("fn", "event", "value", "error", "traceback", "queued_at", "started_at")

    def __init__(self, fn):
        self.fn = fn
        self.event = threading.Event()
        self.value = None
        self.error = None
        self.traceback = None
        self.queued_at = time.time()
        self.started_at = None

    def run(self):
        self.started_at = time.time()
        try:
            self.value = self.fn()
        except Exception as exc:
            self.error = exc
            self.traceback = traceback.format_exc()
        finally:
            self.event.set()


def _pump():
    """Drain queued jobs. Runs on Houdini's main thread."""
    while True:
        try:
            job = _jobs.get_nowait()
        except queue.Empty:
            return
        job.run()


def _ui_available():
    # hou.ui only exists when a UI is attached; hython has no such module.
    return hasattr(hou, "ui") and hou.isUIAvailable()


def _install_pump():
    global _pump_installed
    if _pump_installed:
        return "already-installed"
    if not _ui_available():
        return "none-headless"
    hou.ui.addEventLoopCallback(_pump)
    _pump_installed = True
    return "hou.ui.addEventLoopCallback"


def _remove_pump():
    global _pump_installed
    if not _pump_installed:
        return
    try:
        hou.ui.removeEventLoopCallback(_pump)
    except Exception:
        sys.stderr.write("[dth-listener] removeEventLoopCallback failed:\n%s\n" % traceback.format_exc())
    _pump_installed = False


def run_on_main_thread(fn, timeout=MAIN_THREAD_TIMEOUT):
    """Execute `fn` on Houdini's main thread and return (value, how, wait_ms).

    `how` names the mechanism that actually ran it, so a caller can never
    mistake the headless inline path for real main-thread deferral.
    """
    if not _pump_installed:
        # hython / no UI: there is no event loop to defer onto. Run inline and
        # label it honestly. In hython the calling thread is the only thread
        # touching hou, so this is safe *there* and nowhere else.
        started = time.time()
        return fn(), "inline-no-event-loop", (time.time() - started) * 1000.0

    job = _Job(fn)
    _jobs.put(job)
    if not job.event.wait(timeout):
        raise RuntimeError(
            "timed out after %.1fs waiting for Houdini's main thread; it is busy "
            "(cooking, or a modal dialog is open)" % timeout
        )
    if job.error is not None:
        raise job.error
    wait_ms = (job.started_at - job.queued_at) * 1000.0
    return job.value, "hou.ui.addEventLoopCallback", wait_ms


# --------------------------------------------------------------------------
# Commands -- Phase 0: read-only, exactly one
# --------------------------------------------------------------------------

def _cmd_ping(args):
    message = args.get("message", "")
    if not isinstance(message, str):
        raise ValueError("message must be a string, got %s" % type(message).__name__)

    def collect():
        # Everything in here reads. Nothing writes.
        return {
            "houdini_version": hou.applicationVersionString(),
            "houdini_product": hou.applicationName(),
            "python_version": sys.version.split()[0],
            "hip_name": hou.hipFile.name(),
            "hip_path": hou.hipFile.path(),
            "hip_has_unsaved_changes": hou.hipFile.hasUnsavedChanges(),
            "ui_available": hou.isUIAvailable(),
            "executed_on_main_thread": threading.current_thread() is threading.main_thread(),
            "executed_thread_ident": threading.get_ident(),
        }

    value, how, wait_ms = run_on_main_thread(collect)
    value["echo"] = message
    value["protocol_version"] = PROTOCOL_VERSION
    value["dispatch_mechanism"] = how
    value["main_thread_wait_ms"] = round(wait_ms, 2)
    value["listener_thread_ident"] = threading.get_ident()
    value["listener_pid"] = os.getpid()
    value["audit_log"] = _log_path()
    return value


COMMANDS = {
    "ping": _cmd_ping,
}


# --------------------------------------------------------------------------
# Socket transport
# --------------------------------------------------------------------------

class _Handler(socketserver.StreamRequestHandler):
    def handle(self):
        peer = "%s:%s" % self.client_address[:2]
        for raw in self.rfile:
            raw = raw.strip()
            if not raw:
                continue
            started = time.time()
            request_id = None
            cmd = None
            args = {}
            try:
                request = json.loads(raw.decode("utf-8"))
                request_id = request.get("id")
                cmd = request.get("cmd")
                args = request.get("args") or {}
                handler = COMMANDS.get(cmd)
                if handler is None:
                    raise KeyError(
                        "unknown command %r; Phase 0 exposes only: %s"
                        % (cmd, ", ".join(sorted(COMMANDS)))
                    )
                result = handler(args)
                response = {"id": request_id, "ok": True, "result": result}
                self._audit_call(peer, cmd, args, True, None, started)
            except Exception as exc:
                response = {
                    "id": request_id,
                    "ok": False,
                    "error": {
                        "type": type(exc).__name__,
                        "message": str(exc),
                        "traceback": traceback.format_exc(),
                    },
                }
                self._audit_call(peer, cmd, args, False, exc, started)
            try:
                self.wfile.write((json.dumps(response, default=repr) + "\n").encode("utf-8"))
                self.wfile.flush()
            except Exception:
                return  # client hung up mid-response

    def _audit_call(self, peer, cmd, args, ok, exc, started):
        _audit({
            "event": "command",
            "peer": peer,
            "cmd": cmd,
            "args": args,
            "ok": ok,
            "error": None if exc is None else "%s: %s" % (type(exc).__name__, exc),
            "duration_ms": round((time.time() - started) * 1000.0, 2),
        })


class _Server(socketserver.ThreadingTCPServer):
    daemon_threads = True
    # Deliberately NOT reusing the address: on Windows SO_REUSEADDR lets a
    # second bind steal a live port. A loud failure beats a silent hijack.
    allow_reuse_address = False


# --------------------------------------------------------------------------
# Lifecycle
# --------------------------------------------------------------------------

def start(port=None, host=DEFAULT_HOST):
    """Start the listener. Idempotent -- a second call reports the running one."""
    global _server, _server_thread

    if _server is not None:
        print("[dth-listener] already running on %s:%s" % (host, _server.server_address[1]))
        return _server.server_address[1]

    if port is None:
        port = int(os.environ.get("DTH_HOUDINI_MCP_PORT", DEFAULT_PORT))

    if host not in ("127.0.0.1", "localhost", "::1"):
        raise ValueError("refusing to bind to non-loopback host %r" % host)

    mechanism = _install_pump()

    try:
        _server = _Server((host, port), _Handler)
    except OSError as exc:
        _remove_pump()
        raise RuntimeError(
            "could not bind %s:%s (%s). Another listener may already be running; "
            "call stop() first, or pick another port." % (host, port, exc)
        )

    _server_thread = threading.Thread(target=_server.serve_forever, name="dth-mcp-listener", daemon=True)
    _server_thread.start()

    _audit({
        "event": "start",
        "host": host,
        "port": port,
        "dispatch_mechanism": mechanism,
        "houdini_version": hou.applicationVersionString(),
        "ui_available": _ui_available(),
        "commands": sorted(COMMANDS),
    })

    print("[dth-listener] listening on %s:%s" % (host, port))
    print("[dth-listener] houdini      %s (ui_available=%s)" % (hou.applicationVersionString(), _ui_available()))
    print("[dth-listener] dispatch     %s" % mechanism)
    print("[dth-listener] commands     %s" % ", ".join(sorted(COMMANDS)))
    print("[dth-listener] audit log    %s" % _log_path())
    if mechanism == "none-headless":
        print("[dth-listener] WARNING: no UI event loop; hou calls run INLINE on the socket thread.")
    return port


def stop():
    """Stop the listener and unregister the event-loop pump."""
    global _server, _server_thread
    if _server is None:
        print("[dth-listener] not running")
        return
    port = _server.server_address[1]
    _server.shutdown()
    _server.server_close()
    _server = None
    _server_thread = None
    _remove_pump()
    _audit({"event": "stop", "port": port})
    print("[dth-listener] stopped (port %s)" % port)


def status():
    return {
        "running": _server is not None,
        "port": None if _server is None else _server.server_address[1],
        "pump_installed": _pump_installed,
        "ui_available": _ui_available(),
        "commands": sorted(COMMANDS),
        "audit_log": _log_path(),
    }


if __name__ == "__main__":
    # Lets hython run this file directly for a headless transport test.
    start()
    try:
        while True:
            time.sleep(0.2)
    except KeyboardInterrupt:
        stop()

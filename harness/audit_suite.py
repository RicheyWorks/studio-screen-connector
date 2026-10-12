# SPDX-License-Identifier: MIT
"""audit suite: audit.ps1 against a fake Unreal bridge (ping + execute_python) and fixture window lists."""
import json
import os
import socket
import socketserver
import subprocess
import threading

from . import ps
from .ledger import ROOT

FIX = os.path.join(ROOT, "tests", "fixtures")


class FakeBridge:
    """Answers ping with result.playing and execute_python with result.result = dirty package names."""

    def __init__(self, playing=False, dirty=(), ping_ok=True, py_ok=True):
        self.requests = []
        outer = self

        class H(socketserver.StreamRequestHandler):
            def handle(self):
                req = json.loads(self.rfile.readline().decode("utf-8"))
                outer.requests.append(req)
                t = req.get("type")
                if t == "ping" and ping_ok:
                    rep = {"id": req.get("id"), "status": "ok", "result": {"playing": playing, "level": "L"}}
                elif t == "execute_python" and py_ok:
                    rep = {"id": req.get("id"), "status": "ok", "result": {"stdout": "", "result": list(dirty)}}
                else:
                    rep = {"id": req.get("id"), "status": "error", "message": "nope"}
                self.wfile.write((json.dumps(rep) + "\n").encode("utf-8"))

        self.srv = socketserver.ThreadingTCPServer(("127.0.0.1", 0), H)
        self.srv.daemon_threads = True
        self.port = self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def close(self):
        self.srv.shutdown(); self.srv.server_close()


def free_port():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close()
    return p


def audit(script=None, windows="audit_clean.json", bridge=None, extra=()):
    script = script or os.path.join(ROOT, "audit.ps1")
    fb = FakeBridge(**(bridge or {})) if bridge is not False else None
    port = fb.port if fb else free_port()
    cmd = [ps.shell(), "-NoProfile", "-NonInteractive", "-File", script, "-BridgePort", str(port),
           "-ConnectMs", "800", "-ReadMs", "3000", "-FakeWindows", os.path.join(FIX, windows)] + list(extra)
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    finally:
        reqs = fb.requests if fb else []
        if fb:
            fb.close()
    lines = [l for l in p.stdout.splitlines() if l.strip()]
    try:
        out = json.loads(lines[-1])
    except (ValueError, IndexError):
        out = {"ok": None, "message": (p.stdout + p.stderr)[-300:]}
    checks = {c["check"]: c for c in out.get("checks") or []}
    return {"rc": p.returncode, "out": out, "checks": checks, "requests": reqs}


def ok_of(r, name):
    return r["checks"].get(name, {}).get("ok")


def cases():
    return [
        ("clean turn: ok, exit 0, all four checks pass", dict(),
         lambda r: r["rc"] == 0 and r["out"].get("ok") is True and len(r["checks"]) == 4
                   and all(c["ok"] for c in r["checks"].values())),
        ("Play running: 'play stopped' fails, exit 1", dict(bridge={"playing": True}),
         lambda r: r["rc"] == 1 and ok_of(r, "play stopped") is False and ok_of(r, "saved") is True),
        ("dirty packages: 'saved' fails and names them", dict(bridge={"dirty": ["MyLevel", "M_Rock"]}),
         lambda r: r["rc"] == 1 and ok_of(r, "saved") is False and "MyLevel" in r["checks"]["saved"]["detail"]),
        ("floating panels and a dialog fail separately; main window is never a panel", dict(windows="audit_untidy.json"),
         lambda r: r["rc"] == 1 and ok_of(r, "no floating panels") is False and ok_of(r, "no open dialogs") is False
                   and "Content Browser" in r["checks"]["no floating panels"]["detail"]
                   and "Message Log" in r["checks"]["no floating panels"]["detail"]
                   and "Save Content" not in r["checks"]["no floating panels"]["detail"]
                   and "Save Content" in r["checks"]["no open dialogs"]["detail"]
                   and "MyLevel - Unreal Editor" not in json.dumps(r["checks"])),
        ("bridge down: unverified is not clean (play + saved fail), exit 1, no hang", dict(bridge=False),
         lambda r: r["rc"] == 1 and ok_of(r, "play stopped") is False and ok_of(r, "saved") is False),
        ("ping without result.playing fails, never assumed stopped", dict(bridge={"ping_ok": False}),
         lambda r: r["rc"] == 1 and ok_of(r, "play stopped") is False),
        ("dirty query refused: 'saved' fails with the bridge message", dict(bridge={"py_ok": False}),
         lambda r: ok_of(r, "saved") is False and "nope" in r["checks"]["saved"]["detail"]),
        ("read-only: only ping and execute_python are sent, never stop/save", dict(),
         lambda r: sorted(q["type"] for q in r["requests"]) == ["execute_python", "ping"]
                   and "save_" not in json.dumps(r["requests"]) and "get_dirty" in json.dumps(r["requests"])),
        ("-SkipSaved: no execute_python at all", dict(extra=["-SkipSaved"]),
         lambda r: r["rc"] == 0 and [q["type"] for q in r["requests"]] == ["ping"]),
    ]


def run(script=None):
    rows = []
    for name, kw, check in cases():
        r = audit(script=script, **kw)
        try:
            ok = bool(check(r))
        except Exception as e:   # a malformed output is a failure, not a crash
            ok = False
            r["out"]["error"] = repr(e)
        rows.append({"name": name, "pass": ok, "detail": json.dumps(r["out"])[:200]})
    return rows

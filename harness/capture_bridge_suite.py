# SPDX-License-Identifier: MIT
"""capture bridge suite: capture.ps1 -Target unreal falls back to the Unreal bridge when the editor is minimized/hidden.
A fake bridge answers execute_python and writes the PNG itself (extension-less, like UE's export_render_target)."""
import json
import os
import re
import shutil
import socketserver
import subprocess
import tempfile
import threading
import time

from . import ps
from .audit_suite import free_port
from .ledger import ROOT

FIX = os.path.join(ROOT, "tests", "fixtures")
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


class FakeBridge:
    def __init__(self, mode="ok", delay=0.0):
        self.requests = []
        outer = self

        class H(socketserver.StreamRequestHandler):
            def handle(self):
                req = json.loads(self.rfile.readline().decode("utf-8"))
                outer.requests.append(req)
                code = (req.get("params") or {}).get("code", "")
                d = re.search(r'OUT_DIR = r"([^"]+)"', code)
                s = re.search(r'STEM = "([^"]+)"', code)
                if req.get("type") != "execute_python" or not d or not s or mode == "refuse":
                    rep = {"id": req.get("id"), "status": "error", "message": "nope"}
                else:
                    target = os.path.join(d.group(1), s.group(1))
                    data = b"not a png at all" if mode == "notpng" else PNG

                    def write():
                        time.sleep(delay)
                        if mode not in ("nofile", "editor_error"):
                            with open(target, "wb") as f:
                                f.write(data)
                    if delay:
                        threading.Thread(target=write, daemon=True).start()
                    else:
                        write()
                    out = {"cam": [1.0, 2.0, 3.0], "error": "boom" if mode == "editor_error" else None, "newly_dirty": []}
                    rep = {"id": req.get("id"), "status": "ok", "result": {"stdout": json.dumps(out) + "\r\n", "result": None}}
                self.wfile.write((json.dumps(rep) + "\n").encode("utf-8"))

        self.srv = socketserver.ThreadingTCPServer(("127.0.0.1", 0), H)
        self.srv.daemon_threads = True
        self.port = self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def close(self):
        self.srv.shutdown(); self.srv.server_close()


def capture(bridge=None, windows="capture_unreal_min.json", target="unreal", extra=()):
    out_dir = tempfile.mkdtemp(prefix="ssc-cap-")
    fb = FakeBridge(**bridge) if bridge is not None else None
    port = fb.port if fb else free_port()
    cmd = [ps.shell(), "-NoProfile", "-NonInteractive", "-File", os.path.join(ROOT, "capture.ps1"), "-DryRun",
           "-Op", "capture", "-Target", target, "-Allowlist", ps.ALLOW, "-FakeWindows", os.path.join(FIX, windows), "-OutDir", out_dir,
           "-BridgePort", str(port), "-BridgeTimeoutMs", "4000"] + list(extra)
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
    files = sorted(os.listdir(out_dir))
    shutil.rmtree(out_dir, ignore_errors=True)
    return {"rc": p.returncode, "out": out, "files": files, "requests": reqs}


def pngs(r):
    return [f for f in r["files"] if f.endswith(".png")]


def cases():
    return [
        ("minimized editor -> bridge PNG in OutDir, method=bridge", dict(bridge={}),
         lambda r: r["rc"] == 0 and r["out"].get("method") == "bridge" and r["out"].get("reason") == "window minimized"
                   and len(pngs(r)) == 1 and r["out"]["path"].endswith(pngs(r)[0])),
        ("async export: file appears after the reply, poll finds it", dict(bridge={"delay": 1.5}),
         lambda r: r["rc"] == 0 and len(pngs(r)) == 1),
        ("no editor window at all (hidden) -> bridge", dict(bridge={}, windows="capture_no_unreal.json"),
         lambda r: r["rc"] == 0 and r["out"].get("method") == "bridge"),
        ("visible editor still uses PrintWindow, bridge never called", dict(bridge={}, windows="clean.json"),
         lambda r: r["rc"] == 0 and r["out"].get("hwnd") == 201 and r["out"].get("method") is None and not r["requests"]),
        ("-Method window keeps the old minimized error, bridge never called", dict(bridge={}, extra=["-Method", "window"]),
         lambda r: r["rc"] == 1 and "minimized" in r["out"].get("message", "") and not r["requests"]),
        ("bridge down -> clean error, no file, no hang", dict(bridge=None),
         lambda r: r["rc"] == 1 and "did not answer" in r["out"].get("message", "") and not r["files"]),
        ("bridge refuses -> error with its message", dict(bridge={"mode": "refuse"}),
         lambda r: r["rc"] == 1 and "nope" in r["out"].get("message", "")),
        ("editor-side exception -> error, not ok", dict(bridge={"mode": "editor_error"}),
         lambda r: r["rc"] == 1 and "boom" in r["out"].get("message", "")),
        ("no file written -> times out with an error", dict(bridge={"mode": "nofile"}),
         lambda r: r["rc"] == 1 and "wrote no file" in r["out"].get("message", "")),
        ("non-PNG output rejected and removed", dict(bridge={"mode": "notpng"}),
         lambda r: r["rc"] == 1 and "not a PNG" in r["out"].get("message", "") and not r["files"]),
        ("request is one execute_python: transient actor, destroyed, never saves/restores a window",
         dict(bridge={}),
         lambda r: [q["type"] for q in r["requests"]] == ["execute_python"]
                   and "transient=True" in r["requests"][0]["params"]["code"]
                   and "destroy_actor" in r["requests"][0]["params"]["code"]
                   and not re.search(r"save_|ShowWindow|restore", r["requests"][0]["params"]["code"])),
        ("non-unreal minimized target never uses the bridge", dict(bridge={}, windows="windows.json", target="blender"),
         lambda r: r["rc"] == 1 and not r["requests"]),
    ]


def run():
    rows = []
    for name, kw, check in cases():
        r = capture(**kw)
        try:
            ok = bool(check(r))
        except Exception as e:
            ok = False
            r["out"]["error"] = repr(e)
        rows.append({"name": name, "pass": ok, "detail": json.dumps(r["out"])[:200]})
    return rows

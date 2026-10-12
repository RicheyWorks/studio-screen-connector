# SPDX-License-Identifier: MIT
"""playguard suite: one playguard.ps1 tick against a fake Unreal bridge (TCP, newline JSON) and a fake team-link
(HTTP), with art STATUS.md / QUEUE.md fixtures, for every policy branch."""
import datetime
import http.server
import io
import json
import os
import shutil
import socket
import socketserver
import subprocess
import tempfile
import threading
import time

from . import ps
from .ledger import ROOT

FIX = os.path.join(ROOT, "tests", "fixtures", "art")


class FakeBridge:
    def __init__(self, playing):
        self.playing, self.requests = playing, []
        outer = self

        class H(socketserver.StreamRequestHandler):
            def handle(self):
                line = self.rfile.readline()
                if not line:
                    return
                req = json.loads(line)
                outer.requests.append(req["type"])
                if req["type"] == "stop":
                    outer.playing = False
                res = {"id": req.get("id"), "status": "ok", "result": {"playing": outer.playing}}
                self.wfile.write((json.dumps(res) + "\n").encode())

        socketserver.ThreadingTCPServer.allow_reuse_address = True
        self.srv = socketserver.ThreadingTCPServer(("127.0.0.1", 0), H)
        self.port = self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def close(self):
        self.srv.shutdown(); self.srv.server_close()


class FakeTeamLink:
    def __init__(self):
        self.posts = []
        outer = self

        class H(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                n = int(self.headers.get("Content-Length") or 0)
                outer.posts.append({"path": self.path, "body": json.loads(self.rfile.read(n) or b"{}")})
                self.send_response(200); self.send_header("Content-Type", "application/json"); self.end_headers()
                self.wfile.write(b'{"ok":true}')

            def log_message(self, *a):
                pass

        self.srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.port = self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def close(self):
        self.srv.shutdown(); self.srv.server_close()


def free_port():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p


def ago(minutes):
    return (datetime.datetime.now() - datetime.timedelta(minutes=minutes)).isoformat()


def tick(script, art, playing=True, enabled=True, play_ago=None, wait_ago=None, bridge_up=True):
    d = tempfile.mkdtemp()
    bridge = FakeBridge(playing) if bridge_up else None
    link = FakeTeamLink()
    cfg = json.load(io.open(os.path.join(ROOT, "playguard.json")))
    cfg.update(enabled=enabled, art_dir=os.path.join(FIX, art), bridge_port=bridge.port if bridge else free_port(),
               teamlink_url="http://127.0.0.1:%d/send" % link.port, stop_timeout_ms=5000)
    cp, sp, lp = (os.path.join(d, n) for n in ("pg.json", "state.json", "tidy.log"))
    json.dump(cfg, io.open(cp, "w"))
    json.dump({"play_since": ago(play_ago) if play_ago is not None else None,
               "wait_since": ago(wait_ago) if wait_ago is not None else None}, io.open(sp, "w"))
    t0 = time.time()
    p = subprocess.run([ps.shell(), "-NoProfile", "-NonInteractive", "-File", script,
                        "-PgConfig", cp, "-PgStateFile", sp, "-PgLog", lp], capture_output=True, text=True, timeout=60)
    secs = time.time() - t0
    try:
        out = json.loads([l for l in p.stdout.splitlines() if l.strip()][-1])
    except (ValueError, IndexError):
        out = {"ok": False, "message": (p.stdout + p.stderr)[-300:]}
    log = io.open(lp).read() if os.path.exists(lp) else ""
    state = json.load(io.open(sp)) if os.path.exists(sp) else {}
    res = {"out": out, "requests": bridge.requests if bridge else [], "posts": link.posts, "log": log,
           "state": state, "secs": secs}
    if bridge:
        bridge.close()
    link.close()
    shutil.rmtree(d, ignore_errors=True)
    return res


def stopped(r):
    return r["out"].get("action") == "stopped" and r["requests"].count("stop") == 1


def not_stopped(r):
    return r["out"].get("ok") is True and "stop" not in r["requests"] and not r["posts"]


def cases():
    return [
        ("disabled: never pings or stops", dict(art="ready", enabled=False, play_ago=60),
         lambda r: r["out"].get("action") == "disabled" and r["requests"] == [] and not r["posts"]),
        ("PIE off: no stop, play timer cleared", dict(art="ready", playing=False, play_ago=60),
         lambda r: not_stopped(r) and r["state"].get("play_since") is None),
        ("PIE long + READY rows: stop, log, note", dict(art="ready", play_ago=9),
         lambda r: stopped(r) and "playguard stopped Play" in r["log"] and "READY" in r["log"]
                   and len(r["posts"]) == 1 and r["posts"][0]["path"] == "/send"
                   and r["posts"][0]["body"].get("from") == "2hot2trot" and r["posts"][0]["body"].get("to") == "*"
                   and r["posts"][0]["body"].get("type") == "note" and "auto-stopped" in r["posts"][0]["body"].get("text", "")),
        ("QUEUE.md READY rows count too", dict(art="queue_ready", play_ago=9), stopped),
        ("PIE long, no READY rows (prose only): no stop", dict(art="idle", play_ago=30), not_stopped),
        ("polls with READ ping, not stop", dict(art="idle", play_ago=30), lambda r: r["requests"] == ["ping"]),
        ("PIE short + READY: no stop", dict(art="ready", play_ago=2), not_stopped),
        ("first sighting starts the play timer, no stop", dict(art="ready"),
         lambda r: not_stopped(r) and r["state"].get("play_since")),
        ("waiting text > wait_max: stop", dict(art="waiting", play_ago=1, wait_ago=4),
         lambda r: stopped(r) and "leave Play mode" in r["out"].get("reason", "") and len(r["posts"]) == 1),
        ("waiting text < wait_max: no stop", dict(art="waiting", play_ago=1, wait_ago=1), not_stopped),
        ("bridge down: reports it fast, no hang", dict(art="ready", play_ago=60, bridge_up=False),
         lambda r: r["out"].get("action") == "bridge_unreachable" and r["secs"] < 15),
    ]


def run(script=None):
    script = script or os.path.join(ROOT, "playguard.ps1")
    rows = []
    for name, kw, check in cases():
        r = tick(script, **kw)
        rows.append({"name": name, "pass": bool(check(r)),
                     "detail": json.dumps({k: r[k] for k in ("out", "requests", "secs")})[:200]})
    return rows

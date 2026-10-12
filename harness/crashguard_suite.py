# SPDX-License-Identifier: MIT
"""crashguard suite: one crashguard.ps1 tick with -FakeProcs (no kills, no launches), a temp project with a UTF-16
PackageRestoreData.json + autosaves, a fake bridge (ping / execute_python) and a fake team-link."""
import io
import json
import os
import re
import shutil
import socketserver
import subprocess
import tempfile
import threading

from . import ps
from .ledger import ROOT
from .playguard_suite import FakeTeamLink, free_port

PKGS = ["/Game/Daedalus/Art/Prop/Voidleaf/M0", "/Game/Daedalus/Art/Prop/Shroom/M2", "/Game/Daedalus/DaedalusWorld"]


class FakeBridge:
    def __init__(self):
        self.requests = []
        outer = self

        class H(socketserver.StreamRequestHandler):
            def handle(self):
                line = self.rfile.readline()
                if not line:
                    return
                req = json.loads(line)
                outer.requests.append(req["type"])
                res = {"id": req.get("id"), "status": "ok", "result": {"playing": False}}
                if req["type"] == "execute_python":
                    m = re.search(r"paths=(.*)\n", req["params"]["code"])
                    paths = json.loads(m.group(1))
                    paths = [paths] if isinstance(paths, str) else paths
                    res["result"] = {"stdout": "CRASHGUARD_SAVED %d %d True\r\n" % (len(paths), len(paths))}
                self.wfile.write((json.dumps(res) + "\n").encode())

        socketserver.ThreadingTCPServer.allow_reuse_address = True
        self.srv = socketserver.ThreadingTCPServer(("127.0.0.1", 0), H)
        self.port = self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def close(self):
        self.srv.shutdown(); self.srv.server_close()


def make_project(d, restore=True, enabled=True, missing_one=False):
    proj = os.path.join(d, "proj")
    auto = os.path.join(proj, "Saved", "Autosaves")
    os.makedirs(auto)
    entries = []
    for i, p in enumerate(PKGS):
        ext = ".umap" if p.endswith("World") else ".uasset"
        rel = p[len("/Game/"):] + "_Auto1" + ext
        entries.append({"PackagePathName": p, "PackageAssetName": p.rsplit("/", 1)[1], "AutoSavePath": "Game/" + rel})
        c = os.path.join(proj, "Content", *(p[len("/Game/"):] + ext).split("/"))
        os.makedirs(os.path.dirname(c), exist_ok=True)
        io.open(c, "w").write("OLD " + p)
        if missing_one and i == 1:
            continue
        f = os.path.join(auto, "Game", *rel.split("/"))
        os.makedirs(os.path.dirname(f), exist_ok=True)
        io.open(f, "w").write("AUTOSAVE " + p)
    if restore:
        txt = json.dumps({"RestoreEnabled": enabled, "Packages": entries}, indent="\t")
        io.open(os.path.join(auto, "PackageRestoreData.json"), "wb").write(txt.encode("utf-16-le"))   # UE: UTF-16, no BOM
    return proj


def tick(script, procs, crashed_log=True, state=None, restore=True, enabled=True, missing_one=False, bridge_up=True, cg_enabled=True, restore_only=False):
    d = tempfile.mkdtemp()
    proj = make_project(d, restore, enabled, missing_one)
    log = os.path.join(proj, "Saved", "Logs", "DaedalusRealm.log")
    os.makedirs(os.path.dirname(log))
    io.open(log, "w").write("LogInit: ok\n" + ("LogD3D12RHI: Error: DXGI_ERROR_INVALID_CALL\nCritical error: ===\n" if crashed_log else "LogExit: clean\n"))
    bridge = FakeBridge() if bridge_up else None
    link = FakeTeamLink()
    cfg = json.load(io.open(os.path.join(ROOT, "crashguard.json")))
    cfg.update(enabled=cg_enabled, project_dir=proj, project_file=os.path.join(proj, "P.uproject"), editor_log=log,
               backup_dir=os.path.join(d, "bk"), bridge_port=bridge.port if bridge else free_port(),
               teamlink_url="http://127.0.0.1:%d/send" % link.port)
    cp, sp, pp, lp = (os.path.join(d, n) for n in ("cg.json", "state.json", "procs.json", "tidy.log"))
    json.dump(cfg, io.open(cp, "w")); json.dump(procs, io.open(pp, "w"))
    json.dump(state or {"crashes": [], "pending_save": []}, io.open(sp, "w"))
    args = [ps.shell(), "-NoProfile", "-NonInteractive", "-File", script, "-CgConfig", cp, "-CgLog", lp, "-FakeProcs", pp]
    args += ["-RestoreOnly"] if restore_only else ["-CgStateFile", sp]
    p = subprocess.run(args, capture_output=True, text=True, timeout=90)
    try:
        out = json.loads([l for l in p.stdout.splitlines() if l.strip()][-1])
    except (ValueError, IndexError):
        out = {"ok": False, "message": (p.stdout + p.stderr)[-300:]}

    def content(pkg):
        ext = ".umap" if pkg.endswith("World") else ".uasset"
        f = os.path.join(proj, "Content", *(pkg[len("/Game/"):] + ext).split("/"))
        return io.open(f).read() if os.path.exists(f) else None

    res = {"out": out, "posts": link.posts, "requests": bridge.requests if bridge else [],
           "log": io.open(lp).read() if os.path.exists(lp) else "",
           "state": json.load(io.open(sp)) if os.path.exists(sp) else {},
           "content": {k: content(k) for k in PKGS},
           "json_left": os.path.exists(os.path.join(proj, "Saved", "Autosaves", "PackageRestoreData.json")),
           "backup_old": any("OLD" in io.open(os.path.join(r, f)).read() for r, _, fs in os.walk(os.path.join(d, "bk")) for f in fs if not f.endswith(".json"))}
    if bridge:
        bridge.close()
    link.close()
    shutil.rmtree(d, ignore_errors=True)
    return res


ED = {"name": "UnrealEditor", "pid": 100, "title": "DaedalusRealm - Unreal Editor"}
DLG = {"name": "UnrealEditor", "pid": 100, "title": "Restore Packages"}
CRC = {"name": "CrashReportClientEditor", "pid": 200, "title": ""}
UBT = {"name": "UnrealBuildTool", "pid": 300, "title": ""}


def launches(r):
    return [a for a in r["out"].get("acts", []) if a.startswith("launch")]


def restored_all(r):
    return all(v and v.startswith("AUTOSAVE") for v in r["content"].values()) and not r["json_left"] and r["backup_old"]


def blocked(r):
    return any(p["body"].get("type") == "BLOCKED" and p["body"].get("to") == "all" and p["body"].get("from") == "2hot2trot"
               and "BLOCKED" in (p["body"].get("body") or {}).get("text", "") for p in r["posts"])


def untouched(r):
    return (r["out"].get("ok") is True and not r["out"].get("acts") and not r["posts"]
            and all(v and v.startswith("OLD") for v in r["content"].values()))


def cases():
    old = {"crashes": ["2000-01-01T00:00:00"], "pending_save": []}
    import datetime
    recent = {"crashes": [(datetime.datetime.now() - datetime.timedelta(minutes=5)).isoformat()], "pending_save": []}
    return [
        ("disabled: does nothing", dict(procs=[CRC], cg_enabled=False),
         lambda r: r["out"].get("action") == "disabled" and untouched(r)),
        ("healthy editor + its CRC companion: no action", dict(procs=[ED, CRC]), untouched),
        ("CRC alive, no editor, clean log: not a crash", dict(procs=[CRC], crashed_log=False), untouched),
        ("crash: kills CRC, BLOCKED note, restores all, relaunches hidden w/o -d3d11",
         dict(procs=[CRC]),
         lambda r: r["out"].get("action") == "crashed" and "kill 200" in r["out"]["acts"] and blocked(r) and restored_all(r)
                   and len(launches(r)) == 1 and "-d3d11" not in launches(r)[0] and r["out"].get("crashes") == 1),
        ("crash restores UTF-16 list incl. .umap, logs package names",
         dict(procs=[CRC]), lambda r: "DaedalusWorld" in r["log"] and "Voidleaf" in r["log"] and "restored 3 package" in r["log"]),
        ("crash queues restored packages for save", dict(procs=[CRC]), lambda r: sorted(r["state"].get("pending_save", [])) == sorted(PKGS)),
        ("2nd crash inside window: relaunch with -d3d11", dict(procs=[CRC], state=recent),
         lambda r: r["out"].get("crashes") == 2 and len(launches(r)) == 1 and "-d3d11" in launches(r)[0]),
        ("old crash outside window doesn't count", dict(procs=[CRC], state=old),
         lambda r: r["out"].get("crashes") == 1 and "-d3d11" not in launches(r)[0]),
        ("Restore Packages dialog: kill editor + CRC, restore, relaunch",
         dict(procs=[DLG, CRC], crashed_log=False),
         lambda r: r["out"].get("action") == "restore_dialog" and "kill 100" in r["out"]["acts"] and "kill 200" in r["out"]["acts"]
                   and restored_all(r) and len(launches(r)) == 1 and blocked(r)),
        ("build tools running: no relaunch", dict(procs=[CRC, UBT]),
         lambda r: r["out"].get("action") == "busy_build" and not launches(r)),
        ("no restore file: relaunch without copying", dict(procs=[CRC], restore=False),
         lambda r: len(launches(r)) == 1 and all(v.startswith("OLD") for v in r["content"].values())),
        ("RestoreEnabled false (clean exit): nothing copied", dict(procs=[CRC], enabled=False),
         lambda r: all(v.startswith("OLD") for v in r["content"].values()) and r["json_left"]),
        ("missing autosave: others restored, missing reported", dict(procs=[CRC], missing_one=True),
         lambda r: r["out"].get("missing") == [PKGS[1]] and r["content"][PKGS[0]].startswith("AUTOSAVE") and r["content"][PKGS[1]].startswith("OLD")),
        ("pending save + bridge up: load+save via bridge, note, cleared",
         dict(procs=[ED], crashed_log=False, state={"crashes": [], "pending_save": PKGS}),
         lambda r: r["out"].get("action") == "saved" and r["out"].get("saved") == 3 and "execute_python" in r["requests"]
                   and r["state"].get("pending_save") in ([], None) and any("restored + saved 3/3" in p["body"]["body"]["text"] for p in r["posts"])),
        ("pending save + bridge down: keeps the list", dict(procs=[ED], crashed_log=False, bridge_up=False, state={"crashes": [], "pending_save": PKGS}),
         lambda r: r["out"].get("ok") is True and sorted(r["state"].get("pending_save", [])) == sorted(PKGS)),
        ("-RestoreOnly restores without launching", dict(procs=[], restore_only=True),
         lambda r: r["out"].get("ok") is True and len(r["out"].get("restored", [])) == 3 and restored_all(r)),
    ]


def run(script=None):
    script = script or os.path.join(ROOT, "crashguard.ps1")
    rows = []
    for name, kw, check in cases():
        r = tick(script, **kw)
        try:
            ok = bool(check(r))
        except Exception as e:
            ok = False; r["out"]["exc"] = repr(e)
        rows.append({"name": name, "pass": ok, "detail": json.dumps({"out": r["out"], "requests": r["requests"]})[:300]})
    return rows

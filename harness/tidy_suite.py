# SPDX-License-Identifier: MIT
"""tidy suite: tidy.ps1 -DryRun decisions against fixture window lists. Also kills tidy mutants."""
import json
import os
import subprocess

from . import ps
from .ledger import ROOT

FIX = os.path.join(ROOT, "tests", "fixtures")


def run_tidy(args, script=None, config=None, fixture="tidy_windows.json"):
    script = script or os.path.join(ROOT, "tidy.ps1")
    cmd = [ps.shell(), "-NoProfile", "-NonInteractive", "-File", script, "-DryRun",
           "-FakeWindows", os.path.join(FIX, fixture)]
    if config:
        cmd += ["-Config", os.path.join(FIX, config)]
    p = subprocess.run(cmd + list(args), capture_output=True, text=True, timeout=60)
    lines = [l for l in p.stdout.splitlines() if l.strip()]
    try:
        return p.returncode, json.loads(lines[-1])
    except (ValueError, IndexError):
        return p.returncode, {"ok": False, "message": (p.stdout + p.stderr)[-300:]}


def closed(o):
    return sorted(o.get("closed") or [])


def cases():
    return [
        ("closes listed floating panels", [], None,
         lambda rc, o: rc == 0 and closed(o) == ["Content Browser", "Content Browser 2", "Message Log", "Output Log"]),
        ("keeps the main editor window", [], None,
         lambda rc, o: "MyLevel - Unreal Editor" not in closed(o)),
        ("keeps an unowned window even if its title is listed", [], None,
         lambda rc, o: any(k["title"] == "Content Browser" and k["why"] == "main/unowned window" for k in o.get("kept", []))),
        ("keeps dialogs (Save, Message, Restore, Import)", [], None,
         lambda rc, o: not set(closed(o)) & {"Save Content", "Message", "Restore Packages", "Import Warning"}),
        ("dialog guard beats tidy.json", [], "tidy_dialog_listed.json",
         lambda rc, o: rc == 0 and closed(o) == ["Content Browser"]),
        ("'Message Log' is not treated as a dialog", [], None,
         lambda rc, o: "Message Log" in closed(o)),
        ("keeps unlisted owned windows (Plugins)", [], None,
         lambda rc, o: "Plugins" not in closed(o)),
        ("ignores other processes", [], None,
         lambda rc, o: "Details" not in closed(o) and all(k["title"] != "Details" for k in o.get("kept", []))),
        ("-Minimize changes only the action", ["-Minimize"], None,
         lambda rc, o: o.get("action") == "minimize" and len(closed(o)) == 4),
        ("output is one JSON object with closed + kept", [], None,
         lambda rc, o: o.get("ok") is True and "closed" in o and "kept" in o),
    ]


def run(script=None):
    rows = []
    for name, args, cfg, check in cases():
        rc, out = run_tidy(args, script=script, config=cfg)
        rows.append({"name": name, "pass": bool(check(rc, out)), "detail": str(out)[:200]})
    return rows

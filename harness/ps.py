# SPDX-License-Identifier: MIT
"""Run capture.ps1 in -DryRun mode and parse its one JSON line."""
import json
import os
import shutil
import subprocess

from .ledger import ROOT

FIXTURE = os.path.join(ROOT, "tests", "fixtures", "windows.json")
ALLOW = os.path.join(ROOT, "allowlist.example.json")


def shell():
    for exe in ("pwsh", "powershell"):
        if shutil.which(exe):
            return exe
    raise RuntimeError("no PowerShell found (install pwsh)")


def run(args, script=None, allowlist=ALLOW, fixture=FIXTURE):
    script = script or os.path.join(ROOT, "capture.ps1")
    cmd = [shell(), "-NoProfile", "-NonInteractive", "-File", script, "-DryRun", "-Allowlist", allowlist]
    if fixture:
        cmd += ["-FakeWindows", fixture]
    p = subprocess.run(cmd + list(args), capture_output=True, text=True, timeout=60)
    line = [l for l in p.stdout.strip().splitlines() if l.strip()]
    try:
        out = json.loads(line[-1]) if line else {}
    except ValueError:
        out = {"ok": False, "message": "unparseable: " + p.stdout[-200:] + p.stderr[-200:]}
    return p.returncode, out

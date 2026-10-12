# SPDX-License-Identifier: MIT
"""tidy-start suite: -Status output stays small and log_tail is plain strings (runs on Linux pwsh, no WMI).
Runtime checks run under pwsh 7; the PS 5.1-only metadata bug is guarded by a source check."""
import io
import json
import os
import shutil
import subprocess
import tempfile

from . import ps
from .ledger import ROOT


def status(script=None):
    d = tempfile.mkdtemp()
    shutil.copy(script or os.path.join(ROOT, "tidy-start.ps1"), os.path.join(d, "tidy-start.ps1"))
    with io.open(os.path.join(d, "tidy.log"), "w", encoding="utf-8") as f:
        for i in range(8):
            f.write("2026-10-12T13:00:0%d closed 'Content Browser %d'\n" % (i, i))
    p = subprocess.run([ps.shell(), "-NoProfile", "-NonInteractive", "-File", os.path.join(d, "tidy-start.ps1"), "-Status"],
                       capture_output=True, text=True, timeout=60)
    return p.stdout.strip()


def run(script=None):
    raw = status(script)
    try:
        o = json.loads(raw.splitlines()[-1])
    except (ValueError, IndexError):
        o = {}
    tail = o.get("log_tail")
    src = io.open(script or os.path.join(ROOT, "tidy-start.ps1"), encoding="utf-8").read()
    return [
        # pwsh 7 (CI) serializes Get-Content lines as plain strings anyway; Windows PowerShell 5.1 dumps their
        # PSPath/ReadCount metadata (found live on BLACKBEARD). CI has no 5.1, so the cast is also checked in source.
        {"name": "log_tail lines are cast to [string] (PS 5.1 metadata bug)",
         "pass": "Get-Content $log -Tail 5 | ForEach-Object { [string]$_ }" in src, "detail": "source check"},
        {"name": "-Status reports not running without a lock", "pass": o.get("running") is False, "detail": raw[:200]},
        {"name": "log_tail is the last 5 lines as plain strings",
         "pass": isinstance(tail, list) and len(tail) == 5 and all(isinstance(t, str) for t in tail)
                 and tail[-1].endswith("'Content Browser 7'"), "detail": str(tail)[:200]},
        {"name": "-Status output stays small (< 2 KB)", "pass": len(raw) < 2048, "detail": "%d bytes" % len(raw)},
    ]

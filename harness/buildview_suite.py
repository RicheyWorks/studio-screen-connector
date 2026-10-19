# SPDX-License-Identifier: MIT
"""Build-view + Blender splash suite: statuspage.py, status-capture.ps1 (stub capture.ps1), blender_prefs.py (fake bpy),
set-blender-prefs.ps1 -DryRun. No Win32, no Blender, no pixels; runs under pwsh on Linux CI and on BLACKBEARD."""
import base64
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import types

from . import ps
from .ledger import ROOT

sys.path.insert(0, ROOT)
import statuspage  # noqa: E402

PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")


def _write(path, text):
    with io.open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


def _fake_bpy(saved):
    view = types.SimpleNamespace(show_splash=True)
    ops = types.SimpleNamespace(wm=types.SimpleNamespace(save_userpref=lambda: saved.append(1)))
    return types.SimpleNamespace(context=types.SimpleNamespace(preferences=types.SimpleNamespace(view=view)), ops=ops,
                                 app=types.SimpleNamespace(version_string="4.3.1")), view


def _run_prefs_script():
    saved = []
    bpy, view = _fake_bpy(saved)
    sys.modules["bpy"] = bpy
    buf = io.StringIO()
    old = sys.stdout
    try:
        sys.stdout = buf
        with io.open(os.path.join(ROOT, "blender_prefs.py"), encoding="utf-8") as f:
            exec(compile(f.read(), "blender_prefs.py", "exec"), {"__name__": "__main__"})
    finally:
        sys.stdout = old
        sys.modules.pop("bpy", None)
    line = [l for l in buf.getvalue().splitlines() if l.startswith("BLENDER_PREFS ")]
    return view.show_splash, len(saved), json.loads(line[0][14:]) if line else {}


def _stub_capture(d, png_ok=True, blank=False):
    """A fake capture.ps1: records its arguments, writes a PNG, prints the one JSON line the real one prints."""
    png = os.path.join(d, "fixture.png")
    with io.open(png, "wb") as f:
        f.write(PNG)
    body = (
        "param([string]$Op='capture',[string]$Target='',[string]$OutDir='',[string]$Method='')\n"
        "Add-Content -LiteralPath '%s' -Value ($Target + '|' + $Method)\n"
        "if ($Target -eq 'blender') { '{\"ok\":false,\"message\":\"window is minimized\"}'; return }\n"
        "$p = Join-Path $OutDir ($Target + '-x.png'); Copy-Item -LiteralPath '%s' -Destination $p\n"
        "(@{ ok = $true; target = $Target; path = $p; method = 'printwindow'; title = 'T ' + $Target; blank = %s } | ConvertTo-Json -Compress)\n"
    ) % (os.path.join(d, "calls.txt").replace("'", "''"), png.replace("'", "''"), "$true" if blank else "$false")
    p = os.path.join(d, "stub-capture.ps1")
    _write(p, body)
    return p


def _capture(d, extra=()):
    live = os.path.join(d, "live")
    p = subprocess.run([ps.shell(), "-NoProfile", "-NonInteractive", "-File", os.path.join(ROOT, "status-capture.ps1"),
                        "-LiveDir", live, "-CaptureScript", _stub_capture(d)] + list(extra), capture_output=True, text=True, timeout=90)
    state = {}
    sp = os.path.join(live, "capture-state.json")
    if os.path.exists(sp):
        state = json.load(io.open(sp, encoding="utf-8-sig"))
    calls = io.open(os.path.join(d, "calls.txt"), encoding="utf-8").read().split() if os.path.exists(os.path.join(d, "calls.txt")) else []
    return live, state, calls, p


def run():
    rows = []

    def add(name, ok, detail=""):
        rows.append({"name": name, "pass": bool(ok), "detail": str(detail)[:300]})

    # --- Blender splash ---
    splash, saves, info = _run_prefs_script()
    add("blender_prefs.py sets show_splash False and saves the user prefs", splash is False and saves == 1 and info.get("saved") is True, info)
    src = io.open(os.path.join(ROOT, "set-blender-prefs.ps1"), encoding="utf-8").read()
    add("set-blender-prefs.ps1 never uses --factory-startup for the real run", "--factory-startup" not in [l for l in src.splitlines() if "ArgumentList" in l][0], "source check")
    p = subprocess.run([ps.shell(), "-NoProfile", "-NonInteractive", "-File", os.path.join(ROOT, "set-blender-prefs.ps1"), "-DryRun", "-Live"],
                       capture_output=True, text=True, timeout=60)
    try:
        o = json.loads([l for l in p.stdout.splitlines() if l.strip()][-1])
    except (ValueError, IndexError):
        o = {}
    add("set-blender-prefs.ps1 -DryRun changes nothing and reports the plan", o.get("ok") is True and "would" in str(o.get("background")) and "bridge" in str(o.get("live")), p.stdout[:200])

    # --- statuspage.py ---
    add("redact hides passwords, tokens and 32-byte hex keys",
        "hunter2" not in statuspage.redact("AUTH_PASSWORD=hunter2 ok") and "[redacted-hex]" in statuspage.redact("k 0x" + "ab" * 32)
        and "abc123" not in statuspage.redact("api_key: abc123"), statuspage.redact("AUTH_PASSWORD=hunter2"))
    art = statuspage.parse_art_status(io.open(os.path.join(ROOT, "tests", "fixtures", "art", "board", "STATUS.md"), encoding="utf-8").read())
    add("parse_art_status reads counts, updated stamp, waiting-on", art["counts"].get("READY") == 12 and art["counts"].get("SHIPPED") == 40 and art["updated"] == "2026-10-18T20:05:14" and "Unreal" in art["waiting_on"] and art["rows"] == 59, art)
    d = tempfile.mkdtemp()
    try:
        log = os.path.join(d, "log.jsonl")
        with io.open(log, "w", encoding="utf-8") as f:
            f.write(json.dumps({"id": 1, "ts": "2026-10-18T20:00:00", "from": "bot-a", "type": "CLAIM", "body": {"text": "CLAIM thing <script>alert(1)</script>"}}) + "\n")
            for i in range(5000):
                f.write(json.dumps({"id": 10 + i, "ts": "2026-10-18T20:01:00", "from": "x", "to": "y", "type": "ack", "body": {"id": i}}) + "\n")
            f.write(json.dumps({"id": 9000, "ts": "2026-10-18T20:10:00", "from": "bot-b", "type": "note", "body": {"text": "DONE it AUTH_PASSWORD=hunter2"}}) + "\n")
            f.write(json.dumps({"id": 9001, "ts": "2026-10-18T20:11:00", "from": "bot-c", "type": "lock", "body": {"name": "unreal"}}) + "\n")
        vis = statuspage.tail_visible(log, want=2, start_bytes=2000)
        add("tail_visible widens the window past ack/lock noise", [m["from"] for m in vis] == ["bot-a", "bot-b"], [m["from"] for m in vis])
        live = os.path.join(d, "live")
        os.makedirs(live)
        art_dir = os.path.join(d, "art")
        shutil.copytree(os.path.join(ROOT, "tests", "fixtures", "art", "board"), art_dir)
        io.open(os.path.join(live, "blender.png"), "wb").write(PNG)
        rc = statuspage.main(["--art", art_dir, "--link-log", log, "--live", live, "--no-ports", "--now", "2026-10-18T20:12:00"])
        page = io.open(os.path.join(live, "index.html"), encoding="utf-8").read()
        add("statuspage main writes index.html + status.json", rc == 0 and os.path.exists(os.path.join(live, "status.json")), "")
        add("page escapes message HTML", "<script>alert" not in page and "&lt;script&gt;" in page, "")
        add("page redacts secrets and auto-refreshes", "hunter2" not in page and "http-equiv='refresh'" in page, "")
        add("page shows the art counts and feed events", "READY 12" in page and "Prop_Bad_01" in page, "")
        add("page shows the screenshot and each bot's last post", "blender.png?v=" in page and "bot-a" in page and "bot-b" in page and "bot-c" not in page, "")
        data = json.load(io.open(os.path.join(live, "status.json"), encoding="utf-8"))
        add("bots sorted newest first; only bots that posted something other than locks", [b["bot"] for b in data["link"]["bots"]] == ["bot-b", "bot-a"], [b["bot"] for b in data["link"]["bots"]])
    finally:
        shutil.rmtree(d, ignore_errors=True)

    # --- status-capture.ps1 with a stub capture.ps1 ---
    d = tempfile.mkdtemp()
    try:
        live, state, calls, pr = _capture(d)
        add("status-capture copies a good window shot to <live>/<target>.png", os.path.exists(os.path.join(live, "unreal.png")) and state.get("unreal", {}).get("ok") is True, pr.stdout[:200] + pr.stderr[:200])
        add("status-capture keeps no image for a minimized window and says why", not os.path.exists(os.path.join(live, "blender.png"))
            and state.get("blender", {}).get("ok") is False and "minimized" in state["blender"].get("message", "")
            and "-BlenderBridge" in state["blender"]["message"], state.get("blender"))
        add("unreal is read window-only unless -UnrealBridge is given", "unreal|window" in calls, calls)
    finally:
        shutil.rmtree(d, ignore_errors=True)
    d = tempfile.mkdtemp()
    try:
        live, state, calls, pr = _capture(d, ["-UnrealBridge"])
        add("-UnrealBridge switches capture.ps1 to -Method auto", "unreal|auto" in calls, calls)
    finally:
        shutil.rmtree(d, ignore_errors=True)
    return rows

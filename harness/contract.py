# SPDX-License-Identifier: MIT
"""Contract suite: shape of capture.ps1's JSON and target selection, in dry-run (no Win32)."""
from . import ps


def cases():
    return [
        ("capture claude picks largest real Claude window", ["-Target", "claude"],
         lambda rc, o: rc == 0 and o.get("ok") and o.get("hwnd") == 101 and o.get("target") == "claude"),
        ("capture unreal picks main editor", ["-Target", "unreal"],
         lambda rc, o: rc == 0 and o.get("hwnd") == 201),
        ("capture by allowlisted hwnd", ["-Hwnd", "202"],
         lambda rc, o: rc == 0 and o.get("target") == "unreal"),
        ("minimized blender reports error, not refusal", ["-Target", "blender"],
         lambda rc, o: rc == 1 and not o.get("ok") and o.get("refused") is False and "minimized" in o.get("message", "")),
        ("missing target is an error", [],
         lambda rc, o: rc == 1 and "Target" in o.get("message", "")),
        ("unknown hwnd is an error", ["-Hwnd", "999"],
         lambda rc, o: rc == 1 and o.get("refused") is False),
        ("list returns only allowlisted windows", ["-Op", "list"],
         lambda rc, o: rc == 0 and sorted(w["hwnd"] for w in o.get("windows", [])) == [101, 102, 201, 202, 301]),
        ("-Op shot is an alias of capture", ["-Op", "shot", "-Target", "claude"],
         lambda rc, o: rc == 0 and o.get("hwnd") == 101),
        ("-Process keeps matching app", ["-Target", "claude", "-Process", "claude"],
         lambda rc, o: rc == 0 and o.get("hwnd") == 101),
        ("every output is one JSON object with ok", ["-Target", "claude"],
         lambda rc, o: isinstance(o, dict) and "ok" in o and o.get("dry_run") is True),
    ]


def run(script=None):
    rows = []
    for name, args, check in cases():
        rc, out = ps.run(args, script=script)
        rows.append({"name": name, "pass": bool(check(rc, out)), "detail": str(out)[:200]})
    return rows

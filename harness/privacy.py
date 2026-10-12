# SPDX-License-Identifier: MIT
"""Privacy suite: every deny path must come back refused. Also used to kill guard mutants."""
import io
import json
import os
import tempfile

from . import ps


def _allow(overrides):
    with io.open(ps.ALLOW, encoding="utf-8") as f:
        a = json.load(f)
    a.update(overrides)
    fd, p = tempfile.mkstemp(suffix=".json")
    with os.fdopen(fd, "w") as f:
        json.dump(a, f)
    return p


CLEAN = os.path.join(os.path.dirname(ps.FIXTURE), "clean.json")


def _refused(rc, o):
    return rc == 1 and o.get("ok") is False and o.get("refused") is True


def cases():
    screen_on = _allow({"screen": {"enabled": True}})
    return [
        ("target not in allowlist", ["-Target", "chrome"], None, _refused),
        ("denylisted hwnd (Bitwarden)", ["-Hwnd", "401"], None, _refused),
        ("denylisted hwnd (Gmail)", ["-Hwnd", "402"], None, _refused),
        ("deny title wins over allow-ish title", ["-Hwnd", "403"], None, _refused),
        ("process must match, not just title", ["-Hwnd", "404"], None, _refused),
        ("deny title wins even on allowlisted process", ["-Hwnd", "405"], None, _refused),
        ("screen disabled by default", ["-Target", "screen"], None, _refused),
        ("screen disabled even with no denylisted windows", ["-Target", "screen"], None, _refused, CLEAN),
        ("-Process filter excludes other allowlisted apps", ["-Target", "unreal", "-Process", "claude"], None,
         lambda rc, o: rc == 1 and o.get("ok") is False),
        ("screen refused while denylisted window visible", ["-Target", "screen"], screen_on, _refused),
        ("list never shows denylisted windows", ["-Op", "list"], None,
         lambda rc, o: rc == 0 and not any(w["hwnd"] in (401, 402, 403, 404, 405) for w in o.get("windows", []))),
    ]


def run(script=None):
    rows = []
    for c in cases():
        name, args, allow, check = c[:4]
        kw = {"script": script}
        if len(c) > 4:
            kw["fixture"] = c[4]
        if allow:
            kw["allowlist"] = allow
        rc, out = ps.run(args, **kw)
        rows.append({"name": name, "pass": bool(check(rc, out)), "detail": str(out)[:200]})
    return rows

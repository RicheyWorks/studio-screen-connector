# SPDX-License-Identifier: MIT
"""Mutate the privacy guard in capture.ps1; the privacy suite must fail (kill) each mutant."""
import io
import os
import tempfile

from . import privacy
from .ledger import ROOT

MUTANTS = [
    ("drop denylist in Find-Rule", "if ($win.title -match $DenyTitle) { return $null }", ""),
    ("screen enabled ignored", "$rule.enabled -ne $true", "$false"),
    ("process match dropped", "if ($rule.process -and ($win.process -notmatch", "if ($false -and ($win.process -notmatch"),
    ("unknown target allowed", "return @{ refused = \"'$Target' is not in the allowlist\" }", "$null = 0"),
    ("deny check before screen dropped", "if ($deny.Count -gt 0)", "if ($false)"),
]


def run():
    src = io.open(os.path.join(ROOT, "capture.ps1"), encoding="utf-8").read()
    rows = []
    for name, old, new in MUTANTS:
        if old not in src:
            rows.append({"name": name, "verdict": "inconclusive", "detail": "anchor not found"})
            continue
        d = tempfile.mkdtemp()
        p = os.path.join(d, "capture.ps1")
        io.open(p, "w", encoding="utf-8").write(src.replace(old, new, 1))
        failed = [r["name"] for r in privacy.run(script=p) if not r["pass"]]
        rows.append({"name": name, "verdict": "killed" if failed else "SURVIVED", "detail": ", ".join(failed)})
    return rows

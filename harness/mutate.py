# SPDX-License-Identifier: MIT
"""Mutate the privacy guard in capture.ps1; the privacy suite must fail (kill) each mutant."""
import io
import os
import tempfile

from . import privacy, tidy_suite
from .ledger import ROOT

MUTANTS = [
    ("drop denylist in Find-Rule", "if ($win.title -match $DenyTitle) { return $null }", ""),
    ("screen enabled ignored", "$rule.enabled -ne $true", "$false"),
    ("process match dropped", "if ($rule.process -and ($win.process -notmatch", "if ($false -and ($win.process -notmatch"),
    ("unknown target allowed", "return @{ refused = \"'$Target' is not in the allowlist\" }", "$null = 0"),
    ("deny check before screen dropped", "if ($deny.Count -gt 0)", "if ($false)"),
]


TIDY_MUTANTS = [
    ("tidy: process rule dropped", 'if ($w.process -ine "UnrealEditor") { return "other process" }', ""),
    ("tidy: owner rule dropped", 'if ([long]$w.owner -eq 0) { return "main/unowned window" }', ""),
    ("tidy: dialog rule dropped", 'if ($w.title -match $DialogTitle) { return "looks like a dialog" }', ""),
    ("tidy: list rule dropped", 'if ($list -notcontains $w.title.ToLowerInvariant()) { return "not in tidy.json" }', ""),
    ("tidy: Message Log exception dropped", "message(?!\\s+log)", "message"),
]


def _mutate(script, mutants, suite):
    src = io.open(os.path.join(ROOT, script), encoding="utf-8").read()
    rows = []
    for name, old, new in mutants:
        if old not in src:
            rows.append({"name": name, "verdict": "inconclusive", "detail": "anchor not found"})
            continue
        d = tempfile.mkdtemp()
        p = os.path.join(d, script)
        io.open(p, "w", encoding="utf-8").write(src.replace(old, new, 1))
        failed = [r["name"] for r in suite.run(script=p) if not r["pass"]]
        rows.append({"name": name, "verdict": "killed" if failed else "SURVIVED", "detail": ", ".join(failed)})
    return rows


def run():
    return _mutate("capture.ps1", MUTANTS, privacy) + _mutate("tidy.ps1", TIDY_MUTANTS, tidy_suite)


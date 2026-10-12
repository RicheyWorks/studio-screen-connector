# SPDX-License-Identifier: MIT
"""Mutate the privacy guard in capture.ps1; the privacy suite must fail (kill) each mutant."""
import io
import os
import tempfile

from . import playguard_suite, privacy, start_suite, tidy_suite
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


START_MUTANTS = [
    ("tidy-start: log_tail string cast dropped", "Get-Content $log -Tail 5 | ForEach-Object { [string]$_ }", "Get-Content $log -Tail 5"),
]

PG_MUTANTS = [
    ("playguard: enabled check dropped in tick", 'if ($cfg.enabled -ne $true) { return @{ action = "disabled" } }', ""),
    ("playguard: READY condition dropped", "-gt [double]$cfg.max_play_min -and $ready -gt 0", "-gt [double]$cfg.max_play_min"),
    ("playguard: play duration condition dropped", "$playMin -gt [double]$cfg.max_play_min -and", "$true -and"),
    ("playguard: wait duration condition dropped", "if ($waitMin -gt [double]$cfg.wait_max_min)", "if ($waitMin -ge 0)"),
    ("playguard: polls with stop", 'Send-BridgeCommand $cfg "ping"', 'Send-BridgeCommand $cfg "stop"'),
    ("playguard: READY row anchor dropped", "$l -match '^\\s*(\\||[-*]\\s)' -and ", ""),
    ("playguard: no team-link note", '$noted = Send-TeamNote $cfg', '$noted = $false #'),
    ("playguard: no log line", "if ($logPath) { try { Add-Content", "if ($false) { try { Add-Content"),
]


def run():
    return (_mutate("playguard.ps1", PG_MUTANTS, playguard_suite) + _mutate("capture.ps1", MUTANTS, privacy) + _mutate("tidy.ps1", TIDY_MUTANTS, tidy_suite)
            + _mutate("tidy-start.ps1", START_MUTANTS, start_suite))


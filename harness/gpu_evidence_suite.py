# SPDX-License-Identifier: MIT
"""GPU evidence fixtures: function ASTs, fixed UTC facts and temp bytes only.

No production entrypoint, native process/window inventory, network, restore,
relaunch or bridge call is permitted. Every such helper is replaced by a trap.
"""
import datetime as dt
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile

from . import ps
from .ledger import ROOT


NOW = "2026-10-14T15:43:00Z"
START = "2026-10-14T15:40:00Z"
EVENT = "2026-10-14T15:42:00Z"
LIMIT = 256 * 1024
ED = {"name": "UnrealEditor", "pid": 100, "title": "fixture", "started_utc": START}
CRC = {"name": "CrashReportClientEditor", "pid": 200, "title": ""}

DRIVER = r'''
param([string]$Source, [string]$Fixture)
$ErrorActionPreference = 'Stop'
$tokens = $null; $parseErrors = $null
$ast = [Management.Automation.Language.Parser]::ParseFile($Source, [ref]$tokens, [ref]$parseErrors)
if ($parseErrors.Count) { throw 'production source has parse errors' }
$functions = @($ast.EndBlock.Statements | Where-Object { $_ -is [Management.Automation.Language.FunctionDefinitionAst] })
foreach ($required in @('Get-GpuCrashEvidence', 'Invoke-CrashGuard')) {
    if (@($functions | Where-Object { $_.Name -eq $required }).Count -ne 1) { throw "missing or duplicate helper: $required" }
}
foreach ($fn in $functions) { Invoke-Expression $fn.Extent.Text }
$literal = @($ast.EndBlock.Statements | Where-Object {
    $_ -is [Management.Automation.Language.AssignmentStatementAst] -and $_.Left.VariablePath.UserPath -eq 'CrashLogPattern'
})
if ($literal.Count -eq 1 -and $literal[0].Right -is [Management.Automation.Language.CommandExpressionAst] -and
    $literal[0].Right.Expression -is [Management.Automation.Language.StringConstantExpressionAst]) {
    $script:CrashLogPattern = $literal[0].Right.Expression.Value
}
$script:traps = New-Object System.Collections.ArrayList
foreach ($name in @('Stop-Ids', 'Start-EditorHidden', 'Invoke-PreLaunchRestore', 'Send-CgBridge', 'Set-CrashPrivacy',
    'Get-Process', 'Stop-Process', 'Start-Process', 'Invoke-CimMethod', 'New-CimInstance', 'Get-CimInstance',
    'Get-WmiObject', 'Invoke-WmiMethod', 'Invoke-RestMethod', 'Invoke-WebRequest', 'Start-Sleep', 'Add-Type')) {
    $body = "[void]`$script:traps.Add('$name'); throw 'forbidden fixture action: $name'"
    Set-Item -Path ("Function:" + $name) -Value ([scriptblock]::Create($body))
}
function Get-Procs([string]$fake) { return ,$script:fakeProcs }
function Send-CgNote($cfg, [string]$type, [string]$text) {
    [void]$script:notes.Add(@{ type = $type; text = $text })
    return $script:noteResult
}
$fixtureData = Get-Content -LiteralPath $Fixture -Raw -Encoding UTF8 | ConvertFrom-Json
$rows = New-Object System.Collections.ArrayList
foreach ($scenario in $fixtureData) {
    $state = @{ crashes = @($scenario.crashes | ForEach-Object { $_.Substring(4) }); pending_save = @($scenario.pending_save) }
    $script:notes = New-Object System.Collections.ArrayList
    $script:noteResult = $scenario.note_result
    $script:traps.Clear()
    $cfg = [pscustomobject]@{ enabled = $true; editor_log = ''; project_dir = $scenario.project_dir;
        project_file = 'fixture.uproject'; editor_exe = 'forbidden.exe'; backup_dir = $scenario.project_dir;
        extra_args = ''; d3d11_after_crashes = 2; crash_window_min = 60; from = 'fixture'; teamlink_url = 'forbidden' }
    $observed = New-Object System.Collections.ArrayList
    foreach ($step in $scenario.steps) {
        $cfg.editor_log = $step.log
        $script:fakeProcs = @($step.procs)
        foreach ($p in $script:fakeProcs) {
            if ($p.started_utc -is [string] -and $p.started_utc.StartsWith('utc:')) { $p.started_utc = $p.started_utc.Substring(4) }
        }
        $at = [datetime]::Parse($step.now.Substring(4), [Globalization.CultureInfo]::InvariantCulture, [Globalization.DateTimeStyles]::RoundtripKind)
        try {
            if ($step.mode -eq 'guard') { $result = Invoke-CrashGuard $cfg $state '' 'fixture-only' $at }
            else { $result = Get-GpuCrashEvidence $cfg $script:fakeProcs $state $at }
            $snapshot = @{ result = $result; state = $state; notes = @($script:notes); traps = @($script:traps) }
            # Freeze every observation before a later tick mutates the shared state.
            [void]$observed.Add(($snapshot | ConvertTo-Json -Depth 20 -Compress))
        } catch {
            [void]$observed.Add((@{ error = $_.Exception.Message; traps = @($script:traps); notes = @($script:notes) } | ConvertTo-Json -Depth 10 -Compress))
        }
    }
    [void]$rows.Add(@{ name = $scenario.name; steps = @($observed) })
}
ConvertTo-Json -InputObject @($rows) -Depth 24 -Compress
'''


CLOSURE_DRIVER = r'''
param([string]$TidySource, [string]$GuardSource)
$ErrorActionPreference = 'Stop'
foreach ($item in @(@{ path=$TidySource; name='Invoke-WatcherTick' }, @{ path=$GuardSource; name='Invoke-CrashGuard' })) {
    $tokens=$null; $errors=$null
    $ast=[Management.Automation.Language.Parser]::ParseFile($item.path,[ref]$tokens,[ref]$errors)
    if ($errors.Count) { throw 'source parse error' }
    $fn=@($ast.EndBlock.Statements | Where-Object { $_ -is [Management.Automation.Language.FunctionDefinitionAst] -and $_.Name -eq $item.name })
    if ($fn.Count -ne 1) { throw ('missing isolated helper: '+$item.name) }
    Invoke-Expression $fn[0].Extent.Text
    if ($item.name -eq 'Invoke-WatcherTick') {
        $loops=@($ast.FindAll({param($node) $node -is [Management.Automation.Language.WhileStatementAst] -and
            $node.Condition.Extent.Text -eq '$true'},$true))
        if ($loops.Count -ne 1 -or $loops[0].Body.Extent.Text -notmatch 'Invoke-WatcherTick' -or
            $loops[0].Body.Extent.Text -match 'Invoke-(CrashGuard|PlayGuard|Tidy)\b') { throw 'watcher loop bypasses isolated tick' }
    }
}
$guardBody=(Get-Command Invoke-CrashGuard).ScriptBlock
$script:traps=New-Object Collections.ArrayList
foreach ($name in @('Get-Process','Stop-Process','Start-Process','Invoke-CimMethod','New-CimInstance','Get-CimInstance',
    'Get-WmiObject','Invoke-WmiMethod','Invoke-RestMethod','Invoke-WebRequest','Start-Sleep','Add-Type',
    'Stop-Ids','Start-EditorHidden','Invoke-PreLaunchRestore','Set-CrashPrivacy')) {
    Set-Item ('Function:'+$name) ([scriptblock]::Create("[void]`$script:traps.Add('$name'); throw 'forbidden fixture action'"))
}
function Add-Content { }
function Invoke-PlayGuard { [void]$script:calls.Add('playguard') }
function Invoke-Tidy { [void]$script:calls.Add('tidy'); return @{closed=@();kept=@()} }
function Invoke-CrashGuard($cfg,$state) {
    [void]$script:calls.Add('crashguard')
    if ($script:mode -eq 'throw') { throw 'fixture observation failed' }
    $state.gpu_hold=if ($script:mode -eq 'hold') { @{session_key='fixture'} } else { $null }
}
$LogFile='forbidden-fixture-log'; $Minimize=$false
$at=[datetime]'2026-10-14T15:43:00Z'
$rows=New-Object Collections.ArrayList
foreach ($case in @(
    @{name='fatal observation precedes playguard and tidy';held=$false;mode='hold';sec=0;cg_due=$true;pg_due=$true;want='crashguard';end_hold=$true},
    @{name='latched hold blocks intervening watcher ticks';held=$true;mode='clear';sec=3;cg_due=$false;pg_due=$true;want='';end_hold=$true},
    @{name='failed observation preserves the existing hold';held=$true;mode='throw';sec=12;cg_due=$true;pg_due=$true;want='crashguard';end_hold=$true},
    @{name='verified session release permits scheduled actions';held=$true;mode='clear';sec=12;cg_due=$true;pg_due=$true;want='crashguard,playguard,tidy';end_hold=$false},
    @{name='healthy watcher retains guard order';held=$false;mode='clear';sec=0;cg_due=$true;pg_due=$true;want='crashguard,playguard,tidy';end_hold=$false},
    @{name='hold release retains playguard cadence';held=$true;mode='clear';sec=12;cg_due=$true;pg_due=$false;want='crashguard,tidy';end_hold=$false}
)) {
    $script:calls=New-Object Collections.ArrayList; $script:mode=$case.mode
    $state=@{gpu_hold=$(if ($case.held) {@{session_key='fixture'}} else {$null})}
    $schedule=@{crashguard_last=$(if ($case.cg_due) {[datetime]::MinValue} else {$at});
                playguard_last=$(if ($case.pg_due) {[datetime]::MinValue} else {$at})}
    Invoke-WatcherTick @{enabled=$true;check_sec=10} $state @{enabled=$true;check_sec=15} @{} $schedule @() $at.AddSeconds($case.sec)
    [void]$rows.Add(@{name=$case.name;pass=(($script:calls -join ',') -eq $case.want -and [bool]$state.gpu_hold -eq $case.end_hold);
                     detail=($script:calls -join ',')})
}
# Exercise the original production guard body with all external effects mocked.
function Get-Procs { return @() }
function Get-GpuCrashEvidence { return @{fatal=$false} }
function Test-LogCrashed { return $false }
function Get-CrashAction { return 'none' }
function Send-CgBridge($cfg,$kind,$parameters,$timeout) {
    [void]$script:calls.Add($kind)
    if ($kind -eq 'ping') { return @{status='ok'} }
    return $script:receipt
}
function Send-CgNote { [void]$script:calls.Add('note'); return $true }
foreach ($case in @(
    @{name='all requested packages saved';status='ok';text="CRASHGUARD_SAVED 3 3 True`r`n";ok=$true},
    @{name='no loaded packages cannot clear pending';status='ok';text='CRASHGUARD_SAVED 0 3 True';ok=$false},
    @{name='partial load cannot clear pending';status='ok';text='CRASHGUARD_SAVED 2 3 True';ok=$false},
    @{name='different requested count cannot clear pending';status='ok';text='CRASHGUARD_SAVED 2 2 True';ok=$false},
    @{name='failed save cannot clear pending';status='ok';text='CRASHGUARD_SAVED 3 3 False';ok=$false},
    @{name='error response cannot clear pending';status='error';text='CRASHGUARD_SAVED 3 3 True';ok=$false},
    @{name='ambiguous repeated proof cannot clear pending';status='ok';text="CRASHGUARD_SAVED 3 3 True`nCRASHGUARD_SAVED 3 3 True";ok=$false},
    @{name='oversized counts cannot clear pending';status='ok';text='CRASHGUARD_SAVED 99999999999999999 3 True';ok=$false},
    @{name='embedded marker cannot clear pending';status='ok';text='unrelated CRASHGUARD_SAVED 3 3 True';ok=$false},
    @{name='missing proof cannot clear pending';status='ok';text='';ok=$false}
)) {
    $script:calls=New-Object Collections.ArrayList
    $script:receipt=@{status=$case.status;result=@{stdout=$case.text}}
    $pending=@('/Game/Fixture/A','/Game/Fixture/B','/Game/Fixture/C')
    $state=@{crashes=@();pending_save=$pending}
    try {
        $out=& $guardBody @{enabled=$true} $state '' 'fixture-only' $at
        $ok=if ($case.ok) {$out.action -eq 'saved' -and $out.saved -eq 3 -and @($state.pending_save).Count -eq 0 -and ($script:calls -join ',') -eq 'ping,execute_python,note'}
            else {$out.action -eq 'save_failed' -and ($state.pending_save -join ',') -eq ($pending -join ',') -and ($script:calls -join ',') -eq 'ping,execute_python'}
    } catch { $ok=$false; $out=@{action=('error: '+$_.Exception.Message)} }
    [void]$rows.Add(@{name=$case.name;pass=[bool]$ok;detail=$out.action})
}
if ($script:traps.Count) { throw ('forbidden actions: '+($script:traps -join ',')) }
ConvertTo-Json -InputObject @($rows) -Depth 6 -Compress
'''


def closure_checks(tidy_script=None, guard_script=None, shell=None):
    """Focused mocked watcher/save checks; never execute production entrypoints."""
    with tempfile.TemporaryDirectory(prefix="gpu-watch-closure-") as directory:
        driver = Path(directory) / "driver.ps1"
        driver.write_text(CLOSURE_DRIVER, encoding="utf-8")
        proc = subprocess.run([shell or ps.shell(), "-NoProfile", "-NonInteractive", "-File", str(driver),
                               "-TidySource", str(tidy_script or Path(ROOT) / "tidy.ps1"),
                               "-GuardSource", str(guard_script or Path(ROOT) / "crashguard.ps1")],
                              capture_output=True, text=True, timeout=30)
        try:
            rows = json.loads(proc.stdout.strip().splitlines()[-1])
            if proc.returncode or not isinstance(rows, list) or len(rows) != 16:
                raise ValueError("focused fixture response mismatch")
            return rows
        except (ValueError, IndexError) as error:
            return [{"name": "isolated watcher/save driver", "pass": False,
                     "detail": (str(error) + ": " + proc.stdout + proc.stderr)[-1600:]}]


def marker(event=EVENT, severity="Error", category="LogD3D12RHI", text="GPU crash detected:"):
    when = dt.datetime.fromisoformat(event.replace("Z", "+00:00"))
    stamp = when.strftime("%Y.%m.%d-%H.%M.%S") + ":%03d" % (when.microsecond // 1000)
    return ("[%s][123]%s: %s: %s fixture\n" % (stamp, category, severity, text)).encode("utf-8")


def step(data=None, procs=None, now=NOW, mode="evidence"):
    return {"data": data, "procs": [dict(ED)] if procs is None else procs, "now": now, "mode": mode}


def fatal(observation):
    r = observation["result"]
    return (r.get("available") is True and r.get("fatal") is True and r.get("pid") == 100
            and r.get("popup_observed") is False and utc(r.get("started_utc")) == utc(START)
            and utc(r.get("event_utc")) == utc(EVENT))


def utc(value):
    # .NET's round-trip format has seven fractional digits; Python 3.10 accepts
    # at most six, sufficient for the millisecond fixture timestamps.
    value = re.sub(r"(\.\d{6})\d+(?=Z$|[+-]\d\d:\d\d$)", r"\1", value)
    return dt.datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(dt.timezone.utc)


def no_fatal(observation):
    return observation["result"].get("fatal") is False


def unavailable(observation):
    return observation["result"].get("available") is False and no_fatal(observation)


def held(observation):
    return observation["result"].get("action") == "gpu_fatal_hold" and observation["result"].get("acts") == []


def cases():
    harmless = b"LogFixture: harmless trailing diagnostic\n" * 400
    new_pid = dict(ED, pid=101, started_utc="2026-10-14T15:42:30Z")
    reused_pid = dict(ED, started_utc="2026-10-14T15:42:30Z")
    # Tail starts at a marker which is only the suffix of a longer, incomplete line.
    clipped_marker = marker() + b"x" * (LIMIT - len(marker()))
    partial_utf8 = b"\xe9\x9b\xaa\n" + marker()
    partial_utf8 += b"x" * (LIMIT + 2 - len(partial_utf8))
    return [
        ("timestamped GPU marker survives 400 harmless trailing lines", [step(marker() + harmless)], lambda a: fatal(a[0])),
        ("Fatal severity is definite evidence", [step(marker(severity="Fatal"))], lambda a: fatal(a[0])),
        ("large log reads only the final 256 KiB", [step(b"x" * (2 * LIMIT) + b"\n" + marker() + harmless)],
         lambda a: fatal(a[0]) and a[0]["result"].get("read_bytes") == LIMIT and a[0]["result"].get("truncated") is True),
        ("first partial line cannot manufacture a timestamped marker", [step(b"long-prefix-no-newline" + clipped_marker)], lambda a: no_fatal(a[0])),
        ("partial UTF8 at tail boundary does not hide later complete marker", [step(b"long-prefix-no-newline" + partial_utf8)], lambda a: fatal(a[0])),
        ("marker outside bounded tail is not reported", [step(marker() + b"LogFixture: no event\n" * 15000)], lambda a: no_fatal(a[0])),
        ("rotated log event predates current editor start", [step(marker(), [new_pid])], lambda a: no_fatal(a[0])),
        ("same PID with a later start rejects prior session event", [step(marker(), [reused_pid])], lambda a: no_fatal(a[0])),
        ("event older than five minutes is rejected", [step(marker(), now="2026-10-14T15:47:01Z")], lambda a: no_fatal(a[0])),
        ("event more than five seconds in future is rejected", [step(marker(), now="2026-10-14T15:41:54Z")], lambda a: no_fatal(a[0])),
        ("future clock allowance ends at five seconds", [step(marker(), now="2026-10-14T15:41:55Z")], lambda a: fatal(a[0])),
        ("five-minute age boundary remains eligible", [step(marker(), now="2026-10-14T15:47:00Z")], lambda a: fatal(a[0])),
        ("legacy fake process without start is unavailable", [step(marker(), [{k: v for k, v in ED.items() if k != "started_utc"}])], lambda a: unavailable(a[0])),
        ("non-UTC start identity is unavailable", [step(marker(), [dict(ED, started_utc="2026-10-14T15:40:00")])], lambda a: unavailable(a[0])),
        ("malformed start identity is unavailable", [step(marker(), [dict(ED, started_utc="not-a-date")])], lambda a: unavailable(a[0])),
        ("no live or cached editor identity is unavailable", [step(marker(), [CRC])], lambda a: unavailable(a[0])),
        ("ambiguous editor inventory is unavailable", [step(marker(), [ED, new_pid])], lambda a: unavailable(a[0])),
        ("missing log is unavailable", [step(None)], lambda a: unavailable(a[0])),
        ("marker requires the exact GPU fatal category", [step(marker(category="LogFixture"))], lambda a: no_fatal(a[0])),
        ("Warning is not fatal GPU evidence", [step(marker(severity="Warning"))], lambda a: no_fatal(a[0])),
        ("generic DXGI error does not become definite evidence", [step(marker(text="DXGI_ERROR_DEVICE_REMOVED"))], lambda a: no_fatal(a[0])),
        ("GPU marker needs a valid timestamp", [step(marker().replace(b"2026.10.14", b"2026.99.14"))], lambda a: no_fatal(a[0])),
        ("untimestamped GPU marker is not assumed fresh", [step(b"LogD3D12RHI: Error: GPU crash detected: fixture\n")], lambda a: no_fatal(a[0])),
        ("fatal hold precedes restore dialog, CRC, pending save and build", [step(marker(), [dict(ED, title="Restore Packages"), CRC,
            {"name": "UnrealBuildTool", "pid": 300, "title": ""}], mode="guard")], lambda a: held(a[0]) and len(a[0]["notes"]) == 1),
        ("hold survives age, disconnected log and editor exit without renotifying", [step(marker(), mode="guard"),
            step(None, [CRC], now="2026-10-14T16:43:00Z", mode="guard"),
            step(None, [], now="2026-10-14T16:44:00Z", mode="guard")],
         lambda a: all(held(x) and len(x["notes"]) == 1 for x in a)),
        ("ambiguous replacement editors cannot clear a latched hold", [step(marker(), mode="guard"),
            step(b"LogFixture: healthy\n", [ED, new_pid], mode="guard")], lambda a: all(held(x) for x in a)),
        ("unknown replacement start cannot clear a latched hold", [step(marker(), mode="guard"),
            step(b"LogFixture: healthy\n", [{k: v for k, v in ED.items() if k != "started_utc"}], mode="guard")],
         lambda a: all(held(x) for x in a)),
        ("verified new PID and start clears prior hold", [step(marker(), mode="guard"),
            step(marker(), [new_pid], mode="guard")], lambda a: held(a[0]) and a[1]["result"].get("action") == "none"),
        ("verified PID reuse with new start clears prior hold", [step(marker(), mode="guard"),
            step(marker(), [reused_pid], mode="guard")], lambda a: held(a[0]) and a[1]["result"].get("action") == "none"),
        ("healthy editor and CRC with stale DXGI log take no action", [step(b"LogD3D12RHI: Error: DXGI_ERROR_DEVICE_REMOVED\n", [ED, CRC], mode="guard")],
         lambda a: a[0]["result"].get("action") == "none" and a[0]["result"].get("acts") == [] and not a[0]["notes"]),
        ("failed note is a bounded attempt per held session", [step(marker(), mode="guard"), step(marker(), mode="guard")],
         lambda a: all(held(x) and len(x["notes"]) == 1 for x in a)),
    ]


def run(script=None, shell=None):
    script = os.path.abspath(script or os.path.join(ROOT, "crashguard.ps1"))
    definitions = cases()
    with tempfile.TemporaryDirectory(prefix="gpu-evidence-") as directory:
        root = Path(directory)
        sentinel = root / "Saved" / "Autosaves" / "PackageRestoreData.json"
        sentinel.parent.mkdir(parents=True)
        sentinel.write_bytes(b"fixture autosave bytes must remain unchanged\x00\xff")
        fixture = []
        for index, (name, steps, _) in enumerate(definitions):
            # Clear-session controls have no pending save to avoid exercising that
            # unrelated legacy bridge path; hold cases retain one deliberately.
            preserve = name.startswith(("fatal hold", "hold survives", "ambiguous replacement", "failed note"))
            pending = ["/Game/fixture/pending"] if preserve else []
            entry = {"name": name, "steps": [], "project_dir": str(root), "crashes": ["utc:2026-10-14T15:41:00Z"],
                     "pending_save": pending, "note_result": not name.startswith("failed note")}
            for offset, item in enumerate(steps):
                log = root / ("log-%d-%d.txt" % (index, offset))
                if item["data"] is not None:
                    log.write_bytes(item["data"])
                # PS7 auto-parses ISO JSON strings as DateTime. Prefix fixture
                # strings so both PS7 and WinPS5.1 pass the exact intended facts.
                procs = [dict(p, started_utc="utc:" + p["started_utc"]) if isinstance(p.get("started_utc"), str)
                         else dict(p) for p in item["procs"]]
                entry["steps"].append({"procs": procs, "now": "utc:" + item["now"],
                                       "mode": item["mode"], "log": str(log)})
            fixture.append(entry)
        before = {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob("*") if p.is_file()}
        driver, manifest = root / "driver.ps1", root / "fixture.json"
        driver.write_text(DRIVER, encoding="utf-8")
        manifest.write_text(json.dumps(fixture, ensure_ascii=True), encoding="utf-8")
        proc = subprocess.run([shell or ps.shell(), "-NoProfile", "-NonInteractive", "-File", str(driver),
                               "-Source", script, "-Fixture", str(manifest)], capture_output=True, text=True, timeout=60)
        try:
            observed = json.loads(proc.stdout.strip().splitlines()[-1])
            if not isinstance(observed, list) or len(observed) != len(definitions):
                raise ValueError("fixture response count mismatch")
        except (ValueError, IndexError) as exc:
            return [{"name": "isolated helper driver", "pass": False,
                     "detail": (str(exc) + ": " + proc.stdout + proc.stderr)[-1200:]}]
        unchanged = (all((root / path).is_file() and (root / path).read_bytes() == data for path, data in before.items())
                     and {p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file()}
                     == set(before) | {"driver.ps1", "fixture.json"})
        rows = []
        for definition, expected, actual in zip(definitions, fixture, observed):
            name, _, check = definition
            steps = [json.loads(item) for item in actual.get("steps", [])]
            try:
                safety = (proc.returncode == 0 and actual.get("name") == name and len(steps) == len(expected["steps"])
                          and unchanged and all(not x.get("error") and not x.get("traps") for x in steps))
                preserved = all(x["state"].get("crashes") == [c[4:] for c in expected["crashes"]]
                                and x["state"].get("pending_save") == expected["pending_save"] for x in steps)
                bounded = all(0 <= x["result"].get("read_bytes", 0) <= LIMIT for x in steps)
                ok = safety and preserved and bounded and bool(check(steps))
                detail = json.dumps({"name": name, "steps": steps}, ensure_ascii=True)[:1800]
            except (KeyError, TypeError, ValueError, AttributeError) as exc:
                ok, detail = False, repr(exc) + ": " + json.dumps(actual)[:1000]
            rows.append({"name": name, "pass": ok, "detail": detail})
        return rows + closure_checks(guard_script=script, shell=shell)


if __name__ == "__main__":
    rows = run()
    for row in rows:
        print(("PASS " if row["pass"] else "FAIL ") + row["name"])
        if not row["pass"]:
            print(row["detail"])
    print("%d/%d" % (sum(r["pass"] for r in rows), len(rows)))
    raise SystemExit(0 if all(r["pass"] for r in rows) else 1)

# SPDX-License-Identifier: MIT
# studio-screen-connector / tidy.ps1  (Windows PowerShell 5.1+; -DryRun also runs under pwsh on Linux)
# Closes floating Unreal Editor tool tabs (Content Browser, Message Log, ...) that cover the viewport.
#   -Once (default)            close matching windows now; prints {"ok":true,"closed":[titles],"kept":[...]}
#   -Watch -IntervalSec 3      loop forever; single instance via <repo>\tidy.lock; one line per close in <repo>\tidy.log
#   -Minimize                  minimize instead of WM_CLOSE
#   -DryRun -FakeWindows f.json  decide against a fixture (hwnd,title,process,owner); no Win32
# Rules (all must hold to touch a window):
#   1. it belongs to an UnrealEditor process
#   2. it has an owner (GW_OWNER != 0), so the main "X - Unreal Editor" window is never touched
#   3. its title does not look like a dialog or prompt (that is the dialog watcher's job)
#   4. its title is in tidy.json "close" (exact, case-insensitive)
param(
    [switch]$Once,
    [switch]$Watch,
    [int]$IntervalSec = 3,
    [switch]$Minimize,
    [string]$Config = "",
    [switch]$DryRun,
    [string]$FakeWindows = ""
)
$ErrorActionPreference = "Stop"
if (-not $Config) { $Config = Join-Path $PSScriptRoot "tidy.json" }
$LockFile = Join-Path $PSScriptRoot "tidy.lock"
$LogFile = Join-Path $PSScriptRoot "tidy.log"
$DialogTitle = '(?i)save|delete|import|warning|error|confirm|restore|package|message(?!\s+log)'

function Out-Json($o) { $o | ConvertTo-Json -Depth 5 -Compress }

function Get-CloseList {
    $raw = Get-Content -Path $Config -Raw -Encoding UTF8 | ConvertFrom-Json
    return @($raw.close | ForEach-Object { $_.ToLowerInvariant() })
}

# Pure decision for one window: returns $null to act, or the reason it is kept.
function Get-KeepReason($w, $list) {
    if ($w.process -ine "UnrealEditor") { return "other process" }
    if ([long]$w.owner -eq 0) { return "main/unowned window" }
    if ($w.title -match $DialogTitle) { return "looks like a dialog" }
    if ($list -notcontains $w.title.ToLowerInvariant()) { return "not in tidy.json" }
    return $null
}

function Split-Windows($wins, $list) {
    $act = @(); $kept = @()
    foreach ($w in $wins) {
        $why = Get-KeepReason $w $list
        if ($null -eq $why) { $act += $w } elseif ($w.process -ieq "UnrealEditor") { $kept += [pscustomobject]@{ title = $w.title; why = $why } }
    }
    return @{ act = $act; kept = $kept }
}

if ($DryRun) {
    try {
        $list = Get-CloseList
        $wins = @(Get-Content -Path $FakeWindows -Raw -Encoding UTF8 | ConvertFrom-Json)
        $s = Split-Windows $wins $list
        Out-Json @{ ok = $true; dry_run = $true; action = $(if ($Minimize) { "minimize" } else { "close" });
                    closed = @($s.act | ForEach-Object { $_.title }); kept = @($s.kept) }
        exit 0
    } catch { Out-Json @{ ok = $false; dry_run = $true; message = $_.Exception.Message }; exit 1 }
}

if (-not ("StudioTidyWin" -as [type])) {
Add-Type @"
using System;
using System.Collections.Generic;
using System.Runtime.InteropServices;
using System.Text;
public static class StudioTidyWin {
    public delegate bool EnumProc(IntPtr h, IntPtr l);
    [DllImport("user32.dll")] static extern bool EnumWindows(EnumProc cb, IntPtr l);
    [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr h);
    [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr h, out uint pid);
    [DllImport("user32.dll")] public static extern IntPtr GetWindow(IntPtr h, uint cmd);
    [DllImport("user32.dll", CharSet = CharSet.Unicode)] static extern int GetWindowText(IntPtr h, StringBuilder s, int n);
    [DllImport("user32.dll")] public static extern bool PostMessage(IntPtr h, uint msg, IntPtr w, IntPtr l);
    [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr h, int cmd);
    // Collected in C# and returned as a List: PowerShell scriptblock callbacks lose their output.
    public static List<IntPtr> Visible() {
        var list = new List<IntPtr>();
        EnumWindows((h, l) => { if (IsWindowVisible(h)) list.Add(h); return true; }, IntPtr.Zero);
        return list;
    }
    public static string Text(IntPtr h) { var s = new StringBuilder(512); GetWindowText(h, s, 512); return s.ToString(); }
    public static uint Pid(IntPtr h) { uint p; GetWindowThreadProcessId(h, out p); return p; }
    public static long Owner(IntPtr h) { return GetWindow(h, 4).ToInt64(); }   // GW_OWNER
}
"@
}

function Get-EditorWindows {
    $pids = @{}
    foreach ($p in Get-Process UnrealEditor -ErrorAction SilentlyContinue) { $pids[[uint32]$p.Id] = $true }
    if ($pids.Count -eq 0) { return @() }
    $out = @()
    foreach ($h in [StudioTidyWin]::Visible()) {
        if (-not $pids.ContainsKey([StudioTidyWin]::Pid($h))) { continue }
        $t = [StudioTidyWin]::Text($h)
        if (-not $t) { continue }
        $out += [pscustomobject]@{ hwnd = $h; title = $t; process = "UnrealEditor"; owner = [StudioTidyWin]::Owner($h) }
    }
    return $out
}

function Invoke-Tidy($list) {
    $s = Split-Windows @(Get-EditorWindows) $list
    $done = @()
    foreach ($w in $s.act) {
        if ($Minimize) { [void][StudioTidyWin]::ShowWindow($w.hwnd, 6) } else { [void][StudioTidyWin]::PostMessage($w.hwnd, 0x0010, [IntPtr]::Zero, [IntPtr]::Zero) }
        $done += $w.title
    }
    return @{ closed = $done; kept = @($s.kept) }
}

function Test-LockHeld {
    if (-not (Test-Path $LockFile)) { return $false }
    $old = (Get-Content $LockFile -ErrorAction SilentlyContinue | Select-Object -First 1)
    if ($old -match '^\d+$' -and [int]$old -ne $PID) {
        $p = Get-Process -Id ([int]$old) -ErrorAction SilentlyContinue
        if ($p -and $p.ProcessName -match '^(powershell|pwsh)$') { return $true }
    }
    return $false
}

try {
    $list = Get-CloseList
    if (-not $Watch) {
        $r = Invoke-Tidy $list
        Out-Json @{ ok = $true; action = $(if ($Minimize) { "minimize" } else { "close" }); closed = @($r.closed); kept = @($r.kept) }
        exit 0
    }
    if (Test-LockHeld) { Out-Json @{ ok = $false; message = "watcher already running (pid $(Get-Content $LockFile))" }; exit 1 }
    Set-Content -Path $LockFile -Value $PID -Encoding ASCII
    Add-Content -Path $LogFile -Value ("{0} start pid={1} interval={2}s" -f (Get-Date).ToString("s"), $PID, $IntervalSec)
    try {
        while ($true) {
            try {
                $r = Invoke-Tidy $list
                foreach ($t in $r.closed) {
                    Add-Content -Path $LogFile -Value ("{0} {1} '{2}'" -f (Get-Date).ToString("s"), $(if ($Minimize) { "minimized" } else { "closed" }), $t)
                }
            } catch { Add-Content -Path $LogFile -Value ("{0} error {1}" -f (Get-Date).ToString("s"), $_.Exception.Message) }
            Start-Sleep -Seconds ([Math]::Max(1, $IntervalSec))
        }
    } finally {
        if ((Get-Content $LockFile -ErrorAction SilentlyContinue | Select-Object -First 1) -eq "$PID") { Remove-Item $LockFile -Force -ErrorAction SilentlyContinue }
    }
} catch {
    Out-Json @{ ok = $false; message = $_.Exception.Message }
    exit 1
}

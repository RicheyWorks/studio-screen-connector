# SPDX-License-Identifier: MIT
# studio-screen-connector / audit.ps1  (Windows PowerShell 5.1+; -FakeWindows also runs under pwsh on Linux)
# Turn audit: is the Unreal editor safe to hand to the next team? Read-only; it never closes, saves or stops anything.
#   .\audit.ps1                         one JSON line {"ok":..,"checks":[{check,ok,detail}]}; exit 0 clean, 1 not
#   -BridgeHost/-BridgePort             the Unreal bridge (default 127.0.0.1:9877)
#   -FakeWindows f.json                 decide floating panels against a fixture (hwnd,title,process,owner); no Win32
#   -SkipSaved                          skip the dirty-package query (it runs read-only Python through execute_python)
# Checks:
#   1. play stopped   bridge "ping" (READ) answers and result.playing is false
#   2. saved          no dirty map or content packages (EditorLoadingAndSavingUtils.get_dirty_*_packages, read-only)
#   3. no floating panels  no owned UnrealEditor top-level window (tool tabs) left open
#   4. no open dialogs     no owned UnrealEditor window that looks like a dialog (Save, Restore, Message, ...)
# A bridge that doesn't answer fails checks 1 and 2: an unverified turn is not a clean turn.
param(
    [string]$BridgeHost = "127.0.0.1",
    [int]$BridgePort = 9877,
    [int]$ConnectMs = 1500,
    [int]$ReadMs = 10000,
    [string]$FakeWindows = "",
    [switch]$SkipSaved
)
$ErrorActionPreference = "Stop"
$DialogTitle = '(?i)save|delete|import|warning|error|confirm|restore|package|message(?!\s+log)'
$DirtyCode = "import unreal`nu = unreal.EditorLoadingAndSavingUtils`nresult = sorted(set(p.get_name() for p in list(u.get_dirty_map_packages()) + list(u.get_dirty_content_packages())))`n"

function Out-Json($o) { $o | ConvertTo-Json -Depth 6 -Compress }

function Send-Bridge([string]$type, $params) {
    $client = New-Object System.Net.Sockets.TcpClient
    try {
        $task = $client.ConnectAsync($BridgeHost, $BridgePort)
        if (-not $task.Wait($ConnectMs) -or -not $client.Connected) { return $null }
        $client.ReceiveTimeout = $ReadMs; $client.SendTimeout = 2000
        $stream = $client.GetStream()
        $req = (@{ id = "audit-" + [guid]::NewGuid().ToString("N").Substring(0, 8); type = $type; params = $params } | ConvertTo-Json -Compress -Depth 4) + "`n"
        $bytes = [Text.Encoding]::UTF8.GetBytes($req)
        $stream.Write($bytes, 0, $bytes.Length)
        $buf = New-Object byte[] 65536; $sb = New-Object System.Text.StringBuilder
        while ($true) {
            $n = $stream.Read($buf, 0, $buf.Length)
            if ($n -le 0) { break }
            [void]$sb.Append([Text.Encoding]::UTF8.GetString($buf, 0, $n))
            if ($sb.ToString().Contains("`n")) { break }
        }
        $line = $sb.ToString().Split("`n")[0]
        if (-not $line) { return $null }
        return ($line | ConvertFrom-Json)
    } catch { return $null } finally { $client.Close() }
}

function Get-Windows {
    if ($FakeWindows) { return @(Get-Content -Path $FakeWindows -Raw -Encoding UTF8 | ConvertFrom-Json) }
    if (-not ("StudioAuditWin" -as [type])) {
Add-Type @"
using System;
using System.Collections.Generic;
using System.Runtime.InteropServices;
using System.Text;
public static class StudioAuditWin {
    public delegate bool EnumProc(IntPtr h, IntPtr l);
    [DllImport("user32.dll")] static extern bool EnumWindows(EnumProc cb, IntPtr l);
    [DllImport("user32.dll")] static extern bool IsWindowVisible(IntPtr h);
    [DllImport("user32.dll")] static extern uint GetWindowThreadProcessId(IntPtr h, out uint pid);
    [DllImport("user32.dll")] static extern IntPtr GetWindow(IntPtr h, uint cmd);
    [DllImport("user32.dll", CharSet = CharSet.Unicode)] static extern int GetWindowText(IntPtr h, StringBuilder s, int n);
    public static List<IntPtr> Visible() {
        var list = new List<IntPtr>();
        EnumWindows((h, l) => { if (IsWindowVisible(h)) list.Add(h); return true; }, IntPtr.Zero);
        return list;
    }
    public static string Text(IntPtr h) { var s = new StringBuilder(512); GetWindowText(h, s, 512); return s.ToString(); }
    public static uint Pid(IntPtr h) { uint p; GetWindowThreadProcessId(h, out p); return p; }
    public static long Owner(IntPtr h) { return GetWindow(h, 4).ToInt64(); }
}
"@
    }
    $pids = @{}
    foreach ($p in Get-Process UnrealEditor -ErrorAction SilentlyContinue) { $pids[[uint32]$p.Id] = $true }
    $out = @()
    if ($pids.Count -eq 0) { return $out }
    foreach ($h in [StudioAuditWin]::Visible()) {
        if (-not $pids.ContainsKey([StudioAuditWin]::Pid($h))) { continue }
        $t = [StudioAuditWin]::Text($h)
        if (-not $t) { continue }
        $out += [pscustomobject]@{ hwnd = [long]$h; title = $t; process = "UnrealEditor"; owner = [StudioAuditWin]::Owner($h) }
    }
    return $out
}

$checks = @()
function Add-Check([string]$name, [bool]$ok, $detail) { $script:checks += [pscustomobject]@{ check = $name; ok = $ok; detail = $detail } }

try {
    $ping = Send-Bridge "ping" @{}
    if ($null -eq $ping -or $ping.status -ne "ok" -or $null -eq $ping.result -or $null -eq $ping.result.playing) {
        Add-Check "play stopped" $false ("bridge {0}:{1} did not answer ping with result.playing" -f $BridgeHost, $BridgePort)
    } else {
        Add-Check "play stopped" (-not [bool]$ping.result.playing) ("playing=" + ([string]$ping.result.playing).ToLower())
    }

    if ($SkipSaved) {
        Add-Check "saved" $true "skipped (-SkipSaved)"
    } else {
        $dirty = Send-Bridge "execute_python" @{ code = $DirtyCode }
        if ($null -eq $dirty -or $dirty.status -ne "ok" -or $null -eq $dirty.result) {
            $why = if ($dirty -and $dirty.message) { [string]$dirty.message } else { "no answer" }
            Add-Check "saved" $false ("could not read dirty packages: " + $why)
        } else {
            $names = @($dirty.result.result | Where-Object { $_ })
            Add-Check "saved" ($names.Count -eq 0) $(if ($names.Count) { "unsaved: " + (($names | Select-Object -First 10) -join ", ") } else { "no dirty packages" })
        }
    }

    $owned = @(Get-Windows | Where-Object { $_.process -ieq "UnrealEditor" -and [long]$_.owner -ne 0 })
    $dialogs = @($owned | Where-Object { $_.title -match $DialogTitle } | ForEach-Object { $_.title })
    $panels = @($owned | Where-Object { $_.title -notmatch $DialogTitle } | ForEach-Object { $_.title })
    Add-Check "no floating panels" ($panels.Count -eq 0) $(if ($panels.Count) { "open: " + ($panels -join ", ") + " (run tidy.ps1 -Once)" } else { "none" })
    Add-Check "no open dialogs" ($dialogs.Count -eq 0) $(if ($dialogs.Count) { "open: " + ($dialogs -join ", ") } else { "none" })

    $ok = @($checks | Where-Object { -not $_.ok }).Count -eq 0
    Out-Json @{ ok = $ok; checks = @($checks) }
    if ($ok) { exit 0 } else { exit 1 }
} catch {
    Out-Json @{ ok = $false; checks = @($checks); message = $_.Exception.Message }
    exit 1
}

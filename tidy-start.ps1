# SPDX-License-Identifier: MIT
# Start, stop or check the hidden tidy watcher (tidy.ps1 -Watch).
#   .\tidy-start.ps1 [-IntervalSec 3] [-Minimize]   start it hidden via WMI (outlives the calling shell / job object)
#   .\tidy-start.ps1 -Status                         is it running? last log lines
#   .\tidy-start.ps1 -Stop                           stop it
param([switch]$Stop, [switch]$Status, [int]$IntervalSec = 3, [switch]$Minimize)
$ErrorActionPreference = "Stop"
$tidy = Join-Path $PSScriptRoot "tidy.ps1"
$lock = Join-Path $PSScriptRoot "tidy.lock"
$log = Join-Path $PSScriptRoot "tidy.log"

function Get-Watcher {
    if (-not (Test-Path $lock)) { return $null }
    $id = Get-Content $lock -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($id -notmatch '^\d+$') { return $null }
    $p = Get-Process -Id ([int]$id) -ErrorAction SilentlyContinue
    if ($p -and $p.ProcessName -match '^(powershell|pwsh)$') { return $p }
    return $null
}
function Out-Json($o) { $o | ConvertTo-Json -Depth 4 -Compress }

$w = Get-Watcher
if ($Status) {
    $tail = @(); if (Test-Path $log) { $tail = @(Get-Content $log -Tail 5) }
    Out-Json @{ ok = $true; running = [bool]$w; pid = $(if ($w) { $w.Id } else { $null }); log_tail = $tail }; exit 0
}
if ($Stop) {
    if (-not $w) { Remove-Item $lock -Force -ErrorAction SilentlyContinue; Out-Json @{ ok = $true; stopped = $false; note = "not running" }; exit 0 }
    Stop-Process -Id $w.Id -Force
    Remove-Item $lock -Force -ErrorAction SilentlyContinue
    Add-Content -Path $log -Value ("{0} stop pid={1}" -f (Get-Date).ToString("s"), $w.Id)
    Out-Json @{ ok = $true; stopped = $true; pid = $w.Id }; exit 0
}
if ($w) { Out-Json @{ ok = $true; started = $false; pid = $w.Id; note = "already running" }; exit 0 }
$cmd = "powershell.exe -WindowStyle Hidden -NoProfile -File `"$tidy`" -Watch -IntervalSec $IntervalSec"
if ($Minimize) { $cmd += " -Minimize" }
$startup = New-CimInstance -ClassName Win32_ProcessStartup -ClientOnly -Property @{ ShowWindow = [uint16]0 }
$r = Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{
    CommandLine = $cmd; CurrentDirectory = $PSScriptRoot; ProcessStartupInformation = $startup }
if ($r.ReturnValue -ne 0) { Out-Json @{ ok = $false; message = "Win32_Process.Create returned $($r.ReturnValue)" }; exit 1 }
Out-Json @{ ok = $true; started = $true; pid = $r.ProcessId; command = $cmd }

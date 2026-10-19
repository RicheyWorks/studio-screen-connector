# SPDX-License-Identifier: MIT
# studio-screen-connector / status-loop.ps1  (Windows PowerShell 5.1+)
# Keeps bot-team\BOARD\live\index.html fresh: status-capture.ps1 (window screenshots) then statuspage.py, every -IntervalSec.
# Not a broker: it only reads files and windows and writes <LiveDir>. One loop at a time (loop.lock holds its pid).
#   .\status-loop.ps1 -Once                 one refresh and exit
#   .\status-loop.ps1                       refresh every 60 s until stopped (Ctrl+C or: .\status-loop.ps1 -Stop)
#   .\status-loop.ps1 -BlenderBridge        also read a minimized Blender through its bridge; -UnrealBridge / -AllowBridge: see status-capture.ps1
param(
    [int]$IntervalSec = 60,
    [switch]$Once,
    [switch]$Stop,
    [switch]$AllowBridge,
    [switch]$BlenderBridge,
    [switch]$UnrealBridge,
    [string]$LiveDir = "",
    [string]$Python = "python"
)
$ErrorActionPreference = "Stop"
if (-not $LiveDir) { $LiveDir = Join-Path $env:USERPROFILE "projects\bot-team\BOARD\live" }
New-Item -ItemType Directory -Path $LiveDir -Force | Out-Null
$lock = Join-Path $LiveDir "loop.lock"
function Test-Loop { if (-not (Test-Path -LiteralPath $lock)) { return $false }; $p = (Get-Content -LiteralPath $lock -ErrorAction SilentlyContinue | Select-Object -First 1) -as [int]; return [bool]($p -and (Get-Process -Id $p -ErrorAction SilentlyContinue)) }
if ($Stop) {
    if (Test-Loop) { $p = [int](Get-Content -LiteralPath $lock | Select-Object -First 1); Stop-Process -Id $p -Force; Remove-Item $lock -Force; Write-Output "stopped $p" } else { Write-Output "not running" }
    return
}
if (-not $Once) {
    if (Test-Loop) { Write-Output ("already running: pid " + (Get-Content -LiteralPath $lock | Select-Object -First 1)); return }
    Set-Content -LiteralPath $lock -Value $PID
}
function Invoke-Cycle {
    $cap = @{ LiveDir = $LiveDir }; if ($AllowBridge) { $cap["AllowBridge"] = $true }; if ($BlenderBridge) { $cap["BlenderBridge"] = $true }; if ($UnrealBridge) { $cap["UnrealBridge"] = $true }
    try { & (Join-Path $PSScriptRoot "status-capture.ps1") @cap | Out-Null } catch { Write-Output ("capture error: " + $_.Exception.Message) }
    & $Python (Join-Path $PSScriptRoot "statuspage.py") --live $LiveDir
}
try {
    do { Invoke-Cycle; if (-not $Once) { Start-Sleep -Seconds $IntervalSec } } while (-not $Once)
} finally { if (-not $Once -and (Test-Path -LiteralPath $lock)) { Remove-Item -LiteralPath $lock -Force -ErrorAction SilentlyContinue } }





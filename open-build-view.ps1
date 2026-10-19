# SPDX-License-Identifier: MIT
# studio-screen-connector / open-build-view.ps1  (Windows PowerShell 5.1+)
# For the studio owner: make sure the build-view page is fresh and open it in the default browser.
#   .\open-build-view.ps1            refresh once, start the hidden 60 s refresher if it is not running, open index.html
#   .\open-build-view.ps1 -NoOpen    same without opening the browser
#   -NoBlenderBridge                 window-only for Blender; -UnrealBridge reads a minimized editor through its bridge (editor turn only)
param([string]$LiveDir = "", [switch]$NoOpen, [switch]$NoBlenderBridge, [switch]$UnrealBridge)
$ErrorActionPreference = "Stop"
if (-not $LiveDir) { $LiveDir = Join-Path $env:USERPROFILE "projects\bot-team\BOARD\live" }
$loop = Join-Path $PSScriptRoot "status-loop.ps1"
$extra = @{ LiveDir = $LiveDir }; if (-not $NoBlenderBridge) { $extra["BlenderBridge"] = $true }; if ($UnrealBridge) { $extra["UnrealBridge"] = $true }
& $loop -Once @extra | Out-Null
$lock = Join-Path $LiveDir "loop.lock"
$running = $false
if (Test-Path -LiteralPath $lock) { $p = (Get-Content -LiteralPath $lock | Select-Object -First 1) -as [int]; $running = [bool]($p -and (Get-Process -Id $p -ErrorAction SilentlyContinue)) }
if (-not $running) {
    $a = @("-NoProfile", "-WindowStyle", "Hidden", "-File", "`"$loop`"", "-LiveDir", "`"$LiveDir`"")
    if (-not $NoBlenderBridge) { $a += "-BlenderBridge" }; if ($UnrealBridge) { $a += "-UnrealBridge" }
    Start-Process -FilePath "powershell.exe" -ArgumentList $a -WindowStyle Hidden | Out-Null
}
$page = Join-Path $LiveDir "index.html"
Write-Output ("build view: " + $page)
if (-not $NoOpen) { Start-Process $page }





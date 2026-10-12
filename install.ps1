# SPDX-License-Identifier: MIT
# Installs or updates studio-screen-connector on Windows (PowerShell 5.1+). Safe to re-run.
#   - clones (or fast-forward pulls) into C:\RicheyWorks\studio-screen-connector
#   - creates allowlist.json from allowlist.example.json if missing (never overwrites your edits)
#   - creates C:\RicheyWorks\screens
param(
    [string]$Root = "C:\RicheyWorks",
    [string]$Repo = "https://github.com/RicheyWorks/studio-screen-connector.git"
)
$ErrorActionPreference = "Stop"
$dir = Join-Path $Root "studio-screen-connector"
$screens = Join-Path $Root "screens"
if (-not (Get-Command git -ErrorAction SilentlyContinue)) { throw "git is not on PATH" }
if (-not (Test-Path $Root)) { New-Item -ItemType Directory -Path $Root -Force | Out-Null }
if (Test-Path (Join-Path $dir ".git")) {
    git -C $dir pull --ff-only
    if ($LASTEXITCODE -ne 0) { throw "git pull failed in $dir (local changes? run: git -C $dir status)" }
} else {
    git clone $Repo $dir
    if ($LASTEXITCODE -ne 0) { throw "git clone failed" }
}
$allow = Join-Path $dir "allowlist.json"
if (-not (Test-Path $allow)) { Copy-Item (Join-Path $dir "allowlist.example.json") $allow; $made = $true } else { $made = $false }
if (-not (Test-Path $screens)) { New-Item -ItemType Directory -Path $screens -Force | Out-Null }
@{ ok = $true; dir = $dir; screens = $screens; allowlist = $allow; allowlist_created = $made;
   head = (git -C $dir rev-parse HEAD) } | ConvertTo-Json -Compress

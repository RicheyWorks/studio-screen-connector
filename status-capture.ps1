# SPDX-License-Identifier: MIT
# studio-screen-connector / status-capture.ps1  (Windows PowerShell 5.1+; also runs under pwsh for the tests)
# One refresh of the live screenshots the build-view page shows: <LiveDir>\<target>.png + capture-state.json.
# Reuses capture.ps1 (same allowlist, denylist, privacy log). Read-only: no input is sent, nothing is uploaded, no window is
# shown, restored or focused, and nothing is saved in any app.
#   -Targets blender,unreal    which allowlisted targets to refresh (default blender,unreal)
#   -BlenderBridge             when the Blender window is minimized/hidden, read the 3D viewport through bridge 9876 (viewport_screenshot)
#   -UnrealBridge              when the editor window is minimized/hidden, use capture.ps1 -Method auto (transient SceneCapture2D in the
#                              live editor). Needs an editor turn / codex-play-guard OK, so it is OFF unless asked for.
#   -AllowBridge               both of the above. Details:
#                                unreal  -> capture.ps1 -Method auto (transient SceneCapture2D in the live editor; needs an
#                                           editor turn / codex-play-guard OK, so it is OFF by default)
#                                blender -> bridge 9876 viewport_screenshot
#                              Without it a minimized/hidden window is reported as such and the previous image is kept (marked stale).
#   -CaptureScript PATH        capture.ps1 to call (tests pass a stub)
param(
    [string[]]$Targets = @("blender", "unreal"),
    [string]$LiveDir = "",
    [switch]$AllowBridge,
    [switch]$BlenderBridge,
    [switch]$UnrealBridge,
    [string]$CaptureScript = "",
    [string]$BlenderHost = "127.0.0.1",
    [int]$BlenderPort = 9876
)
$ErrorActionPreference = "Stop"
if ($AllowBridge) { $BlenderBridge = $true; $UnrealBridge = $true }
if (-not $LiveDir) { $LiveDir = Join-Path $env:USERPROFILE "projects\bot-team\BOARD\live" }
if (-not $CaptureScript) { $CaptureScript = Join-Path $PSScriptRoot "capture.ps1" }
New-Item -ItemType Directory -Path $LiveDir -Force | Out-Null
$tmp = Join-Path ([IO.Path]::GetTempPath()) ("ssc-status-" + [guid]::NewGuid().ToString("N").Substring(0, 8))
New-Item -ItemType Directory -Path $tmp -Force | Out-Null
$statePath = Join-Path $LiveDir "capture-state.json"
$state = @{}
if (Test-Path -LiteralPath $statePath) {
    try { $o = Get-Content -LiteralPath $statePath -Raw | ConvertFrom-Json; foreach ($p in $o.PSObject.Properties) { $state[$p.Name] = $p.Value } } catch { }
}

function Get-LastJson($lines) {
    $j = @($lines | ForEach-Object { [string]$_ } | Where-Object { $_.Trim().StartsWith("{") }) | Select-Object -Last 1
    if (-not $j) { return $null }
    try { return $j | ConvertFrom-Json } catch { return $null }
}

function Get-BlenderBridgeShot([string]$dest) {
    $c = New-Object System.Net.Sockets.TcpClient
    try {
        $t = $c.ConnectAsync($BlenderHost, $BlenderPort)
        if (-not $t.Wait(2000) -or -not $c.Connected) { return "Blender bridge $BlenderPort did not answer" }
        $c.ReceiveTimeout = 30000
        $s = $c.GetStream()
        $req = (@{ id = "status-shot"; type = "viewport_screenshot"; params = @{ max_size = 1600 } } | ConvertTo-Json -Compress -Depth 4) + "`n"
        $b = [Text.Encoding]::UTF8.GetBytes($req); $s.Write($b, 0, $b.Length)
        $buf = New-Object byte[] 262144; $sb = New-Object Text.StringBuilder
        while ($true) { $n = $s.Read($buf, 0, $buf.Length); if ($n -le 0) { break }; [void]$sb.Append([Text.Encoding]::UTF8.GetString($buf, 0, $n)); if ($sb.ToString().Contains("`n")) { break } }
        $r = $sb.ToString().Split("`n")[0] | ConvertFrom-Json
        if ($r.status -ne "ok") { return "Blender bridge: " + [string]$r.message }
        $b64 = if ($r.result.image_png_base64) { $r.result.image_png_base64 } else { $r.image_png_base64 }
        if (-not $b64) { return "Blender bridge returned no image" }
        [IO.File]::WriteAllBytes($dest, [Convert]::FromBase64String($b64))
        return $null
    } catch { return "Blender bridge error: " + $_.Exception.Message } finally { $c.Close() }
}

$results = [ordered]@{}
foreach ($t in $Targets) {
    $now = (Get-Date).ToString("yyyy-MM-ddTHH:mm:ss")
    $entry = [ordered]@{ ok = $false; ts = $now; method = ""; title = ""; message = "" }
    try {
        $args2 = @{ Target = $t; OutDir = $tmp }
        if ($t -eq "unreal") { $args2["Method"] = $(if ($UnrealBridge) { "auto" } else { "window" }) }
        $res = Get-LastJson (& $CaptureScript @args2 2>&1)
        $src = $null
        if ($res -and $res.ok -and $res.path -and -not $res.blank -and (Test-Path -LiteralPath $res.path)) {
            $src = $res.path; $entry.method = [string]$res.method; $entry.title = [string]$res.title
        } else {
            $entry.message = if ($res -and $res.message) { [string]$res.message } elseif ($res -and $res.blank) { "window rendered blank" } else { "capture returned nothing" }
            if ($t -eq "blender" -and $BlenderBridge) {
                $dest = Join-Path $tmp "blender-bridge.png"
                $err = Get-BlenderBridgeShot $dest
                if (-not $err) { $src = $dest; $entry.method = "blender-bridge"; $entry.message = "" } else { $entry.message += "; " + $err }
            } elseif ($t -eq "blender") {
                $entry.message += " (window-only mode; -BlenderBridge reads the viewport through the Blender bridge)"
            } elseif ($t -eq "unreal" -and -not $UnrealBridge) {
                $entry.message += " (window-only mode; -UnrealBridge, during a claimed editor turn, reads it through the editor bridge)"
            }
        }
        if ($src) {
            Copy-Item -LiteralPath $src -Destination (Join-Path $LiveDir ($t + ".png")) -Force
            $entry.ok = $true
        }
    } catch { $entry.message = $_.Exception.Message }
    $state[$t] = $entry; $results[$t] = $entry
}
($state | ConvertTo-Json -Depth 4) | Set-Content -LiteralPath $statePath -Encoding UTF8
Remove-Item -LiteralPath $tmp -Recurse -Force -ErrorAction SilentlyContinue
Write-Output (@{ ok = $true; live = $LiveDir; results = $results } | ConvertTo-Json -Depth 5 -Compress)






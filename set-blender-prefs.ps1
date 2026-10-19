# SPDX-License-Identifier: MIT
# studio-screen-connector / set-blender-prefs.ps1  (Windows PowerShell 5.1+)
# Turns the Blender start splash off. Idempotent; safe to call from any Blender launcher.
#   .\set-blender-prefs.ps1                 background Blender loads user prefs, sets show_splash=False, saves userpref.blend
#   .\set-blender-prefs.ps1 -Live           ALSO sets it in the running Blender via the bridge (127.0.0.1:9876) and saves that session's prefs
#   .\set-blender-prefs.ps1 -DryRun         print what would run, change nothing
#   .\set-blender-prefs.ps1 -ConfigDir D:\tmp\cfg      use a separate BLENDER_USER_CONFIG (test run; never touches the real prefs, runs even if Blender is open)
# Skips the background run (and says so) while a Blender is already running, so a live session's prefs are never clobbered
# by a second process; use -Live for that case. Never uses --factory-startup (that would save default prefs over yours).
param(
    [string]$BlenderExe = "C:\Program Files\Blender Foundation\Blender 4.3\blender.exe",
    [string]$Script = "",
    [switch]$Live,
    [string]$ConfigDir = "",
    [string]$BridgeHost = "127.0.0.1",
    [int]$BridgePort = 9876,
    [switch]$DryRun,
    [int]$TimeoutSec = 90
)
$ErrorActionPreference = "Stop"
if (-not $Script) { $Script = Join-Path $PSScriptRoot "blender_prefs.py" }
$out = [ordered]@{ ok = $true; background = "skipped"; live = "skipped" }

function Send-BlenderBridge([string]$code) {
    $c = New-Object System.Net.Sockets.TcpClient
    try {
        $t = $c.ConnectAsync($BridgeHost, $BridgePort)
        if (-not $t.Wait(2000) -or -not $c.Connected) { return $null }
        $c.ReceiveTimeout = 15000
        $s = $c.GetStream()
        $req = (@{ id = "prefs"; type = "execute_code"; params = @{ code = $code } } | ConvertTo-Json -Compress -Depth 4) + "`n"
        $b = [Text.Encoding]::UTF8.GetBytes($req); $s.Write($b, 0, $b.Length)
        $buf = New-Object byte[] 8192; $sb = New-Object Text.StringBuilder
        while ($true) { $n = $s.Read($buf, 0, $buf.Length); if ($n -le 0) { break }; [void]$sb.Append([Text.Encoding]::UTF8.GetString($buf, 0, $n)); if ($sb.ToString().Contains("`n")) { break } }
        return ($sb.ToString().Split("`n")[0] | ConvertFrom-Json)
    } catch { return $null } finally { $c.Close() }
}

$running = @(Get-Process blender -ErrorAction SilentlyContinue)
if ($DryRun) {
    $out.background = if ($running.Count) { "would skip: Blender already running (pid " + ($running.Id -join ",") + ")" } else { "would run: `"$BlenderExe`" -b --python `"$Script`"" }
    $out.live = if ($Live) { "would set show_splash=False via bridge ${BridgeHost}:$BridgePort" } else { "skipped" }
    Write-Output ($out | ConvertTo-Json -Compress); return
}
if (-not $running.Count -or $ConfigDir) {
    if (-not (Test-Path -LiteralPath $BlenderExe)) { throw "Blender not found at $BlenderExe" }
    $log = Join-Path $env:TEMP "blender-prefs-$PID.log"
    if ($ConfigDir) { New-Item -ItemType Directory -Path $ConfigDir -Force | Out-Null; $env:BLENDER_USER_CONFIG = $ConfigDir }
    $p = Start-Process -FilePath $BlenderExe -ArgumentList @("-b", "--python", "`"$Script`"") -NoNewWindow -PassThru -RedirectStandardOutput $log -RedirectStandardError "$log.err"
    if (-not $p.WaitForExit($TimeoutSec * 1000)) { try { $p.Kill() } catch { }; throw "background Blender timed out after ${TimeoutSec}s" }
    $line = (Get-Content -LiteralPath $log -ErrorAction SilentlyContinue | Where-Object { $_ -like "BLENDER_PREFS *" } | Select-Object -Last 1)
    if (-not $line) { throw "blender_prefs.py printed nothing; see $log" }
    $out.background = ($line.Substring(14) | ConvertFrom-Json)
    if (-not $out.background.saved) { $out.ok = $false }
    Remove-Item $log, "$log.err" -ErrorAction SilentlyContinue
} else {
    $out.background = "skipped: Blender already running (pid " + ($running.Id -join ",") + ")"
}
if ($Live) {
    $r = Send-BlenderBridge "import bpy`nbpy.context.preferences.view.show_splash = False`ntry:`n    bpy.ops.wm.save_userpref()`nexcept Exception:`n    pass`nresult = bool(bpy.context.preferences.view.show_splash)"
    $out.live = if ($null -eq $r) { "bridge did not answer" } elseif ($r.status -eq "ok") { "show_splash=" + [string]$r.result.result } else { "bridge error: " + [string]$r.message }
}
Write-Output ($out | ConvertTo-Json -Compress)



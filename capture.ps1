# SPDX-License-Identifier: MIT
# studio-screen-connector / capture.ps1  (Windows PowerShell 5.1+; dry-run also works under pwsh on Linux)
# Read-only capture of allowlisted windows.
# Based on RicheyWorks/claude-unreal-connector extension/server/win.ps1 (MIT): same EnumWindows + PrintWindow
# (PW_RENDERFULLCONTENT) approach; its fixed UnrealEditor process filter is generalized into allowlist.json + -Process.
#   -Op list                      allowlisted visible windows (no pixels) as JSON
#   -Op capture -Target claude    capture the largest window matching allowlist entry "claude" to <repo>\screens\*.png
#   -Op capture -Target screen    full primary screen (only if allowlist "screen".enabled = true)
#   -Op capture -Hwnd N           capture a specific window, which must still match the allowlist
#   -Op shot                      alias of capture (win.ps1 naming)
#   -Process claude,blender       optional extra filter: only these allowlisted processes are eligible
#   -DryRun -FakeWindows f.json   run the selection + privacy guard against a fixture; no Win32, no pixels
#   -Method auto|window|bridge    unreal only. auto (default): PrintWindow, but if the editor window is minimized,
#                                 hidden or renders blank, fall back to the Unreal bridge. bridge: always use it.
#   Bridge fallback: execute_python on -BridgeHost:-BridgePort (127.0.0.1:9877) spawns a TRANSIENT SceneCapture2D at
#   the level-viewport camera, renders one frame into a transient RGBA8 render target, export_render_target -> PNG,
#   destroys the actor, and the script polls for the file. Never shows/restores a window, never launches an editor,
#   never saves; reports any package the capture newly dirtied (expected: none).
# Output: one JSON object on stdout. Never sends input; never uploads.
param(
    [ValidateSet("list", "capture", "shot")][string]$Op = "capture",
    [string[]]$Process = @(),
    [string]$Target = "",
    [long]$Hwnd = 0,
    [string]$OutDir = "",
    [string]$Allowlist = "",
    [int]$MaxEdge = 1600,
    [int]$RetainHours = 24,
    [switch]$AllowFront,
    [switch]$DryRun,
    [string]$FakeWindows = "",
    [ValidateSet("auto", "window", "bridge")][string]$Method = "auto",
    [string]$BridgeHost = "127.0.0.1",
    [int]$BridgePort = 9877,
    [int]$BridgeTimeoutMs = 30000
)
$ErrorActionPreference = "Stop"
if ($Op -eq "shot") { $Op = "capture" }
if (-not $OutDir) { $OutDir = Join-Path $PSScriptRoot "screens" }   # inside the user profile, so CopyToBox can read it
if (-not $Allowlist) { $Allowlist = Join-Path $PSScriptRoot "allowlist.json" }

$DenyTitle = '(?i)password|1password|bitwarden|keepass|lastpass|bank|coinbase|gmail|outlook|signal|whatsapp|authenticator'

function Out-Json($o) { $o | ConvertTo-Json -Depth 5 -Compress }


# ---- Unreal bridge capture: works while the editor is minimized/hidden; no window is touched ----
function Get-BridgeShotPy([string]$dir, [string]$stem, [int]$w, [int]$h) {
    return @"
import unreal, json
# studio-screen-connector capture
OUT_DIR = r"$dir"; STEM = "$stem"; W = $w; H = $h
ues = unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem)
world = ues.get_editor_world()
loc, rot = ues.get_level_viewport_camera_info()
before = set(p.get_name() for p in unreal.EditorLoadingAndSavingUtils.get_dirty_map_packages())
rt = unreal.RenderingLibrary.create_render_target2d(world, W, H, unreal.TextureRenderTargetFormat.RTF_RGBA8_SRGB)
actor = unreal.EditorLevelLibrary.spawn_actor_from_class(unreal.SceneCapture2D, loc, rot, transient=True)
err = None
try:
    comp = actor.capture_component2d
    comp.set_editor_property("texture_target", rt)
    comp.set_editor_property("capture_source", unreal.SceneCaptureSource.SCS_FINAL_COLOR_LDR)
    comp.set_editor_property("capture_every_frame", False)
    comp.set_editor_property("capture_on_movement", False)
    comp.capture_scene()
    unreal.RenderingLibrary.export_render_target(world, rt, OUT_DIR, STEM)
except Exception as e:
    err = str(e)
finally:
    actor.destroy_actor()
after = set(p.get_name() for p in unreal.EditorLoadingAndSavingUtils.get_dirty_map_packages())
print(json.dumps({"cam": [loc.x, loc.y, loc.z], "error": err, "newly_dirty": sorted(after - before)}))
"@
}

function Send-ShotBridge([string]$type, $params, [int]$readMs) {
    $client = New-Object System.Net.Sockets.TcpClient
    try {
        $task = $client.ConnectAsync($BridgeHost, $BridgePort)
        if (-not $task.Wait(2000) -or -not $client.Connected) { return $null }
        $client.ReceiveTimeout = $readMs; $client.SendTimeout = 2000
        $stream = $client.GetStream()
        $req = (@{ id = "capture-" + [guid]::NewGuid().ToString("N").Substring(0, 8); type = $type; params = $params } | ConvertTo-Json -Compress -Depth 4) + "`n"
        $bytes = [Text.Encoding]::UTF8.GetBytes($req); $stream.Write($bytes, 0, $bytes.Length)
        $buf = New-Object byte[] 65536; $sb = New-Object System.Text.StringBuilder
        while ($true) {
            $n = $stream.Read($buf, 0, $buf.Length); if ($n -le 0) { break }
            [void]$sb.Append([Text.Encoding]::UTF8.GetString($buf, 0, $n)); if ($sb.ToString().Contains("`n")) { break }
        }
        $line = $sb.ToString().Split("`n")[0]; if (-not $line) { return $null }
        return ($line | ConvertFrom-Json)
    } catch { return $null } finally { $client.Close() }
}

# Returns path/width/height or throws. Polls for the file: the export can land after the bridge replies.
function Invoke-BridgeCapture([string]$key, [string]$why) {
    if (-not (Test-Path $OutDir)) { New-Item -ItemType Directory -Path $OutDir -Force | Out-Null }
    $w = [Math]::Max(64, [Math]::Min($MaxEdge, 3840)); $h = [int][Math]::Round($w * 9 / 16)
    $stem = "{0}-bridge-{1}" -f ($key -replace '[^A-Za-z0-9_-]', '_'), (Get-Date).ToString("yyyyMMdd-HHmmss")
    $dir = (Resolve-Path $OutDir).Path
    $r = Send-ShotBridge "execute_python" @{ code = (Get-BridgeShotPy $dir $stem $w $h) } $BridgeTimeoutMs
    if ($null -eq $r) { throw ("Unreal bridge {0}:{1} did not answer ({2}); nothing captured, no window touched" -f $BridgeHost, $BridgePort, $why) }
    if ($r.status -ne "ok") { throw ("Unreal bridge refused the capture: " + [string]$r.message) }
    $info = $null
    try { $info = ([string]$r.result.stdout -split "`n" | Where-Object { $_.Trim().StartsWith("{") } | Select-Object -Last 1) | ConvertFrom-Json } catch { }
    if ($info -and $info.error) { throw ("bridge capture failed in the editor: " + $info.error) }
    $final = Join-Path $dir ($stem + ".png"); $found = $null
    $deadline = (Get-Date).AddMilliseconds($BridgeTimeoutMs)
    while (-not $found -and (Get-Date) -lt $deadline) {
        foreach ($c in @((Join-Path $dir $stem), $final)) { if ((Test-Path -LiteralPath $c) -and (Get-Item -LiteralPath $c).Length -gt 8) { $found = $c; break } }
        if (-not $found) { Start-Sleep -Milliseconds 250 }
    }
    if (-not $found) { throw ("bridge capture wrote no file for '{0}' within {1} ms" -f $stem, $BridgeTimeoutMs) }
    Start-Sleep -Milliseconds 200   # let the editor close the file
    $sig = [IO.File]::ReadAllBytes($found)
    if ($sig.Length -lt 8 -or $sig[0] -ne 0x89 -or $sig[1] -ne 0x50 -or $sig[2] -ne 0x4E -or $sig[3] -ne 0x47) {
        Remove-Item -LiteralPath $found -Force -ErrorAction SilentlyContinue
        throw "bridge capture file is not a PNG ($stem)"
    }
    if ($found -ne $final) { Move-Item -LiteralPath $found -Destination $final -Force }
    $o = @{ path = $final; width = $w; height = $h; reason = $why; camera = $null; newly_dirty = @() }
    if ($info) { $o.camera = $info.cam; $o.newly_dirty = @($info.newly_dirty) }
    return $o
}

function Out-BridgeShot($key, $b, [bool]$dry) {
    $o = @{ ok = $true; target = $key; method = "bridge"; reason = $b.reason; path = $b.path; width = $b.width; height = $b.height;
            camera = $b.camera; newly_dirty = $b.newly_dirty }
    if ($dry) { $o.dry_run = $true } else { $o.ms = $sw.ElapsedMilliseconds }
    Write-Audit @{ target = $key; ok = $true; method = "bridge"; reason = $b.reason; path = $b.path }
    Out-Json $o
}

function Write-Audit($entry) {
    if ($DryRun) { return }
    try {
        if (-not (Test-Path $OutDir)) { New-Item -ItemType Directory -Path $OutDir -Force | Out-Null }
        $entry.at = (Get-Date).ToString("o")
        Add-Content -Path (Join-Path $OutDir "capture-log.jsonl") -Value ($entry | ConvertTo-Json -Compress) -Encoding UTF8
    } catch { }
}

function Get-Allow {
    if (-not (Test-Path $Allowlist)) { throw "allowlist not found: $Allowlist (run install.ps1)" }
    $raw = Get-Content -Path $Allowlist -Raw -Encoding UTF8 | ConvertFrom-Json
    $map = @{}
    foreach ($p in $raw.PSObject.Properties) { $map[$p.Name] = $p.Value }
    return $map
}

function Test-Match($win, $rule) {
    if ($null -eq $rule) { return $false }
    if ($rule.enabled -eq $false) { return $false }
    if (-not $rule.process -and -not $rule.title) { return $false }
    if ($rule.process -and ($win.process -notmatch ("^(" + $rule.process + ")$"))) { return $false }
    if ($rule.title -and ($win.title -notmatch $rule.title)) { return $false }
    return $true
}

function Test-Proc($win) {
    if ($Process.Count -eq 0) { return $true }
    foreach ($p in $Process) { foreach ($q in ($p -split ',')) { if ($q -and $win.process -ieq $q.Trim()) { return $true } } }
    return $false
}

function Find-Rule($win, $allow) {
    if ($win.title -match $DenyTitle) { return $null }
    if (-not (Test-Proc $win)) { return $null }
    foreach ($k in $allow.Keys) { if ($k -ne "screen" -and (Test-Match $win $allow[$k])) { return $k } }
    return $null
}

# Pure decision: which window (or the screen) a request resolves to, or a refusal. Shared by live and dry runs.
function Resolve-Request($wins, $allow) {
    if ($Target -eq "screen") {
        $rule = $allow["screen"]
        if (-not $rule -or $rule.enabled -ne $true) { return @{ refused = "full-screen capture is disabled in the allowlist" } }
        $deny = @($wins | Where-Object { -not $_.minimized -and $_.title -match $DenyTitle })
        if ($deny.Count -gt 0) { return @{ refused = "a denylisted window is visible ($($deny[0].title))" } }
        return @{ key = "screen"; win = $null }
    }
    if ($Hwnd -ne 0) {
        $win = $wins | Where-Object { [long]$_.hwnd -eq $Hwnd } | Select-Object -First 1
        if (-not $win) { return @{ error = "window $Hwnd is not open (run -Op list)" } }
        $key = Find-Rule $win $allow
        if (-not $key) { return @{ refused = "window '$($win.title)' is not allowlisted" } }
    } else {
        if (-not $Target) { return @{ error = "pass -Target <allowlist key> or -Hwnd N" } }
        if (-not $allow.ContainsKey($Target)) { return @{ refused = "'$Target' is not in the allowlist" } }
        $key = $Target
        $win = $wins | Where-Object { $_.title -notmatch $DenyTitle -and (Test-Proc $_) -and (Test-Match $_ $allow[$Target]) } |
            Sort-Object { [long]$_.width * [long]$_.height } -Descending | Select-Object -First 1
        if (-not $win) {
            if ($key -eq "unreal" -and $Method -ne "window" -and (Test-Proc ([pscustomobject]@{ process = "UnrealEditor" }))) { return @{ key = $key; win = $null; bridge = "no visible editor window" } }
            return @{ error = "no open window matches '$Target'" }
        }
    }
    if ($key -eq "unreal" -and $Method -eq "bridge") { return @{ key = $key; win = $win; bridge = "requested" } }
    if ($win.minimized -and $key -eq "unreal" -and $Method -ne "window") { return @{ key = $key; win = $win; bridge = "window minimized" } }
    if ($win.minimized) { return @{ error = "window '$($win.title)' is minimized; PrintWindow returns nothing for minimized windows" } }
    return @{ key = $key; win = $win }
}

function Get-Listed($wins, $allow) {
    $out = @()
    foreach ($w in $wins) {
        $k = Find-Rule $w $allow
        if ($k) { $out += [pscustomobject]@{ hwnd = [long]$w.hwnd; title = $w.title; process = $w.process; target = $k;
                                            width = $w.width; height = $w.height; minimized = [bool]$w.minimized } }
    }
    return , $out
}

if ($DryRun) {
    try {
        $allow = Get-Allow
        $wins = @()
        if ($FakeWindows) { $wins = @(Get-Content -Path $FakeWindows -Raw -Encoding UTF8 | ConvertFrom-Json | ForEach-Object { $_ }) }   # unwrap: PS 5.1 emits the array as one object
        if ($Op -eq "list") { Out-Json @{ ok = $true; dry_run = $true; windows = (Get-Listed $wins $allow) }; exit 0 }
        $res = Resolve-Request $wins $allow
        if ($res.refused) { Out-Json @{ ok = $false; dry_run = $true; refused = $true; message = "refused: " + $res.refused }; exit 1 }
        if ($res.error) { Out-Json @{ ok = $false; dry_run = $true; refused = $false; message = $res.error }; exit 1 }
        if ($res.bridge) {   # dry run only talks to a bridge given explicitly (-BridgePort), never the live editor by default
            if (-not $PSBoundParameters.ContainsKey("BridgePort")) { Out-Json @{ ok = $true; dry_run = $true; target = $res.key; method = "bridge"; reason = $res.bridge }; exit 0 }
            Out-BridgeShot $res.key (Invoke-BridgeCapture $res.key $res.bridge) $true; exit 0
        }
        $o = @{ ok = $true; dry_run = $true; target = $res.key }
        if ($res.win) { $o.hwnd = [long]$res.win.hwnd; $o.title = $res.win.title }
        Out-Json $o; exit 0
    } catch { Out-Json @{ ok = $false; dry_run = $true; message = $_.Exception.Message }; exit 1 }
}

Add-Type -AssemblyName System.Drawing
Add-Type -AssemblyName System.Windows.Forms
if (-not ("StudioWin" -as [type])) {
Add-Type @"
using System;
using System.Collections.Generic;
using System.Runtime.InteropServices;
using System.Text;
public static class StudioWin {
    public delegate bool EnumProc(IntPtr h, IntPtr l);
    [DllImport("user32.dll")] public static extern bool EnumWindows(EnumProc cb, IntPtr l);
    [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr h);
    [DllImport("user32.dll")] public static extern bool IsIconic(IntPtr h);
    [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr h, out uint pid);
    [DllImport("user32.dll", CharSet = CharSet.Unicode)] public static extern int GetWindowText(IntPtr h, StringBuilder s, int n);
    [DllImport("user32.dll", CharSet = CharSet.Unicode)] public static extern int GetClassName(IntPtr h, StringBuilder s, int n);
    [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr h, out RECT r);
    [DllImport("user32.dll")] public static extern bool PrintWindow(IntPtr h, IntPtr hdc, uint flags);
    [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr h);
    [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
    [DllImport("user32.dll")] public static extern bool SetProcessDPIAware();
    [StructLayout(LayoutKind.Sequential)] public struct RECT { public int Left, Top, Right, Bottom; }
    public static List<IntPtr> All() {
        var list = new List<IntPtr>();
        EnumWindows((h, l) => { if (IsWindowVisible(h)) list.Add(h); return true; }, IntPtr.Zero);
        return list;
    }
    public static string Text(IntPtr h) { var s = new StringBuilder(512); GetWindowText(h, s, 512); return s.ToString(); }
    public static string Cls(IntPtr h) { var s = new StringBuilder(256); GetClassName(h, s, 256); return s.ToString(); }
    public static uint Pid(IntPtr h) { uint p; GetWindowThreadProcessId(h, out p); return p; }
}
"@
}
[void][StudioWin]::SetProcessDPIAware()

function Get-ProcName([uint32]$procId) {
    try { return (Get-Process -Id $procId -ErrorAction Stop).ProcessName } catch { return "" }
}

function Get-Windows {
    $out = @()
    foreach ($h in [StudioWin]::All()) {
        $r = New-Object StudioWin+RECT; [void][StudioWin]::GetWindowRect($h, [ref]$r)
        $w = $r.Right - $r.Left; $ht = $r.Bottom - $r.Top
        if ($w * $ht -le 400) { continue }
        $title = [StudioWin]::Text($h)
        if (-not $title) { continue }
        $out += [pscustomobject]@{ hwnd = [long]$h; title = $title; class = [StudioWin]::Cls($h);
            process = (Get-ProcName ([StudioWin]::Pid($h))); x = $r.Left; y = $r.Top; width = $w; height = $ht;
            minimized = [StudioWin]::IsIconic($h) }
    }
    return $out
}

function Test-Blank([System.Drawing.Bitmap]$bmp) {
    $first = $bmp.GetPixel(0, 0).ToArgb()
    $stepX = [Math]::Max(1, [int]($bmp.Width / 16)); $stepY = [Math]::Max(1, [int]($bmp.Height / 16))
    for ($y = 0; $y -lt $bmp.Height; $y += $stepY) {
        for ($x = 0; $x -lt $bmp.Width; $x += $stepX) { if ($bmp.GetPixel($x, $y).ToArgb() -ne $first) { return $false } }
    }
    return $true
}

function Save-Png([System.Drawing.Bitmap]$bmp, [string]$name) {
    $scale = [Math]::Min(1.0, [double]$MaxEdge / [Math]::Max($bmp.Width, $bmp.Height))
    if ($scale -lt 1.0) {
        $small = New-Object System.Drawing.Bitmap ([int]($bmp.Width * $scale)), ([int]($bmp.Height * $scale))
        $g = [System.Drawing.Graphics]::FromImage($small)
        $g.InterpolationMode = [System.Drawing.Drawing2D.InterpolationMode]::HighQualityBicubic
        $g.DrawImage($bmp, 0, 0, $small.Width, $small.Height); $g.Dispose()
        $bmp.Dispose(); $bmp = $small
    }
    if (-not (Test-Path $OutDir)) { New-Item -ItemType Directory -Path $OutDir -Force | Out-Null }
    $safe = ($name -replace '[^A-Za-z0-9_-]', '_')
    $path = Join-Path $OutDir ("{0}-{1}.png" -f $safe, (Get-Date).ToString("yyyyMMdd-HHmmss"))
    $bmp.Save($path, [System.Drawing.Imaging.ImageFormat]::Png)
    $res = [pscustomobject]@{ path = $path; width = $bmp.Width; height = $bmp.Height; scale = $scale }
    $bmp.Dispose()
    return $res
}

function Capture-Window($win) {
    $h = [IntPtr]$win.hwnd
    $bmp = New-Object System.Drawing.Bitmap $win.width, $win.height
    $g = [System.Drawing.Graphics]::FromImage($bmp); $hdc = $g.GetHdc()
    $ok = [StudioWin]::PrintWindow($h, $hdc, 2)   # PW_RENDERFULLCONTENT: Electron/DirectX/covered windows
    $g.ReleaseHdc($hdc); $g.Dispose()
    $method = "printwindow"
    if (-not $ok -or (Test-Blank $bmp)) {
        if ($AllowFront) { [void][StudioWin]::SetForegroundWindow($h); Start-Sleep -Milliseconds 250 }
        $g = [System.Drawing.Graphics]::FromImage($bmp)
        $g.CopyFromScreen($win.x, $win.y, 0, 0, $bmp.Size, [System.Drawing.CopyPixelOperation]::SourceCopy)
        $g.Dispose()
        $method = "bitblt"
    }
    return @{ bmp = $bmp; method = $method; blank = (Test-Blank $bmp) }
}

function Remove-Old {
    if (-not (Test-Path $OutDir)) { return }
    $cut = (Get-Date).AddHours(-$RetainHours)
    Get-ChildItem -Path $OutDir -Filter *.png -ErrorAction SilentlyContinue | Where-Object { $_.LastWriteTime -lt $cut } |
        Remove-Item -Force -ErrorAction SilentlyContinue
}

try {
    $sw = [Diagnostics.Stopwatch]::StartNew()
    $allow = Get-Allow
    Remove-Old
    $wins = @(Get-Windows)
    if ($Op -eq "list") {
        Out-Json @{ ok = $true; foreground = [long][StudioWin]::GetForegroundWindow(); windows = (Get-Listed $wins $allow) }
        exit 0
    }
    $res = Resolve-Request $wins $allow
    if ($res.refused) { Write-Audit @{ target = $Target; hwnd = $Hwnd; ok = $false; refused = $res.refused }; throw ("refused: " + $res.refused) }
    if ($res.error) { throw $res.error }
    if ($res.key -eq "screen") {
        $b = [System.Windows.Forms.Screen]::PrimaryScreen.Bounds
        $bmp = New-Object System.Drawing.Bitmap $b.Width, $b.Height
        $g = [System.Drawing.Graphics]::FromImage($bmp); $g.CopyFromScreen($b.X, $b.Y, 0, 0, $bmp.Size); $g.Dispose()
        $png = Save-Png $bmp "screen"
        Write-Audit @{ target = "screen"; ok = $true; path = $png.path }
        Out-Json @{ ok = $true; target = "screen"; method = "bitblt"; path = $png.path; width = $png.width; height = $png.height; ms = $sw.ElapsedMilliseconds }
        exit 0
    }
    $win = $res.win
    if ($res.bridge) { Out-BridgeShot $res.key (Invoke-BridgeCapture $res.key $res.bridge) $false; exit 0 }
    $cap = Capture-Window $win
    if ($cap.blank -and $res.key -eq "unreal" -and $Method -eq "auto") {
        $cap.bmp.Dispose()
        Out-BridgeShot $res.key (Invoke-BridgeCapture $res.key "window rendered blank") $false; exit 0
    }
    $png = Save-Png $cap.bmp $res.key
    Write-Audit @{ target = $res.key; hwnd = $win.hwnd; title = $win.title; ok = $true; method = $cap.method; path = $png.path }
    Out-Json @{ ok = $true; target = $res.key; hwnd = $win.hwnd; title = $win.title; process = $win.process;
                method = $cap.method; blank = $cap.blank; path = $png.path; width = $png.width; height = $png.height;
                scale = $png.scale; ms = $sw.ElapsedMilliseconds }
} catch {
    Out-Json @{ ok = $false; message = $_.Exception.Message }
    exit 1
}

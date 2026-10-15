# SPDX-License-Identifier: MIT
# studio-screen-connector / crashguard.ps1  (Windows PowerShell 5.1+; the suite runs it under pwsh on Linux with -FakeProcs)
# Unreal crash recovery with no popups for the user. One tick per call (tidy.ps1 -Watch calls Invoke-CrashGuard every check_sec):
#   * editor alive but stuck on the Slate "Restore Packages" window (Slate exposes no controls; posted input is ignored
#     when the editor runs hidden)  -> kill editor + CrashReportClient, restore autosaves on disk, relaunch hidden
#   * editor gone + CrashReportClient(Editor) alive + log shows a crash  -> kill CRC quietly (never "Send"), post BLOCKED,
#     restore autosaves on disk, relaunch hidden; from the 2nd crash inside crash_window_min, relaunch with -d3d11
#   * after a relaunch, once the bridge answers ping: load + save every restored package, post a note with the count
# UE 5.8 has no "auto-restore" setting (PackageAutoSaver.cpp: only -AutoDeclinePackageRecovery and -unattended, and both
# DISCARD the restore list), so the restore is done before launch: copy Saved/Autosaves/<AutoSavePath> over
# Content/<PackagePathName>.<ext> for every entry in PackageRestoreData.json, back up what it overwrites, rename the json.
# Never starts an editor while one is running, never relaunches while UnrealBuildTool/AutomationTool is building.
# Fresh GPU fatal evidence is a separate owner hold, including while the editor
# is still alive. That path never clicks, kills, restores files or relaunches.
#   -CgConfig crashguard.json -CgStateFile s.json -CgLog tidy.log [-FakeProcs procs.json]   one tick, prints JSON
#   -RestoreOnly            pre-launch restore only (editor must be closed); prints the restored package list
param([string]$CgConfig = "", [string]$CgStateFile = "", [string]$CgLog = "", [string]$FakeProcs = "", [switch]$RestoreOnly)

$CrashLogPattern = 'Critical error:|Fatal error:|Unhandled Exception:|DXGI_ERROR|GPU crashed'

function Read-CrashGuardConfig([string]$path) {
    if (-not $path) { $path = Join-Path $PSScriptRoot "crashguard.json" }
    return (Get-Content -Path $path -Raw -Encoding UTF8 | ConvertFrom-Json)
}

function Join-Rel([string]$base, [string]$rel) {
    $p = $base
    foreach ($part in ($rel -split '[\\/]' | Where-Object { $_ })) { $p = [IO.Path]::Combine($p, $part) }
    return $p
}

# PackageRestoreData.json is UTF-16LE (often without BOM): sniff the bytes.
function Read-RestoreFile([string]$path) {
    if (-not (Test-Path $path)) { return $null }
    $b = [IO.File]::ReadAllBytes($path)
    if ($b.Length -ge 2 -and ($b[1] -eq 0 -or ($b[0] -eq 0xFF -and $b[1] -eq 0xFE))) { $t = [Text.Encoding]::Unicode.GetString($b) }
    else { $t = [Text.Encoding]::UTF8.GetString($b) }
    return ($t.TrimStart([char]0xFEFF) | ConvertFrom-Json)
}

# Pure: which files to copy. Returns @{ enabled; items=[{package,src,dst}]; missing=[...] }
function Get-RestorePlan([string]$projectDir) {
    $auto = Join-Rel $projectDir "Saved/Autosaves"
    $j = Read-RestoreFile (Join-Rel $auto "PackageRestoreData.json")
    $plan = @{ enabled = $false; items = @(); missing = @() }
    if (-not $j -or $j.RestoreEnabled -ne $true) { return $plan }
    $plan.enabled = $true
    foreach ($k in @($j.Packages)) {
        $src = Join-Rel $auto $k.AutoSavePath
        if (-not (Test-Path $src)) { $plan.missing += [string]$k.PackagePathName; continue }
        if ($k.PackagePathName -notmatch '^/Game/') { $plan.missing += [string]$k.PackagePathName; continue }
        $dst = (Join-Rel (Join-Rel $projectDir "Content") ($k.PackagePathName -replace '^/Game/', '')) + [IO.Path]::GetExtension($src)
        $plan.items += [pscustomobject]@{ package = [string]$k.PackagePathName; src = $src; dst = $dst }
    }
    return $plan
}

# Copies autosaves over content (backing up first), renames the restore json so the editor never prompts.
function Invoke-PreLaunchRestore($cfg, [string]$logPath) {
    $plan = Get-RestorePlan ([string]$cfg.project_dir)
    if (-not $plan.enabled) { return @{ restored = @(); missing = @() } }
    $stamp = (Get-Date).ToString("yyyyMMdd-HHmmss")
    $bk = Join-Rel ([string]$cfg.backup_dir) "crashguard-$stamp"
    $content = Join-Rel ([string]$cfg.project_dir) "Content"
    $auto = Join-Rel ([string]$cfg.project_dir) "Saved/Autosaves"
    New-Item -ItemType Directory -Force -Path $bk | Out-Null
    Copy-Item -Path (Join-Rel $auto "PackageRestoreData.json") -Destination $bk -Force
    $done = @()
    foreach ($it in $plan.items) {
        if (Test-Path $it.dst) {
            $b = Join-Rel $bk ("Content" + $it.dst.Substring($content.Length))
            New-Item -ItemType Directory -Force -Path (Split-Path $b) | Out-Null
            Copy-Item $it.dst $b -Force
        }
        New-Item -ItemType Directory -Force -Path (Split-Path $it.dst) | Out-Null
        Copy-Item $it.src $it.dst -Force
        $done += $it.package
    }
    Rename-Item (Join-Rel $auto "PackageRestoreData.json") "PackageRestoreData.restored-$stamp.json"
    $line = "{0} crashguard restored {1} package(s) from autosaves (backup {2}){3}: {4}" -f (Get-Date).ToString("s"), $done.Count, $bk,
        $(if ($plan.missing.Count) { "; missing " + ($plan.missing -join ", ") } else { "" }), ($done -join ", ")
    if ($logPath) { try { Add-Content -Path $logPath -Value $line -Encoding UTF8 } catch { } }
    return @{ restored = $done; missing = $plan.missing; backup = $bk }
}

function Get-Procs([string]$fake) {
    if ($fake) { return @(Get-Content $fake -Raw | ConvertFrom-Json) }
    $names = "UnrealEditor", "CrashReportClient", "CrashReportClientEditor", "UnrealBuildTool", "AutomationTool"
    return @(Get-Process -Name $names -ErrorAction SilentlyContinue | ForEach-Object {
        $started = $null
        try { $started = $_.StartTime.ToUniversalTime().ToString("o") } catch { }
        [pscustomobject]@{ name = $_.ProcessName; pid = $_.Id; title = $_.MainWindowTitle; started_utc = $started }
    })
}

# Bounded observation of the configured project's current log. A fatal marker
# must belong to an observed editor lifetime, not a stale log or healthy CRC
# companion. UE log timestamps are UTC. No popup identity is inferred from them.
function Get-GpuCrashEvidence($cfg, $procs, $state, [datetime]$NowUtc = [datetime]::UtcNow) {
    $now = $NowUtc.ToUniversalTime()
    $result = @{ available = $false; fatal = $false; reason = "editor-session-unavailable"; source = "editor-log"; popup_observed = $false; read_bytes = 0; truncated = $false }
    # Unknown/ambiguous observations cannot release an already observed fatal.
    if ($state.gpu_hold) {
        $result.fatal = $true; $result.reason = "latched-gpu-fatal"
        $result.pid = $state.gpu_hold.pid; $result.started_utc = $state.gpu_hold.started_utc
        $result.event_utc = $state.gpu_hold.event_utc
    }
    $editors = @($procs | Where-Object { $_.name -eq "UnrealEditor" })
    if ($editors.Count -eq 1) {
        $editorPid = 0; $started = [datetime]::MinValue
        $stampValue = $editors[0].started_utc
        $stamp = if ($stampValue -is [datetime] -and $stampValue.Kind -eq [DateTimeKind]::Utc) { $stampValue.ToString("o") } else { [string]$stampValue }
        if (-not [int]::TryParse([string]$editors[0].pid, [ref]$editorPid) -or $editorPid -le 0 -or
            $stamp -notmatch '^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,7})?Z$' -or
            -not [datetime]::TryParse($stamp, [Globalization.CultureInfo]::InvariantCulture, [Globalization.DateTimeStyles]::RoundtripKind, [ref]$started) -or
            $started -gt $now.AddSeconds(5)) { $result.reason = "invalid-editor-session"; return $result }
        $session = @{ pid = $editorPid; started_utc = $started.ToUniversalTime().ToString("o") }
        $newKey = "{0}|{1}" -f $session.pid, $session.started_utc
        if ($state.gpu_hold -and $state.gpu_hold.session_key -ne $newKey) {
            $state.gpu_hold = $null; $result.fatal = $false; $result.event_utc = $null
        }
        $state.gpu_session = $session
    } elseif ($state.gpu_hold) {
        $result.reason = "latched-gpu-fatal-session-unavailable"
        return $result
    } elseif ($editors.Count -eq 0 -and $state.gpu_session) {
        $session = $state.gpu_session
        $started = [datetime]::MinValue; $editorPid = 0
        $stampValue = $session.started_utc
        $stamp = if ($stampValue -is [datetime] -and $stampValue.Kind -eq [DateTimeKind]::Utc) { $stampValue.ToString("o") } else { [string]$stampValue }
        if (-not [int]::TryParse([string]$session.pid, [ref]$editorPid) -or $editorPid -le 0 -or
            $stamp -notmatch 'Z$' -or
            -not [datetime]::TryParse($stamp, [Globalization.CultureInfo]::InvariantCulture, [Globalization.DateTimeStyles]::RoundtripKind, [ref]$started)) { return $result }
    } else { $result.reason = "ambiguous-editor-session"; return $result }
    $key = "{0}|{1}" -f $session.pid, $session.started_utc
    $result.pid = $session.pid; $result.started_utc = $session.started_utc
    if ($state.gpu_hold) {
        $result.available = $true; $result.fatal = $true; $result.reason = "latched-gpu-fatal"
        $result.event_utc = $state.gpu_hold.event_utc
        return $result
    }
    $stream = $null
    try {
        $share = [IO.FileShare]::ReadWrite -bor [IO.FileShare]::Delete
        $stream = [IO.File]::Open([string]$cfg.editor_log, [IO.FileMode]::Open, [IO.FileAccess]::Read, $share)
        $length = $stream.Length; $offset = [Math]::Max(0, $length - 262144)
        if ($length -eq 0) { $result.available = $true; $result.reason = "no-fresh-gpu-fatal"; return $result }
        [void]$stream.Seek($offset, [IO.SeekOrigin]::Begin)
        [byte[]]$bytes = New-Object byte[] ([int]($length - $offset))
        $read = 0
        while ($read -lt $bytes.Length) {
            $count = $stream.Read($bytes, $read, $bytes.Length - $read)
            if ($count -eq 0) { $result.reason = "log-changed-during-read"; return $result }
            $read += $count
            $result.read_bytes = $read
        }
        $result.read_bytes = $read; $result.truncated = $offset -gt 0
        $begin = 0
        if ($offset -gt 0) {
            while ($begin -lt $read -and $bytes[$begin] -ne 10) { $begin++ }
            if ($begin -eq $read) { $result.reason = "no-complete-log-lines"; return $result }
            $begin++
        }
        $utf8 = New-Object Text.UTF8Encoding($false, $true)
        $text = $utf8.GetString($bytes, $begin, $read - $begin).TrimStart([char]0xFEFF)
        $result.available = $true; $result.reason = "no-fresh-gpu-fatal"
        foreach ($line in ($text -split '\r?\n')) {
            if ($line -notmatch 'LogD3D12RHI:\s*(?:Error|Fatal):\s*GPU crash detected(?:[:\s]|$)' -and
                $line -notmatch 'LogWindows:\s*(?:Error|Fatal):\s*GPU Crash dump Triggered(?:[\s]|$)') { continue }
            if ($line -notmatch '^\[(\d{4}\.\d{2}\.\d{2}-\d{2}\.\d{2}\.\d{2}:\d{3})\]') { continue }
            $event = [datetime]::MinValue
            $style = [Globalization.DateTimeStyles]::AssumeUniversal -bor [Globalization.DateTimeStyles]::AdjustToUniversal
            if (-not [datetime]::TryParseExact($Matches[1], "yyyy.MM.dd-HH.mm.ss:fff", [Globalization.CultureInfo]::InvariantCulture, $style, [ref]$event)) { continue }
            if ($event -lt $started.ToUniversalTime() -or $event -gt $now.AddSeconds(5) -or ($now - $event).TotalSeconds -gt 300) { continue }
            $state.gpu_hold = @{ session_key = $key; pid = $session.pid; started_utc = $session.started_utc; event_utc = $event.ToString("o"); notified = $false; notification_attempted = $false; logged = $false }
            $result.fatal = $true; $result.reason = "fresh-gpu-fatal"; $result.event_utc = $event.ToString("o")
            break
        }
        return $result
    } catch { $result.available = $false; $result.reason = "log-observation-unavailable"; return $result }
    finally { if ($stream) { $stream.Dispose() } }
}

function Test-LogCrashed($cfg) {
    $log = [string]$cfg.editor_log
    if (-not $log -or -not (Test-Path $log)) { return $false }
    return [bool](Get-Content $log -Tail 300 -ErrorAction SilentlyContinue | Select-String -Pattern $CrashLogPattern -Quiet)
}

# Pure policy: "restore_dialog" | "crashed" | "busy_build" | "none"
function Get-CrashAction($procs, [bool]$logCrashed) {
    $ed = @($procs | Where-Object { $_.name -eq "UnrealEditor" })
    $crc = @($procs | Where-Object { $_.name -match '^CrashReportClient(Editor)?$' })
    $build = @($procs | Where-Object { $_.name -match '^(UnrealBuildTool|AutomationTool)$' })
    if ($ed | Where-Object { $_.title -eq "Restore Packages" }) { return "restore_dialog" }
    if ($ed.Count -gt 0) { return "none" }
    if ($crc.Count -gt 0 -and $logCrashed) { if ($build.Count -gt 0) { return "busy_build" } return "crashed" }
    return "none"
}

function Get-RelaunchArgs($cfg, [int]$crashes) {
    $a = @([string]$cfg.extra_args | Where-Object { $_ })
    if ($crashes -ge [int]$cfg.d3d11_after_crashes) { $a += "-d3d11" }
    return ($a -join " ").Trim()
}

function Send-CgNote($cfg, [string]$type, [string]$text) {
    try {
        $body = @{ from = [string]$cfg.from; to = "all"; type = $type; body = @{ text = $text } } | ConvertTo-Json -Compress -Depth 4
        Invoke-RestMethod -Method Post -Uri ([string]$cfg.teamlink_url) -Body $body -ContentType "application/json" -TimeoutSec 3 | Out-Null
        return $true
    } catch { return $false }
}

function Send-CgBridge($cfg, [string]$type, $params, [int]$readMs) {
    $client = New-Object System.Net.Sockets.TcpClient
    try {
        $task = $client.ConnectAsync([string]$cfg.bridge_host, [int]$cfg.bridge_port)
        if (-not $task.Wait(1500) -or -not $client.Connected) { return $null }
        $client.ReceiveTimeout = $readMs
        $s = $client.GetStream()
        $req = (@{ id = "crashguard-" + [guid]::NewGuid().ToString("N").Substring(0, 8); type = $type; params = $params } | ConvertTo-Json -Compress -Depth 4) + "`n"
        $bytes = [Text.Encoding]::UTF8.GetBytes($req); $s.Write($bytes, 0, $bytes.Length)
        $buf = New-Object byte[] 65536; $sb = New-Object System.Text.StringBuilder
        while ($true) { $n = $s.Read($buf, 0, $buf.Length); if ($n -le 0) { break }; [void]$sb.Append([Text.Encoding]::UTF8.GetString($buf, 0, $n)); if ($sb.ToString().Contains("`n")) { break } }
        $line = $sb.ToString().Split("`n")[0]
        if (-not $line) { return $null }
        return ($line | ConvertFrom-Json)
    } catch { return $null } finally { $client.Close() }
}

function Stop-Ids($ids, [bool]$fake, $acts) {
    foreach ($i in $ids) { $acts.Add("kill $i") | Out-Null; if (-not $fake) { Stop-Process -Id $i -Force -ErrorAction SilentlyContinue } }
}

function Start-EditorHidden($cfg, [string]$extra, [bool]$fake, $acts) {
    $cmd = "`"{0}`" `"{1}`" {2}" -f $cfg.editor_exe, $cfg.project_file, $extra
    $acts.Add("launch " + $cmd.Trim()) | Out-Null
    if ($fake) { return $true }
    if (Get-Process UnrealEditor -ErrorAction SilentlyContinue) { return $false }   # never two editors
    $startup = New-CimInstance -ClassName Win32_ProcessStartup -ClientOnly -Property @{ ShowWindow = [uint16]0 }
    $r = Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{ CommandLine = $cmd.Trim(); ProcessStartupInformation = $startup }
    return ($r.ReturnValue -eq 0)
}

# Project-level ini (the editor never rewrites Default*.ini): stop unattended crash uploads.
function Set-CrashPrivacy($cfg) {
    $ini = Join-Rel ([string]$cfg.project_dir) "Config/DefaultEditorSettings.ini"
    $section = "[/Script/UnrealEd.CrashReportsPrivacySettings]"; $key = "bSendUnattendedBugReports"
    $text = if (Test-Path $ini) { [IO.File]::ReadAllText($ini) } else { "" }
    if ($text -match "(?m)^$key=False\s*$") { return $false }
    $text = [regex]::Replace($text, "(?m)^$key=.*\r?\n?", "")
    if ($text.Contains($section)) { $text = $text.Replace($section, "$section`r`n$key=False") }
    else { $text = $text.TrimEnd() + $(if ($text.Trim()) { "`r`n`r`n" } else { "" }) + "$section`r`n$key=False`r`n" }
    New-Item -ItemType Directory -Force -Path (Split-Path $ini) | Out-Null
    [IO.File]::WriteAllText($ini, $text)
    return $true
}

# One tick. $state: crashes (array of ISO times), pending_save (array of package paths)
function Invoke-CrashGuard($cfg, $state, [string]$logPath, [string]$fakeProcs, [datetime]$NowUtc = [datetime]::UtcNow) {
    if ($cfg.enabled -ne $true) { return @{ action = "disabled" } }
    $fake = [bool]$fakeProcs
    $acts = New-Object System.Collections.ArrayList
    $procs = Get-Procs $fakeProcs
    $gpu = Get-GpuCrashEvidence $cfg $procs $state $NowUtc
    if ($gpu.fatal) {
        $message = "GPU fatal evidence HOLD: editor pid={0}, started={1}, event={2}. Owner recovery required; no automatic window/process/file action." -f $gpu.pid, $gpu.started_utc, $gpu.event_utc
        if (-not $state.gpu_hold.logged -and $logPath) {
            try { Add-Content -Path $logPath -Value ($NowUtc.ToUniversalTime().ToString("o") + " crashguard " + $message) -Encoding UTF8; $state.gpu_hold.logged = $true } catch { }
        }
        # A failed/unknown dispatch is not confirmation. Retain the local hold
        # and its delivery status; do not blindly replay the notification.
        if (-not $state.gpu_hold.notification_attempted) {
            $state.gpu_hold.notification_attempted = $true
            $state.gpu_hold.notified = Send-CgNote $cfg "BLOCKED" $message
        }
        return @{ action = "gpu_fatal_hold"; acts = $acts; gpu_evidence = $gpu; owner_recovery_required = $true; notification_attempted = [bool]$state.gpu_hold.notification_attempted; notification_sent = [bool]$state.gpu_hold.notified }
    }
    $action = Get-CrashAction $procs (Test-LogCrashed $cfg)
    $out = @{ action = $action; acts = $acts; gpu_observation = $gpu }
    if ($action -eq "none" -or $action -eq "busy_build") {
        $pending = @($state.pending_save | Where-Object { $_ })
        if ($action -eq "none" -and $pending.Count -gt 0) {
            $ping = Send-CgBridge $cfg "ping" @{} 3000
            if ($ping -and $ping.status -eq "ok") {
                $py = "import unreal`npaths=" + ($pending | ConvertTo-Json -Compress) + "`nif isinstance(paths,str): paths=[paths]`n" +
                      "ok=[p for p in paths if unreal.EditorAssetLibrary.load_asset(p)]`n" +
                      "saved=unreal.EditorLoadingAndSavingUtils.save_dirty_packages(True,True)`nprint('CRASHGUARD_SAVED',len(ok),len(paths),saved)"
                $r = Send-CgBridge $cfg "execute_python" @{ code = $py } 60000
                $txt = [string]$(if ($r) { $r.result.stdout } else { "" })
                $proofs = @([regex]::Matches($txt, '(?m)^CRASHGUARD_SAVED ([0-9]+) ([0-9]+) (True|False)[\t ]*\r?$'))
                $loaded = 0; $requested = 0
                if ($r -and $r.status -eq "ok" -and $proofs.Count -eq 1 -and $proofs[0].Groups[3].Value -eq "True" -and
                    [int]::TryParse($proofs[0].Groups[1].Value, [ref]$loaded) -and [int]::TryParse($proofs[0].Groups[2].Value, [ref]$requested) -and
                    $loaded -eq $pending.Count -and $requested -eq $pending.Count) {
                    $state.pending_save = @()
                    $out.action = "saved"; $out.saved = $loaded
                    if ($logPath) { try { Add-Content -Path $logPath -Value ("{0} crashguard saved {1}/{2} restored package(s)" -f (Get-Date).ToString("s"), $loaded, $requested) } catch { } }
                    [void](Send-CgNote $cfg "note" ("crash-recovery: editor back up, bridge ok; restored + saved {0}/{1} autosaved package(s)." -f $loaded, $requested))
                } else { $out.action = "save_failed" }
            }
        }
        return $out
    }
    $now = Get-Date
    $ed = @($procs | Where-Object { $_.name -eq "UnrealEditor" } | ForEach-Object { $_.pid })
    $crc = @($procs | Where-Object { $_.name -match '^CrashReportClient(Editor)?$' } | ForEach-Object { $_.pid })
    Stop-Ids ($crc + $ed) $fake $acts    # quiet: the crash reporter is killed, so nothing is ever sent
    if (-not $fake) { Start-Sleep -Seconds 3 }
    if ($action -eq "crashed") {
        $win = [double]$cfg.crash_window_min
        $state.crashes = @(@($state.crashes | Where-Object { $_ -and ($now - [datetime]$_).TotalMinutes -le $win }) + $now.ToString("o"))
    }
    $n = @($state.crashes).Count
    $extra = Get-RelaunchArgs $cfg $n
    $why = $(if ($action -eq "crashed") { "editor crashed ($n in $($cfg.crash_window_min) min)" } else { "editor stuck on Restore Packages" })
    [void](Send-CgNote $cfg "BLOCKED" ("BLOCKED crash-recovery: $why. Crash reporter closed without sending; restoring autosaves and relaunching hidden" + $(if ($extra -match '-d3d11') { " with -d3d11" } else { "" }) + "."))
    $res = Invoke-PreLaunchRestore $cfg $logPath
    $state.pending_save = @(@($state.pending_save | Where-Object { $_ }) + $res.restored | Select-Object -Unique)
    $ok = Start-EditorHidden $cfg $extra $fake $acts
    $line = "{0} crashguard {1}: killed {2}; restored {3}; relaunch {4} {5}" -f $now.ToString("s"), $action, (($crc + $ed) -join ","), @($res.restored).Count, $(if ($ok) { "ok" } else { "FAILED" }), $extra
    if ($logPath) { try { Add-Content -Path $logPath -Value $line -Encoding UTF8 } catch { } }
    $out.restored = @($res.restored); $out.missing = @($res.missing); $out.relaunch_args = $extra; $out.crashes = $n; $out.relaunched = $ok
    return $out
}

if ($MyInvocation.InvocationName -ne '.') {
    $ErrorActionPreference = "Stop"
    try {
        if (-not $CgLog) { $CgLog = Join-Path $PSScriptRoot "tidy.log" }
        $cfg = Read-CrashGuardConfig $CgConfig
        if ($RestoreOnly) {
            if (-not $FakeProcs -and (Get-Process UnrealEditor -ErrorAction SilentlyContinue)) { throw "close the editor first" }
            $r = Invoke-PreLaunchRestore $cfg $CgLog
            @{ ok = $true; restored = @($r.restored); missing = @($r.missing) } | ConvertTo-Json -Compress -Depth 4; exit 0
        }
        $state = @{ crashes = @(); pending_save = @() }
        if ($CgStateFile -and (Test-Path $CgStateFile)) {
            $s = Get-Content $CgStateFile -Raw | ConvertFrom-Json
            if ($s.crashes) { $state.crashes = @($s.crashes) }
            if ($s.pending_save) { $state.pending_save = @($s.pending_save) }
            if ($s.gpu_session) { $state.gpu_session = $s.gpu_session }
            if ($s.gpu_hold) { $state.gpu_hold = $s.gpu_hold }
        }
        $r = Invoke-CrashGuard $cfg $state $CgLog $FakeProcs
        if ($CgStateFile) { @{ crashes = @($state.crashes); pending_save = @($state.pending_save); gpu_session = $state.gpu_session; gpu_hold = $state.gpu_hold } | ConvertTo-Json -Compress -Depth 6 | Set-Content -Path $CgStateFile -Encoding UTF8 }
        $r.ok = $true
        $r | ConvertTo-Json -Compress -Depth 4
    } catch { @{ ok = $false; message = $_.Exception.Message } | ConvertTo-Json -Compress; exit 1 }
}

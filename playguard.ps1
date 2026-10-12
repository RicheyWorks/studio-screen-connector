# SPDX-License-Identifier: MIT
# studio-screen-connector / playguard.ps1  (Windows PowerShell 5.1+; tests run under pwsh on Linux)
# Play-guard: Play-in-Editor must never block art imports. Run by tidy.ps1 -Watch every check_sec (playguard.json),
# or one tick at a time:  .\playguard.ps1 [-PgConfig f] [-PgStateFile f] [-PgLog f]   -> one JSON line
# Polls the Unreal bridge (TCP, newline-delimited JSON) with the READ-risk "ping" (result.playing); sends the
# MUTATE "stop" only when the policy says so:
#   stop if PIE has been on > max_play_min AND bot-team\art STATUS.md or QUEUE.md has READY rows waiting
#   stop if STATUS.md has said "Waiting on: Unreal to leave Play mode" for > wait_max_min (while PIE is on)
# Durations are measured from when this guard first saw the state (it keeps first-seen times).
# Never stops when enabled is false. Every stop is logged to tidy.log and posted to team-link as a note.
param([string]$PgConfig = "", [string]$PgStateFile = "", [string]$PgLog = "")

$WaitText = 'Waiting on: Unreal to leave Play mode'

function Read-PlayGuardConfig([string]$path) {
    if (-not $path) { $path = Join-Path $PSScriptRoot "playguard.json" }
    $c = Get-Content -Path $path -Raw -Encoding UTF8 | ConvertFrom-Json
    if (-not $c.art_dir) {
        $c | Add-Member -NotePropertyName art_dir -NotePropertyValue (Join-Path ([Environment]::GetFolderPath("UserProfile")) "projects\bot-team\art") -Force
    }
    return $c
}

# One request/response on the bridge. Short connect and read timeouts; returns $null on any failure.
function Send-BridgeCommand($cfg, [string]$type, [int]$readMs) {
    $client = New-Object System.Net.Sockets.TcpClient
    try {
        $task = $client.ConnectAsync([string]$cfg.bridge_host, [int]$cfg.bridge_port)
        if (-not $task.Wait([int]$cfg.connect_timeout_ms) -or -not $client.Connected) { return $null }
        $client.ReceiveTimeout = $readMs; $client.SendTimeout = 2000
        $stream = $client.GetStream()
        $req = (@{ id = "playguard-" + [guid]::NewGuid().ToString("N").Substring(0, 8); type = $type; params = @{} } | ConvertTo-Json -Compress) + "`n"
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

# READY rows: table rows (|) or list items (- or *) with the word READY. Prose mentioning READY doesn't count.
function Get-ArtSignals($cfg) {
    $ready = 0; $waiting = $false
    foreach ($f in @("STATUS.md", "QUEUE.md")) {
        $p = Join-Path $cfg.art_dir $f
        if (-not (Test-Path $p)) { continue }
        foreach ($l in (Get-Content -Path $p -Encoding UTF8)) {
            if ($l -match '^\s*(\||[-*]\s)' -and $l -cmatch '\bREADY\b') { $ready++ }
            if ($f -eq "STATUS.md" -and $l.Contains($WaitText)) { $waiting = $true }
        }
    }
    return @{ ready = $ready; waiting = $waiting }
}

# Pure policy: returns the reason to stop, or $null.
function Get-StopReason($cfg, [double]$playMin, [int]$ready, [double]$waitMin) {
    if ($cfg.enabled -ne $true) { return $null }
    if ($playMin -gt [double]$cfg.max_play_min -and $ready -gt 0) {
        return ("Play on for {0:N1} min (max {1}) with {2} READY asset rows waiting" -f $playMin, $cfg.max_play_min, $ready)
    }
    if ($waitMin -gt [double]$cfg.wait_max_min) {
        return ("art queue has been '{0}' for {1:N1} min (max {2})" -f $WaitText, $waitMin, $cfg.wait_max_min)
    }
    return $null
}

function Send-TeamNote($cfg, [string]$text) {
    try {
        $body = @{ from = [string]$cfg.from; to = "all"; type = "note"; body = @{ text = $text } } | ConvertTo-Json -Compress -Depth 4
        Invoke-RestMethod -Method Post -Uri ([string]$cfg.teamlink_url) -Body $body -ContentType "application/json" -TimeoutSec 3 | Out-Null
        return $true
    } catch { return $false }
}

# One tick. $state is a hashtable kept by the caller (play_since, wait_since as DateTime or $null).
function Invoke-PlayGuard($cfg, $state, [string]$logPath) {
    if ($cfg.enabled -ne $true) { return @{ action = "disabled" } }
    $now = Get-Date
    $ping = Send-BridgeCommand $cfg "ping" ([int]$cfg.read_timeout_ms)
    if (-not $ping -or $ping.status -ne "ok") { return @{ action = "bridge_unreachable" } }
    if ($ping.result.playing -ne $true) { $state.play_since = $null; $state.wait_since = $null; return @{ action = "none"; playing = $false } }
    if (-not $state.play_since) { $state.play_since = $now }
    $sig = Get-ArtSignals $cfg
    if ($sig.waiting) { if (-not $state.wait_since) { $state.wait_since = $now } } else { $state.wait_since = $null }
    $playMin = ($now - [datetime]$state.play_since).TotalMinutes
    $waitMin = $(if ($state.wait_since) { ($now - [datetime]$state.wait_since).TotalMinutes } else { 0 })
    $reason = Get-StopReason $cfg $playMin $sig.ready $waitMin
    $out = @{ action = "none"; playing = $true; play_min = [math]::Round($playMin, 1); ready = $sig.ready; wait_min = [math]::Round($waitMin, 1) }
    if (-not $reason) { return $out }
    $stop = Send-BridgeCommand $cfg "stop" ([int]$cfg.stop_timeout_ms)
    $ok = [bool]($stop -and $stop.status -eq "ok" -and $stop.result.playing -eq $false)
    $line = "{0} playguard {1} Play: {2}" -f (Get-Date).ToString("s"), $(if ($ok) { "stopped" } else { "FAILED to stop" }), $reason
    if ($logPath) { try { Add-Content -Path $logPath -Value $line -Encoding UTF8 } catch { } }
    $noted = $false
    if ($ok) {
        $state.play_since = $null; $state.wait_since = $null
        $noted = Send-TeamNote $cfg ("Play-in-Editor was auto-stopped by the play-guard: $reason. Imports can continue.")
    }
    $out.action = $(if ($ok) { "stopped" } else { "stop_failed" }); $out.reason = $reason; $out.noted = $noted
    return $out
}

if ($MyInvocation.InvocationName -ne '.') {
    $ErrorActionPreference = "Stop"
    try {
        if (-not $PgLog) { $PgLog = Join-Path $PSScriptRoot "tidy.log" }
        $cfg = Read-PlayGuardConfig $PgConfig
        $state = @{ play_since = $null; wait_since = $null }
        if ($PgStateFile -and (Test-Path $PgStateFile)) {
            $s = Get-Content $PgStateFile -Raw | ConvertFrom-Json
            if ($s.play_since) { $state.play_since = [datetime]$s.play_since }
            if ($s.wait_since) { $state.wait_since = [datetime]$s.wait_since }
        }
        $r = Invoke-PlayGuard $cfg $state $PgLog
        if ($PgStateFile) {
            @{ play_since = $(if ($state.play_since) { ([datetime]$state.play_since).ToString("o") } else { $null });
               wait_since = $(if ($state.wait_since) { ([datetime]$state.wait_since).ToString("o") } else { $null }) } |
                ConvertTo-Json -Compress | Set-Content -Path $PgStateFile -Encoding UTF8
        }
        $r.ok = $true
        $r | ConvertTo-Json -Compress
    } catch {
        @{ ok = $false; message = $_.Exception.Message } | ConvertTo-Json -Compress
        exit 1
    }
}

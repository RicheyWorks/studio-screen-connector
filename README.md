# studio-screen-connector

**Let a remote agent see one allowlisted window on a Windows PC: one read-only PowerShell call, one PNG, nothing uploaded.**

<p align="center"><img src="docs/images/capture-flow.svg" width="900" alt="Flow: the agent calls capture.ps1, the allowlist and denylist are checked, PrintWindow captures the window with no input sent, the PNG and a title-only log entry stay on the PC, and the agent copies the path."></p>

Read-only, on-demand screenshots of allowlisted windows on a Windows PC (the Claude desktop app, Unreal Editor,
Blender), so a remote agent can see what Claude is doing. One PowerShell call per capture: no server, no daemon,
no input sent, nothing uploaded. The PNG stays on the PC until the agent copies it.

Based on `extension/server/win.ps1` from [claude-unreal-connector](https://github.com/RicheyWorks/claude-unreal-connector)
(EnumWindows + PrintWindow with PW_RENDERFULLCONTENT); its UnrealEditor-only filter is generalized into an allowlist.

## Use

```powershell
cd $env:USERPROFILE\projects\studio-screen-connector
.\capture.ps1 -Op list                       # allowlisted windows, no pixels
.\capture.ps1 -Target claude                 # largest Claude window -> <repo>\screens\claude-<time>.png
.\capture.ps1 -Op shot -Hwnd 123456          # a specific window (must be allowlisted)
.\capture.ps1 -Target unreal -Process UnrealEditor
.\capture.ps1 -Target screen                 # full screen, only if enabled in allowlist.json
```

Output is one JSON line: `{"ok":true,"target":"claude","path":"C:\\Users\\<user>\\projects\\studio-screen-connector\\screens\\claude-....png","method":"printwindow",...}`.
The agent then pulls `path` to its box (CopyToBox) and reads it.

## Privacy guard

- Nothing is captured unless a call asks for it.
- `allowlist.json` (machine-local, git-ignored) maps a target to a process name and a title regex; both must match.
- Full-screen capture is disabled by default, and refused whenever a denylisted window is visible.
- Titles matching password managers, banking, Coinbase, Gmail/Outlook, Signal, WhatsApp, authenticators are always refused.
- Every call is logged (title only, no pixels) to `<repo>\screens\capture-log.jsonl`; PNGs older than 24 h are deleted.

## Install (Windows PowerShell 5.1)

```powershell
irm https://raw.githubusercontent.com/RicheyWorks/studio-screen-connector/main/install.ps1 -OutFile $env:TEMP\ssc-install.ps1; & $env:TEMP\ssc-install.ps1
```

This clones or pulls into `$env:USERPROFILE\projects\studio-screen-connector`, creates `allowlist.json` from the example
(never overwriting yours) and creates `<repo>\screens` (git-ignored). Captures stay inside the user profile because CopyToBox only reads paths there. If script execution is blocked, run
`Unblock-File` on the file rather than changing the execution policy.

Verify the machine matches GitHub:

```powershell
$d="$env:USERPROFILE\projects\studio-screen-connector"; git -C $d fetch -q; if((git -C $d rev-parse HEAD) -eq (git -C $d rev-parse origin/main) -and -not (git -C $d status --porcelain)){'MATCH'}else{'DIFFERS'}
```

## Post-job hook: tidy floating Unreal tabs

Floating editor tabs (Content Browser, Message Log, Output Log, ...) cover the viewport. `tidy.ps1` closes them
with `WM_CLOSE` (or minimizes them with `-Minimize`).

**Any harness calls this at the end of every job:**

```powershell
& "$env:USERPROFILE\projects\studio-screen-connector\tidy.ps1"     # prints {"ok":true,"closed":[...],"kept":[...]}
```

**While idle, run the hidden watcher** (it checks every 3 s, allows one instance via `tidy.lock`, and logs each close to `tidy.log`):

```powershell
& "$env:USERPROFILE\projects\studio-screen-connector\tidy-start.ps1"            # start (WMI, hidden, outlives the caller)
& "$env:USERPROFILE\projects\studio-screen-connector\tidy-start.ps1" -Status
& "$env:USERPROFILE\projects\studio-screen-connector\tidy-start.ps1" -Stop
```

A window is touched only if **all** of these are true:
1. It belongs to an `UnrealEditor` process.
2. It has an owner window (`GW_OWNER != 0`), so the main `X - Unreal Editor` window is never touched.
3. Its title doesn't look like a dialog or prompt (Save, Delete, Import, Warning, Error, Confirm, Restore,
   Package, or Message, though "Message Log" is fine). Dialogs are left to the dialog watcher.
4. Its title is in `tidy.json` (committed default list; exact match, case-insensitive).

`tidy.log` and `tidy.lock` are git-ignored. The `tidy` suite and its mutants (one per rule) run in CI.

## Tests

`python -m harness.run_all` runs the dry-run suites (needs `pwsh`), and CI runs them on Linux:

- `contract`: JSON shape and window selection against `tests/fixtures/*.json`
- `privacy`: every deny path is refused
- `tidy`: tidy.ps1 close/keep decisions against `tests/fixtures/tidy_*.json`
- `start`: tidy-start.ps1 -Status returns small JSON with log_tail as plain strings
- `mutants`: the privacy and tidy suites must kill each mutation of their rules (results in `ledgers/mutants.json`)
- `capture_live`: BLACKBEARD only (phase 2)

Ledgers follow the claude-unreal-connector pattern (`harness/ledger.py`): `ledgers/runs.json`, `privacy.json`,
`mutants.json`, `capabilities.json`.

## Later (not in phase 1)

Any local server would bind to 127.0.0.1 and require a token read from a file in the user profile. Long jobs go
through WMI `Win32_Process.Create` (see Daedalus2 `docs/AUTOMATION-LESSONS.md`).

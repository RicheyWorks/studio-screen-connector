# studio-screen-connector

Read-only, on-demand screenshots of allowlisted windows on a Windows PC (the Claude desktop app, Unreal Editor,
Blender), so a remote agent can see what Claude is doing. One PowerShell call per capture: no server, no daemon,
no input sent, nothing uploaded. The PNG stays on the PC until the agent copies it.

Based on `extension/server/win.ps1` from [claude-unreal-connector](https://github.com/RicheyWorks/claude-unreal-connector)
(EnumWindows + PrintWindow with PW_RENDERFULLCONTENT); its UnrealEditor-only filter is generalized into an allowlist.

## Use

```powershell
cd C:\RicheyWorks\studio-screen-connector
.\capture.ps1 -Op list                       # allowlisted windows, no pixels
.\capture.ps1 -Target claude                 # largest Claude window -> C:\RicheyWorks\screens\claude-<time>.png
.\capture.ps1 -Op shot -Hwnd 123456          # a specific window (must be allowlisted)
.\capture.ps1 -Target unreal -Process UnrealEditor
.\capture.ps1 -Target screen                 # full screen, only if enabled in allowlist.json
```

Output is one JSON line: `{"ok":true,"target":"claude","path":"C:\\RicheyWorks\\screens\\claude-....png","method":"printwindow",...}`.
The agent then pulls `path` to its box (CopyToBox) and reads it.

## Privacy guard

- Nothing is captured unless a call asks for it.
- `allowlist.json` (machine-local, git-ignored) maps a target to a process name and a title regex; both must match.
- Full-screen capture is disabled by default, and refused whenever a denylisted window is visible.
- Titles matching password managers, banking, Coinbase, Gmail/Outlook, Signal, WhatsApp, authenticators are always refused.
- Every call is logged (title only, no pixels) to `screens\capture-log.jsonl`; PNGs older than 24 h are deleted.

## Install (Windows PowerShell 5.1)

```powershell
irm https://raw.githubusercontent.com/RicheyWorks/studio-screen-connector/main/install.ps1 -OutFile $env:TEMP\ssc-install.ps1; & $env:TEMP\ssc-install.ps1
```

This clones or pulls into `C:\RicheyWorks\studio-screen-connector`, creates `allowlist.json` from the example
(never overwriting yours) and creates `C:\RicheyWorks\screens`. If script execution is blocked, run
`Unblock-File` on the file rather than changing the execution policy.

Verify the machine matches GitHub:

```powershell
$d='C:\RicheyWorks\studio-screen-connector'; git -C $d fetch -q; if((git -C $d rev-parse HEAD) -eq (git -C $d rev-parse origin/main) -and -not (git -C $d status --porcelain)){'MATCH'}else{'DIFFERS'}
```

## Tests

`python -m harness.run_all` runs the dry-run suites (needs `pwsh`), and CI runs them on Linux:

- `contract`: JSON shape and window selection against `tests/fixtures/*.json`
- `privacy`: every deny path is refused
- `mutants`: the privacy suite must kill each mutation of the guard (results in `ledgers/mutants.json`)
- `capture_live`: BLACKBEARD only (phase 2)

Ledgers follow the claude-unreal-connector pattern (`harness/ledger.py`): `ledgers/runs.json`, `privacy.json`,
`mutants.json`, `capabilities.json`.

## Later (not in phase 1)

Any local server would bind to 127.0.0.1 and require a token read from a file in the user profile. Long jobs go
through WMI `Win32_Process.Create` (see Daedalus2 `docs/AUTOMATION-LESSONS.md`).

# OneDrive/SharePoint Migration Tool

A small interactive command-line tool that copies files between OneDrive accounts
and/or SharePoint sites using [rclone](https://rclone.org/), without relying on
rclone's built-in drive-discovery (which fails for OneDrive Personal accounts).

> Note: the interactive prompts and log messages are currently in **German**
> (the tool was originally written for personal/family use). The logic itself
> is generic and works for any OneDrive/SharePoint combination.

## What it does

At startup you're asked, independently for source and target:

```
OneDrive     -> OneDrive
OneDrive     -> SharePoint site
SharePoint site -> OneDrive
SharePoint site -> SharePoint site
```

Then whether to copy the entire drive or only selected folders, and into which
target subfolder. It shows a summary (source, target, scope, exclusions,
planned copy operations) before anything is copied, retries failed files
automatically, and opens the log (plus an extracted error log) when done.

### Why not just use rclone directly?

- rclone's OAuth login for the `onedrive` backend triggers an automatic
  "list available drives" call (`GET /me/drives`, plural) that Microsoft Graph
  rejects for OneDrive **Personal** accounts with a `403 accessDenied` —
  regardless of scopes/permissions. This tool logs in via `rclone authorize`
  instead and resolves the drive ID directly via Microsoft Graph
  (`/me/drive` for a normal account, `/sites/.../drive` for a SharePoint site),
  then creates the rclone remote non-interactively with that ID.
- Folders that are actually shortcuts to **someone else's** OneDrive (e.g. via
  "Add shortcut to My files") are detected and clearly marked, so you don't
  accidentally copy content that isn't yours.
- SharePoint modifies certain Office file types (`.docx`/`.xlsx`/...) shortly
  after upload (embedded compatibility metadata), which changes their size and
  hash. Plain rclone misreads this as transfer corruption and deletes the
  (actually fine) uploaded file. This tool passes `--ignore-size
  --ignore-checksum` specifically for SharePoint targets to avoid that.

## Requirements

- [rclone](https://rclone.org/downloads/)
- Python 3.10+ (only if running the `.py` script directly — the packaged
  binary below has no dependencies at all)

## Usage

```
python3 OneDrive_Copy.py
python3 OneDrive_Copy.py --ca-cert-bundle /path/to/corporate-ca-bundle.pem
```

`--ca-cert-bundle` is only needed if you're behind a TLS-inspecting corporate
proxy/firewall (Cato, Zscaler, etc.) and haven't set up a bypass rule for
`login.microsoftonline.com`, `login.live.com`, `graph.microsoft.com`,
`*.onedrive.com`, `*.sharepoint.com`.

### Double-click launchers

- **macOS**: `OneDrive_Copy.command` — runs a bundled `OneDrive_Copy` binary if
  present (see Packaging below), otherwise falls back to `python3
  OneDrive_Copy.py` (auto-installing Python via Homebrew if missing).
- **Windows**: `OneDrive_Copy.bat` — runs `OneDrive_Copy.exe` if present,
  otherwise falls back to `py`/`python`, auto-installing Python via `winget`
  if missing.

## Packaging as a single self-contained binary

The script can be bundled with Python *and* rclone into one standalone
executable via [PyInstaller](https://pyinstaller.org/), so end users need
nothing pre-installed:

```bash
pip install pyinstaller
# place a matching rclone binary next to OneDrive_Copy.py first

# macOS/Linux:
pyinstaller --onefile --console --name OneDrive_Copy --add-binary "rclone:." OneDrive_Copy.py

# Windows (note the ";" instead of ":"):
pyinstaller --onefile --console --name OneDrive_Copy --add-binary "rclone.exe;." OneDrive_Copy.py
```

See [BUILD_WINDOWS.txt](BUILD_WINDOWS.txt) for detailed step-by-step Windows
build instructions. PyInstaller can only build for the OS it runs on, so the
Windows executable must be built on Windows.

## Logs

Logs are written to `~/Logs` on macOS/Linux and `C:\Logs` on Windows.
`rclone.conf` working files (never containing long-lived credentials — deleted
at the end of each run) live under `~/Claude/OneDriveCopy`.

## License

MIT — see [LICENSE](LICENSE).

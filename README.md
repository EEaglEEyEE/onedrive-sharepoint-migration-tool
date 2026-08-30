# OneDrive/SharePoint Migration Tool

A small interactive command-line tool for two related tasks around
[rclone](https://rclone.org/) and OneDrive/SharePoint, without relying on
rclone's built-in drive-discovery (which fails for OneDrive Personal
accounts):

1. **Copy/migrate** files between OneDrive accounts and/or SharePoint sites
2. **Find duplicates** across the entire tree of a single account

> Note: the interactive prompts and log messages are currently in **German**
> (the tool was originally written for personal/family use). The logic itself
> is generic and works for any OneDrive/SharePoint combination.

## What it does

At startup you choose which of the two tools to use. Each login (source,
target, or the account to scan for duplicates) first offers any **previously
saved accounts** to pick from — no need to log in again, the tool refreshes
the token automatically before using it — or the option to log in fresh.
After a fresh login you can optionally save the account under a name (e.g.
`Jane Doe (Personal)`) so it shows up next time. If a saved account's token
has stopped working entirely (e.g. `HTTP Error 401: Unauthorized` after a
password change or long inactivity), the account picker also offers
**"Bestehendes Konto neu anmelden"** (re-authenticate an existing account) —
it replaces just the token, keeping the saved name and drive ID. Saved
accounts live in `accounts.conf` next to the script (see [Logs &
data](#logs--data) below) and are never committed to this repository.

### Tool 1: Copy/migrate

Source and target are asked independently, so any combination works:

```
OneDrive        -> OneDrive
OneDrive        -> SharePoint site
SharePoint site -> OneDrive
SharePoint site -> SharePoint site
```

Then whether to copy the entire drive or only selected folders, and into
which target subfolder. It shows a summary (source, target, scope,
exclusions, planned copy operations) before anything is copied, retries
failed files automatically, and opens the log (plus an extracted error log)
when done.

### Tool 2: Find duplicates

Scans an account (saved or freshly logged into) with `rclone lsjson -R
--hash` and writes a CSV report (`dedupe_report_<account-name>_<timestamp>.csv`,
saved to the Desktop by default) classifying files into:

- **1_sicheres_duplikat** — same name *and* same hash (safe to dedupe)
- **2_nur_name_gleich** — same filename, different content (needs a manual look)
- **3_nur_hash_gleich** — identical content, different filename (renamed copy)

Every row also has a `Konto_Share` column: the scanned account's name for
files in its own storage, or the name of the shared-folder link for files
that live under a foreign OneDrive/SharePoint share.

It always skips the Personal Vault (inaccessible via the API regardless), but
— unlike the copy tool — it does NOT exclude folders that are shortcuts to
someone else's OneDrive/SharePoint; those are scanned too, so duplicates
across shared content are found as well. The CSV opens automatically once
it's written — the tool itself never deletes anything, the report is purely
for review.

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
- SharePoint — and OneDrive **for Business**, which runs on the same backend —
  modifies certain Office file types (`.docx`/`.xlsx`/...) shortly after
  upload (embedded compatibility metadata), which changes their size and
  hash. Plain rclone misreads this as transfer corruption, deletes the
  (actually fine) uploaded file, and re-uploads it as a new version on every
  run — silently bloating OneDrive's version history storage over repeated
  runs. This tool passes `--ignore-size --ignore-checksum` for SharePoint and
  OneDrive-Business targets to avoid both problems.

## Requirements

- [rclone](https://rclone.org/downloads/)
- Python 3.10+ (only if running the `.py` script directly — the packaged
  binary below has no dependencies at all)

## Usage

```
python3 onedrive-sharepoint-migration-tool.py
python3 onedrive-sharepoint-migration-tool.py --ca-cert-bundle /path/to/corporate-ca-bundle.pem
```

`--ca-cert-bundle` is only needed if you're behind a TLS-inspecting corporate
proxy/firewall (Cato, Zscaler, etc.) and haven't set up a bypass rule for
`login.microsoftonline.com`, `login.live.com`, `graph.microsoft.com`,
`*.onedrive.com`, `*.sharepoint.com`.

### Double-click launchers

- **macOS**: `onedrive-sharepoint-migration-tool.command` — runs a bundled `onedrive-sharepoint-migration-tool` binary if
  present (see Packaging below), otherwise falls back to `python3
  onedrive-sharepoint-migration-tool.py` (auto-installing Python via Homebrew if missing).
- **Windows**: `onedrive-sharepoint-migration-tool.bat` — runs `onedrive-sharepoint-migration-tool.exe` if present,
  otherwise falls back to `py`/`python`, auto-installing Python via `winget`
  if missing.

## Packaging as a single self-contained binary

The script can be bundled with Python *and* rclone into one standalone
executable via [PyInstaller](https://pyinstaller.org/), so end users need
nothing pre-installed:

```bash
pip install pyinstaller
# place a matching rclone binary next to onedrive-sharepoint-migration-tool.py first

# macOS/Linux:
pyinstaller --onefile --console --name onedrive-sharepoint-migration-tool --add-binary "rclone:." onedrive-sharepoint-migration-tool.py

# Windows (note the ";" instead of ":"):
pyinstaller --onefile --console --name onedrive-sharepoint-migration-tool --add-binary "rclone.exe;." onedrive-sharepoint-migration-tool.py
```

See [BUILD_WINDOWS.txt](BUILD_WINDOWS.txt) for detailed step-by-step Windows
build instructions. PyInstaller can only build for the OS it runs on, so the
Windows executable must be built on Windows.

## Logs & data

- Run logs are written to `~/Logs` on macOS/Linux and `C:\Logs` on Windows.
- Temporary per-run `rclone_*.conf` files (deleted again at the end of each
  run) and the persistent `accounts.conf` live under
  `~/Claude/onedrive-sharepoint-migration-tool`.
- `accounts.conf` contains long-lived OAuth tokens for every saved account —
  treat it like a password file. It's created with owner-only file
  permissions and is excluded from this repository via `.gitignore`.
- Duplicate-finder CSV reports default to the Desktop on both macOS and
  Windows (you can enter a different path when prompted).

## License

MIT — see [LICENSE](LICENSE).

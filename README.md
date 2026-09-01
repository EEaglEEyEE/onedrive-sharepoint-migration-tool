# OneDrive/SharePoint Migration Tool

A small interactive tool for two related tasks around [rclone](https://rclone.org/)
and OneDrive/SharePoint, without relying on rclone's built-in drive-discovery
(which fails for OneDrive Personal accounts):

1. **Copy/migrate** files between OneDrive accounts and/or SharePoint sites
2. **Find duplicates** across the entire tree of a single account

By default the tool starts a small graphical interface (currently: tool 1,
Copy/migrate — tool 2 is marked there as "CLI only for now"). Pass `--cli` to
get the original terminal interface instead, with both tools available. Both
interfaces share the exact same underlying logic (`migration_core.py`).

> Note: the interactive prompts and log messages are currently in **German**
> (the tool was originally written for personal/family use). The logic itself
> is generic and works for any OneDrive/SharePoint combination.

## What it does

At startup you choose which of the two tools to use. Each endpoint (source,
target, or the account to scan for duplicates) first asks what kind it is —
OneDrive, a SharePoint site, or a local path/network drive. For OneDrive or
SharePoint, it then offers any **previously saved accounts of that type**
to pick from — no need to log in again, the tool refreshes the token
automatically before using it — or the option to log in fresh. After a
fresh login you can optionally save the account under a name (e.g. `Jane
Doe (Personal)`) so it shows up next time. If a saved account's token has
stopped working entirely (e.g. `HTTP Error 401: Unauthorized` after a
password change or long inactivity), the account picker also offers
**"Bestehendes Konto neu anmelden"** (re-authenticate an existing account) —
it replaces just the token, keeping the saved name and drive ID. Saved
accounts live in `accounts.conf` next to the script (see [Logs &
data](#logs--data) below) and are never committed to this repository.

### Tool 1: Copy/migrate

Source and target are asked independently, so any combination works:

```
OneDrive                     -> OneDrive
OneDrive                     -> SharePoint site
SharePoint site               -> OneDrive
SharePoint site               -> SharePoint site
OneDrive/SharePoint site      -> local path / network drive   (backup)
local path / network drive    -> OneDrive/SharePoint site      (restore/upload)
```

A local path or network drive needs no login and isn't saved as an account —
just type the path. An already-mounted network drive (macOS `/Volumes/...`,
Windows a drive letter or UNC path) is, as far as rclone is concerned, just a
regular local path.

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
  hash. Plain rclone misreads this as transfer corruption and deletes the
  (actually fine) uploaded file. This tool passes `--ignore-size
  --ignore-checksum` for SharePoint and OneDrive-Business targets to avoid
  that.
- Both target types also create a **new file version on every modifying
  operation** (upload, overwrite, or even just setting the modification
  time), which counts against storage quota. Repeated/resumed runs — even
  ones where every file is already byte-identical at the destination — can
  silently accumulate hundreds of GB of redundant version history this way.
  This tool sets rclone's `no_versions` remote option for SharePoint and
  OneDrive-Business targets, which automatically removes the extra versions
  after each modifying operation. (Not applied to OneDrive Personal — rclone
  can't delete versions there.)

## Requirements

- [rclone](https://rclone.org/downloads/)
- Python 3.10+ with a working `tkinter` (only if running the `.py` script
  directly — the packaged binary below has no dependencies at all).
  Homebrew's Python on macOS does **not** ship `tkinter`; use the official
  python.org installer or `brew install python-tk` instead. `pip install
  customtkinter` is also needed for the GUI.

## Usage

```
python3 onedrive-sharepoint-migration-tool.py                 # GUI (default)
python3 onedrive-sharepoint-migration-tool.py --cli            # terminal, both tools
python3 onedrive-sharepoint-migration-tool.py --ca-cert-bundle /path/to/corporate-ca-bundle.pem
```

`--ca-cert-bundle` is only needed if you're behind a TLS-inspecting corporate
proxy/firewall (Cato, Zscaler, etc.) and haven't set up a bypass rule for
`login.microsoftonline.com`, `login.live.com`, `graph.microsoft.com`,
`*.onedrive.com`, `*.sharepoint.com`.

### Double-click launchers

All launchers expect the packaged binary under `dist/` (see Packaging below)
— that's where PyInstaller puts its output by default, and `build_exe.ps1`/
`build_app.sh` follow the same convention.

- **macOS, GUI**: `dist/onedrive-sharepoint-migration-tool.app` — double-click
  like any other app.
- **macOS, terminal**: `onedrive-sharepoint-migration-tool.command` — runs
  `dist/onedrive-sharepoint-migration-tool --cli` in a Terminal window.
- **Windows**: `onedrive-sharepoint-migration-tool.bat` — runs
  `dist\onedrive-sharepoint-migration-tool.exe` (GUI by default) if present,
  otherwise falls back to `py`/`python` directly on the `.py` script,
  auto-installing Python via `winget` if missing. Pass `--cli` as an argument
  for the terminal interface instead.

## Packaging as a single self-contained binary

The script can be bundled with Python, customtkinter, *and* rclone into one
standalone executable via [PyInstaller](https://pyinstaller.org/), so end
users need nothing pre-installed:

```bash
pip install pyinstaller customtkinter
# place a matching rclone binary next to onedrive-sharepoint-migration-tool.py first

# macOS/Linux:
pyinstaller --onefile --console --name onedrive-sharepoint-migration-tool --add-binary "rclone:." --collect-data customtkinter onedrive-sharepoint-migration-tool.py

# Windows (note the ";" instead of ":"):
pyinstaller --onefile --console --name onedrive-sharepoint-migration-tool --add-binary "rclone.exe;." --collect-data customtkinter onedrive-sharepoint-migration-tool.py
```

The binary is intentionally still a `--console` build on both platforms (no
separate `--windowed` variant to maintain): in GUI mode it hides its own
console window at startup on Windows, and on macOS it's wrapped in a plain
`.app` bundle with no Terminal involved — `--cli` mode uses the same console
normally.

Run [build_app.sh](build_app.sh) (macOS) or [build_exe.ps1](build_exe.ps1)
(Windows) to automate all of the steps above, including downloading a
matching rclone binary if it's not already next to the script, and — for
`build_app.sh` — assembling the `.app` bundle. See
[BUILD_WINDOWS.txt](BUILD_WINDOWS.txt) for detailed step-by-step Windows
build instructions if you'd rather not use the script. PyInstaller can only
build for the OS it runs on, so each platform's binary must be built on that
platform.

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

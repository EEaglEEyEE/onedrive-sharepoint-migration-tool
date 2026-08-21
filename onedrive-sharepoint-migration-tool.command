#!/bin/bash
# Double-click launcher for macOS: Finder runs .command files in a new Terminal
# window. If onedrive-sharepoint-migration-tool (no extension, same folder) is
# a PyInstaller build with Python and rclone already embedded, run that
# directly - no separate Python/Homebrew/rclone setup needed. Otherwise fall
# back to the plain script.
cd "$(dirname "$0")" || exit 1

if [[ -x "./onedrive-sharepoint-migration-tool" ]]; then
    ./onedrive-sharepoint-migration-tool "$@"
else
    if ! command -v python3 >/dev/null 2>&1; then
        echo "python3 not found - trying to install it via Homebrew..."
        if command -v brew >/dev/null 2>&1; then
            brew install python3
        else
            echo "Homebrew not available. Please install Python 3 manually: https://www.python.org/downloads/"
            read -r -p "Press Enter to close..."
            exit 1
        fi
    fi
    python3 onedrive-sharepoint-migration-tool.py "$@"
fi

echo ""
read -r -p "Press Enter to close..."

#!/bin/bash
# Doppelklick-Starter fuer die TERMINAL-Oberflaeche (CLI) unter macOS: Finder
# fuehrt .command-Dateien in einem neuen Terminal-Fenster aus - deshalb --cli.
# Fuer die grafische Oberflaeche stattdessen dist/onedrive-sharepoint-migration-
# tool.app per Doppelklick starten. dist/onedrive-sharepoint-migration-tool
# (ohne Dateiendung) ist eine mit PyInstaller gebaute, eigenstaendige Binary
# mit Python UND rclone bereits eingebettet - kein separates Python/Homebrew/
# rclone-Setup mehr noetig. Build-Ausgaben (.app/.exe/lose Binary) liegen
# immer unter dist/, nie direkt im Projektordner.
cd "$(dirname "$0")" || exit 1

ARGS=("--cli" "$@")
DEFAULT_CA_BUNDLE="/Users/johannes_lassner/combined-ca-bundle.pem"
if [[ ! " ${ARGS[*]} " == *" --ca-cert-bundle "* ]] && [[ -f "$DEFAULT_CA_BUNDLE" ]]; then
    ARGS+=("--ca-cert-bundle" "$DEFAULT_CA_BUNDLE")
fi

./dist/onedrive-sharepoint-migration-tool "${ARGS[@]}"

echo ""
read -r -p "Enter druecken zum Schliessen..."

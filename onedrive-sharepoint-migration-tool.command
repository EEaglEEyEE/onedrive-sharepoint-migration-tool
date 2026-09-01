#!/bin/bash
# Doppelklick-Starter fuer die TERMINAL-Oberflaeche (CLI) unter macOS: Finder
# fuehrt .command-Dateien in einem neuen Terminal-Fenster aus - deshalb --cli.
# Fuer die grafische Oberflaeche stattdessen dist/onedrive-sharepoint-migration-
# tool.app per Doppelklick starten. Ruft die Binary INNERHALB des .app-Bundles
# auf (Contents/MacOS/<name>) - das .spec baut das .app seit dem Umstieg auf
# onedir (statt onefile) als kanonische Ausgabe, die lose Top-Level-Datei
# gleichen Namens gibt es so nicht mehr. Enthaelt Python UND rclone bereits
# eingebettet - kein separates Python/Homebrew/rclone-Setup mehr noetig.
cd "$(dirname "$0")" || exit 1

ARGS=("--cli" "$@")
DEFAULT_CA_BUNDLE="/Users/johannes_lassner/combined-ca-bundle.pem"
if [[ ! " ${ARGS[*]} " == *" --ca-cert-bundle "* ]] && [[ -f "$DEFAULT_CA_BUNDLE" ]]; then
    ARGS+=("--ca-cert-bundle" "$DEFAULT_CA_BUNDLE")
fi

./dist/onedrive-sharepoint-migration-tool.app/Contents/MacOS/onedrive-sharepoint-migration-tool "${ARGS[@]}"

echo ""
read -r -p "Enter druecken zum Schliessen..."

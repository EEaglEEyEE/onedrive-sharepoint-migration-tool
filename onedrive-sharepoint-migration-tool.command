#!/bin/bash
# Doppelklick-Starter fuer macOS: Finder fuehrt .command-Dateien in einem neuen
# Terminal-Fenster aus. onedrive-sharepoint-migration-tool (ohne Dateiendung,
# im selben Ordner) ist eine mit PyInstaller gebaute, eigenstaendige Binary mit
# Python UND rclone bereits eingebettet - kein separates Python/Homebrew/
# rclone-Setup mehr noetig.
cd "$(dirname "$0")" || exit 1

ARGS=("$@")
# Optional: passe diesen Pfad an dein eigenes CA-Bundle an, falls du hinter
# einem TLS-inspizierenden Firmen-Proxy/Firewall sitzt (siehe README).
DEFAULT_CA_BUNDLE="$HOME/combined-ca-bundle.pem"
if [[ ! " ${ARGS[*]} " == *" --ca-cert-bundle "* ]] && [[ -f "$DEFAULT_CA_BUNDLE" ]]; then
    ARGS+=("--ca-cert-bundle" "$DEFAULT_CA_BUNDLE")
fi

./onedrive-sharepoint-migration-tool "${ARGS[@]}"

echo ""
read -r -p "Enter druecken zum Schliessen..."

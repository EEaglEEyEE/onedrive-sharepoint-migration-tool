#!/bin/bash
# Baut onedrive-sharepoint-migration-tool.app (macOS) per PyInstaller - das
# macOS-Aequivalent zu build_exe.ps1 (Windows). Prueft Voraussetzungen, holt
# eine passende rclone-Binary bei Bedarf, baut eine einzelne --console-Binary
# mit eingebettetem Python + rclone und packt sie in ein einfaches
# .app-Bundle. KEIN AppleScript-Terminal-Trampolin mehr (fruehere Versionen
# oeffneten dafuer extra ein Terminal-Fenster) - die GUI braucht kein TTY,
# Contents/MacOS/<name> ist direkt die gebaute Binary. Fuer die
# Terminal-Oberflaeche (CLI) weiterhin onedrive-sharepoint-migration-
# tool.command nutzen, das dieselbe Binary mit --cli aufruft.
#
# Muss im Projektordner liegen (oder per --script-path auf die .py zeigen).
#
# Beispiel:
#   ./build_app.sh
#   ./build_app.sh --script-path /pfad/zu/onedrive-sharepoint-migration-tool.py
set -euo pipefail

SCRIPT_PATH="onedrive-sharepoint-migration-tool.py"
if [[ "${1:-}" == "--script-path" && -n "${2:-}" ]]; then
    SCRIPT_PATH="$2"
fi

fail() { echo "$1" >&2; exit 1; }

[[ -f "$SCRIPT_PATH" ]] || fail "Konnte '$SCRIPT_PATH' nicht finden. Mit --script-path <pfad> auf die .py-Datei zeigen."
PROJECT_DIR="$(cd "$(dirname "$SCRIPT_PATH")" && pwd)"
SCRIPT_NAME="$(basename "$SCRIPT_PATH")"
APP_NAME="onedrive-sharepoint-migration-tool"
cd "$PROJECT_DIR"

# --- Python finden ---
PYTHON_CMD=""
for candidate in python3 python; do
    if command -v "$candidate" >/dev/null 2>&1; then
        PYTHON_CMD="$candidate"
        break
    fi
done
[[ -n "$PYTHON_CMD" ]] || fail "Python wurde nicht gefunden. Bitte zuerst installieren: https://www.python.org/downloads/"
echo "Verwende Python: $PYTHON_CMD ($("$PYTHON_CMD" --version 2>&1))"

# --- tkinter-Verfuegbarkeit pruefen (Voraussetzung fuer die GUI) ---
# Wichtiger, real geprueften Befund (siehe Plan): Homebrew-Python hat
# standardmaessig KEIN tkinter - das faellt hier auf, statt erst mitten im
# PyInstaller-Build oder (schlimmer) erst beim Testen der fertigen Binary.
if ! "$PYTHON_CMD" -c "import tkinter" >/dev/null 2>&1; then
    fail "Das gefundene Python ($PYTHON_CMD) hat kein funktionierendes tkinter (fuer die GUI benoetigt). Homebrew-Python bringt das standardmaessig NICHT mit. Entweder die offizielle Python-Installation von https://www.python.org/downloads/ verwenden (bringt tkinter mit), oder zusaetzlich 'brew install python-tk' ausfuehren - dann dieses Skript erneut starten."
fi

# --- PyInstaller + customtkinter installieren/aktualisieren ---
echo ""
echo "Installiere/aktualisiere PyInstaller und customtkinter..."
"$PYTHON_CMD" -m pip install --quiet --upgrade pyinstaller customtkinter \
    || fail "'pip install pyinstaller customtkinter' fehlgeschlagen."

# --- rclone-Binary (macOS) sicherstellen ---
RCLONE_PATH="$PROJECT_DIR/rclone"
if [[ ! -f "$RCLONE_PATH" ]]; then
    echo ""
    echo "rclone (macOS-Binary) nicht gefunden - lade aktuelle Version herunter..."
    ARCH="$(uname -m)"
    case "$ARCH" in
        arm64) RCLONE_ASSET="rclone-current-osx-arm64.zip" ;;
        x86_64) RCLONE_ASSET="rclone-current-osx-amd64.zip" ;;
        *) fail "Unbekannte Architektur '$ARCH' - rclone bitte manuell von https://rclone.org/downloads/ laden und als '$RCLONE_PATH' ablegen." ;;
    esac
    TMP_DIR="$(mktemp -d)"
    trap 'rm -rf "$TMP_DIR"' EXIT
    curl -fsSL "https://downloads.rclone.org/$RCLONE_ASSET" -o "$TMP_DIR/rclone.zip" \
        || fail "Download von rclone fehlgeschlagen. Alternativ manuell von https://rclone.org/downloads/ laden und als '$RCLONE_PATH' ablegen."
    unzip -q "$TMP_DIR/rclone.zip" -d "$TMP_DIR"
    FOUND_RCLONE="$(find "$TMP_DIR" -name rclone -type f | head -1)"
    [[ -n "$FOUND_RCLONE" ]] || fail "rclone wurde im heruntergeladenen Archiv nicht gefunden."
    cp "$FOUND_RCLONE" "$RCLONE_PATH"
    chmod +x "$RCLONE_PATH"
    echo "rclone nach '$RCLONE_PATH' kopiert."
else
    echo ""
    echo "rclone bereits vorhanden: $RCLONE_PATH"
fi

# --- Alte Build-Artefakte aufraeumen ---
# Bewusst NUR die eigenen Build-Ausgaben (Binary/.app/PyInstaller-build-Ordner/
# *.spec) entfernen, NIE den ganzen dist/-Ordner - dort liegen accounts.conf
# (dauerhaft gespeicherte Konten der ganzen Familie) sowie transiente
# Lauf-Configs eines evtl. gerade laufenden Kopiervorgangs, die nicht
# stillschweigend geloescht werden duerfen.
rm -rf "$PROJECT_DIR/build" "$PROJECT_DIR"/*.spec
rm -f "$PROJECT_DIR/dist/$APP_NAME"
rm -rf "$PROJECT_DIR/dist/$APP_NAME.app"

# --- Build der eigenstaendigen Binary ---
echo ""
echo "Baue $APP_NAME (kann ein bis zwei Minuten dauern)..."
"$PYTHON_CMD" -m PyInstaller --onefile --console --name "$APP_NAME" \
    --add-binary "rclone:." --collect-data customtkinter "$SCRIPT_NAME" \
    || fail "PyInstaller-Build fehlgeschlagen."

BIN_PATH="$PROJECT_DIR/dist/$APP_NAME"
[[ -f "$BIN_PATH" ]] || fail "Build abgeschlossen, aber '$BIN_PATH' wurde nicht gefunden - irgendetwas ist schiefgelaufen."

# --- .app-Bundle zusammensetzen (ohne Terminal-Trampolin, siehe oben) ---
APP_BUNDLE="$PROJECT_DIR/dist/$APP_NAME.app"
mkdir -p "$APP_BUNDLE/Contents/MacOS"
cp "$BIN_PATH" "$APP_BUNDLE/Contents/MacOS/$APP_NAME"
chmod +x "$APP_BUNDLE/Contents/MacOS/$APP_NAME"

cat > "$APP_BUNDLE/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleName</key>
    <string>$APP_NAME</string>
    <key>CFBundleDisplayName</key>
    <string>OneDrive/SharePoint Migration Tool</string>
    <key>CFBundleIdentifier</key>
    <string>de.lassners.$APP_NAME</string>
    <key>CFBundleVersion</key>
    <string>1.0.0</string>
    <key>CFBundleShortVersionString</key>
    <string>1.0.0</string>
    <key>CFBundleExecutable</key>
    <string>$APP_NAME</string>
    <key>CFBundlePackageType</key>
    <string>APPL</string>
    <key>CFBundleInfoDictionaryVersion</key>
    <string>6.0</string>
    <key>LSMinimumSystemVersion</key>
    <string>11.0</string>
    <key>NSHighResolutionCapable</key>
    <true/>
    <key>LSUIElement</key>
    <false/>
</dict>
</plist>
PLIST

echo ""
echo "Fertig: $APP_BUNDLE"
echo "Doppelklick startet die grafische Oberflaeche. Fuer die Terminal-Oberflaeche"
echo "weiterhin onedrive-sharepoint-migration-tool.command per Doppelklick nutzen."

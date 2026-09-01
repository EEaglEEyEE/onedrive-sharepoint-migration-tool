#!/bin/bash
# Baut onedrive-sharepoint-migration-tool.app (macOS) per PyInstaller - das
# macOS-Aequivalent zu build_exe.ps1 (Windows). Prueft Voraussetzungen, holt
# eine passende rclone-Binary bei Bedarf und baut ueber die mitgelieferte
# onedrive-sharepoint-migration-tool.spec (siehe dort) - die .spec regelt
# Icon, Splash-Screen und das .app-Bundling (PyInstallers BUNDLE()) in einem
# Rutsch, statt das Bundle hier von Hand zusammenzusetzen. KEIN AppleScript-
# Terminal-Trampolin (fruehere Versionen oeffneten dafuer extra ein Terminal-
# Fenster) - die GUI braucht kein TTY. Fuer die Terminal-Oberflaeche (CLI)
# weiterhin onedrive-sharepoint-migration-tool.command nutzen, das dieselbe
# Binary mit --cli aufruft.
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
APP_NAME="onedrive-sharepoint-migration-tool"
SPEC_PATH="$PROJECT_DIR/$APP_NAME.spec"
cd "$PROJECT_DIR"

[[ -f "$SPEC_PATH" ]] || fail "Konnte '$SPEC_PATH' nicht finden - die .spec-Datei gehoert zum Repository und sollte neben der .py liegen."
[[ -f "$PROJECT_DIR/app_icon/icon.icns" && -f "$PROJECT_DIR/app_icon/splash.png" ]] \
    || fail "app_icon/icon.icns bzw. app_icon/splash.png fehlen - beide werden von der .spec referenziert."

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
# Bewusst NUR die eigenen Build-Ausgaben (Binary/.app/PyInstaller-build-
# Ordner) entfernen, NIE den ganzen dist/-Ordner - dort liegen accounts.conf
# (dauerhaft gespeicherte Konten der ganzen Familie) sowie transiente
# Lauf-Configs eines evtl. gerade laufenden Kopiervorgangs, die nicht
# stillschweigend geloescht werden duerfen. Die .spec-Datei selbst bleibt
# unangetastet (Teil des Repos, kein Wegwerf-Artefakt mehr).
rm -rf "$PROJECT_DIR/build"
# dist/$APP_NAME ist seit dem Umstieg auf onedir (statt onefile, siehe .spec)
# ein ORDNER (PyInstallers COLLECT()-Ausgabe, aus der das .app-Bundle gebaut
# wird), keine lose Datei mehr.
rm -rf "$PROJECT_DIR/dist/$APP_NAME"
rm -rf "$PROJECT_DIR/dist/$APP_NAME.app"

# --- Build ueber die .spec-Datei (Icon + Splash-Screen + .app-Bundling) ---
echo ""
echo "Baue $APP_NAME (kann ein bis zwei Minuten dauern)..."
"$PYTHON_CMD" -m PyInstaller --noconfirm "$SPEC_PATH" \
    || fail "PyInstaller-Build fehlgeschlagen."

APP_BUNDLE="$PROJECT_DIR/dist/$APP_NAME.app"
[[ -d "$APP_BUNDLE" ]] || fail "Build abgeschlossen, aber '$APP_BUNDLE' wurde nicht gefunden - irgendetwas ist schiefgelaufen."

echo ""
echo "Fertig: $APP_BUNDLE"
echo "Doppelklick startet die grafische Oberflaeche (onedir-Build, startet nahezu sofort - kein Splash-Screen auf macOS, siehe .spec)."
echo "Fuer die Terminal-Oberflaeche weiterhin onedrive-sharepoint-migration-tool.command per Doppelklick nutzen."

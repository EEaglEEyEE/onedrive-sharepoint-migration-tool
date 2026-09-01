# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller-Buildspezifikation - ersetzt die vorherigen reinen CLI-Flag-
Aufrufe in build_app.sh/build_exe.ps1, weil das Splash-Screen-Feature (Icon +
Ladetext waehrend der onefile-Extraktion, siehe unten) und die volle
Icon-Einbindung nur ueber ein .spec-Objektmodell konfigurierbar sind. Beide
Build-Skripte rufen ab jetzt nur noch 'pyinstaller onedrive-sharepoint-
migration-tool.spec' auf; rclone(.exe) muss weiterhin VOR diesem Aufruf schon
im Projektordner liegen (das erledigen die Skripte selbst)."""

import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files

project_dir = Path(SPECPATH)  # noqa: F821 - von PyInstaller zur Laufzeit des .spec injiziert
is_windows = sys.platform.startswith("win")
is_macos = sys.platform == "darwin"
rclone_bin = "rclone.exe" if is_windows else "rclone"
app_name = "onedrive-sharepoint-migration-tool"

a = Analysis(  # noqa: F821
    [str(project_dir / f"{app_name}.py")],
    pathex=[str(project_dir)],
    binaries=[(str(project_dir / rclone_bin), ".")],
    # customtkinter braucht seine Asset-Daten (Themes/Fonts) mitgebuendelt -
    # Aequivalent zu --collect-data customtkinter als CLI-Flag. MUSS hier in
    # Analysis(datas=...) rein (collect_data_files() liefert (src, dest)-2-
    # Tupel) - ein nachtraegliches a.datas += ... schlug fehl, weil a.datas
    # nach Analysis() bereits normalisierte 3-Tupel (dest, src, typecode)
    # enthaelt und die 2-Tupel-Form dort nicht mehr passt.
    datas=collect_data_files("customtkinter"),
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)

pyz = PYZ(a.pure)  # noqa: F821

# Splash-Screen: erscheint SOFORT beim Start, noch bevor die eigentliche
# Python-Laufzeit/das GUI-Fenster bereit ist - genau die Zeitspanne (v.a. unter
# Windows spuerbar: onefile-Extraktion der gebuendelten Runtime bei jedem
# Start), die vorher als kurz aufblitzendes, leeres Konsolenfenster
# wahrgenommen wurde. migration_gui.app schliesst den Splash aktiv, sobald das
# Hauptfenster steht (siehe App.__init__ dort - close_splash()).
# WICHTIG: PyInstallers Splash-Feature ist NICHT auf macOS verfuegbar (bricht
# den Build sonst mit "Splash screen is not supported on macOS" ab) - dort
# faellt der Splash daher ganz weg; das ist unkritisch, weil das .app-Bundle
# ohnehin ohne sichtbares Konsolenfenster startet (kein Trampolin mehr).
splash = None
if not is_macos:
    splash = Splash(  # noqa: F821
        str(project_dir / "app_icon" / "splash.png"),
        binaries=a.binaries,
        datas=a.datas,
        text_pos=(40, 232),
        text_size=13,
        text_color="#5a6472",
        text_default="Wird geladen...",
        text_align="left",
        minify_script=True,
        always_on_top=True,
    )

exe_icon = None
if is_windows:
    exe_icon = str(project_dir / "app_icon" / "icon.ico")

exe_extra_args = [splash, splash.binaries] if splash is not None else []

exe = EXE(  # noqa: F821
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    *exe_extra_args,
    [],
    name=app_name,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    # macOS: console=True aendert nichts Sichtbares (Finder/.app haengt ohnehin
    # kein Terminal an, siehe build_app.sh - bewusst unveraendert gelassen wie
    # im bisherigen, bereits getesteten Setup). Windows: console=False nutzt
    # das WINDOWS-PE-Subsystem statt CONSOLE, wodurch Windows GAR KEIN
    # Konsolenfenster mehr automatisch erzeugt (nicht nur versteckt) - fuer
    # --cli holt sich der Prozess bei Bedarf per AllocConsole() selbst eins
    # (siehe onedrive-sharepoint-migration-tool.py, _ensure_windows_console()).
    console=not is_windows,
    disable_windowed_traceback=False,
    argv_emulation=False,
    icon=exe_icon,
)

if is_macos:
    app = BUNDLE(  # noqa: F821
        exe,
        name=f"{app_name}.app",
        icon=str(project_dir / "app_icon" / "icon.icns"),
        bundle_identifier="de.lassners.onedrive-sharepoint-migration-tool",
        info_plist={
            "CFBundleDisplayName": "OneDrive/SharePoint Migration Tool",
            "CFBundleShortVersionString": "1.0.0",
            "CFBundleVersion": "1.0.0",
            "NSHighResolutionCapable": True,
            "LSMinimumSystemVersion": "11.0",
        },
    )

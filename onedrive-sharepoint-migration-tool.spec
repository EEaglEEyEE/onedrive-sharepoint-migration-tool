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
    # Icon-Dateien als Datenfiles mitbuendeln (zusaetzlich zu EXE(icon=...)
    # unten, das nur die .exe-Datei selbst betrifft): migration_gui/app.py
    # setzt das Fenster-/Taskleisten-Icon zur Laufzeit auf zwei Wegen (siehe
    # _apply_window_icon dort) - root.iconphoto() aus den PNGs UND zusaetzlich
    # WM_SETICON direkt per WinAPI aus icon.ico, deshalb werden hier beide
    # Formate gebraucht. Nur unter Windows noetig, macOS bekommt sein
    # Dock-Icon bereits ueber BUNDLE(icon=...) unabhaengig von Tk.
    datas=collect_data_files("customtkinter") + (
        [
            (str(project_dir / "app_icon" / f"icon_{size}.png"), "app_icon")
            for size in (16, 32, 48, 128, 256)
        ] + [
            (str(project_dir / "app_icon" / "icon.ico"), "app_icon"),
        ] if is_windows else []
    ),
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # pyi_splash wird von PyInstaller automatisch erkannt (statischer Scan
    # nach "import pyi_splash" in migration_gui/app.py) und mitgebuendelt -
    # inklusive eines eigenen Runtime-Hooks, der schon beim Interpreter-Start
    # versucht, die Splash-IPC-Verbindung aufzubauen. Auf macOS gibt es aber
    # gar kein Splash() (siehe unten, PyInstaller-Limitierung), wodurch dieser
    # Runtime-Hook mit einem KeyError ('_PYI_SPLASH_IPC' fehlt) crasht - das
    # passiert VOR jeglichem eigenen Code, ein try/except in
    # migration_gui.app._close_splash() faengt das also nicht ab. Einzig
    # sauberer Fix: das Modul auf macOS erst gar nicht mitbuendeln, dann wird
    # "import pyi_splash" in _close_splash() zu einem regulaeren (dort
    # abgefangenen) ImportError.
    excludes=["pyi_splash"] if is_macos else [],
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

if is_macos:
    # onedir statt onefile fuer das .app-Bundle: PyInstaller warnt selbst,
    # dass onefile+.app "will become an error in v7.0" (nicht mit macOS'
    # Sicherheitsmodell vereinbar) - und praktisch relevanter: onefile
    # entpackt sich bei JEDEM Start neu in einen Temp-Ordner (die spuerbare
    # Verzoegerung, die urspruenglich einen Splash-Screen noetig gemacht
    # haette). Da PyInstallers Splash-Feature auf macOS ohnehin nicht
    # verfuegbar ist (siehe oben), macht onedir die Verzoegerung stattdessen
    # praktisch verschwinden - kein Ladebildschirm noetig statt einem, der
    # technisch nicht gebaut werden kann. Fuer den Nutzer sichtbar aendert
    # sich nichts: ein .app-Bundle wird in Finder/Dock immer schon als EIN
    # Icon dargestellt, unabhaengig davon, ob intern eine Datei oder ein
    # ganzer Ordner (Contents/Frameworks/...) dahinter liegt.
    exe = EXE(  # noqa: F821
        pyz,
        a.scripts,
        [],
        exclude_binaries=True,
        name=app_name,
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=False,
        console=True,  # Finder/.app haengt ohnehin kein Terminal an, siehe build_app.sh
        disable_windowed_traceback=False,
        argv_emulation=False,
    )
    coll = COLLECT(  # noqa: F821
        exe,
        a.binaries,
        a.datas,
        strip=False,
        upx=False,
        upx_exclude=[],
        name=app_name,
    )
    app = BUNDLE(  # noqa: F821
        coll,
        name=f"{app_name}.app",
        icon=str(project_dir / "app_icon" / "icon.icns"),
        bundle_identifier="de.lassners.onedrive-sharepoint-migration-tool",
        info_plist={
            "CFBundleDisplayName": "OneDrive/SharePoint Migration Tool",
            "CFBundleShortVersionString": "1.0.0",
            "CFBundleVersion": "1.0.0",
            "NSHighResolutionCapable": True,
            "LSMinimumSystemVersion": "11.0",
            # Ohne diesen Schluessel bringt ein zweiter Doppelklick nur das
            # bereits laufende Fenster nach vorne, statt eine zweite Instanz
            # zu starten (macOS-Standardverhalten fuer .app-Bundles) - damit
            # kann man z.B. zwei Konten gleichzeitig bearbeiten.
            "LSMultipleInstancesProhibited": False,
        },
    )
else:
    # Windows bleibt onefile (eine einzelne .exe, kein Ordner voller Dateien)
    # MIT Splash-Screen - dort ist beides voll unterstuetzt und die
    # onefile-Extraktionszeit ist genau die Verzoegerung, die der Splash
    # ueberbruecken soll.
    exe = EXE(  # noqa: F821
        pyz,
        a.scripts,
        a.binaries,
        a.datas,
        splash,
        splash.binaries,
        [],
        name=app_name,
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=False,
        upx_exclude=[],
        runtime_tmpdir=None,
        # console=False nutzt das WINDOWS-PE-Subsystem statt CONSOLE, wodurch
        # Windows GAR KEIN Konsolenfenster mehr automatisch erzeugt (nicht nur
        # versteckt) - fuer --cli holt sich der Prozess bei Bedarf per
        # AllocConsole() selbst eins (siehe onedrive-sharepoint-migration-
        # tool.py, _ensure_windows_console()).
        console=False,
        disable_windowed_traceback=False,
        argv_emulation=False,
        icon=exe_icon,
    )

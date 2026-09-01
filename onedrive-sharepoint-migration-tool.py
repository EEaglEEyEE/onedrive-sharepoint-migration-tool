#!/usr/bin/env python3
"""
Eine App fuer zwei Werkzeuge rund um OneDrive/SharePoint via rclone:

  1. Kopieren/Migrieren zwischen OneDrive-Accounts und/oder SharePoint-Sites
  2. Duplikate finden (und optional loeschen) in einem einzelnen Konto

Ohne Argumente startet die grafische Oberflaeche (aktuell: Werkzeug 1,
Kopieren/Migrieren - Werkzeug 2 ist dort als "in Kuerze verfuegbar" markiert
und weiterhin ueber --cli erreichbar). Mit --cli startet stattdessen die
bisherige Terminal-Oberflaeche mit BEIDEN Werkzeugen unveraendert.

--- Konten dauerhaft speichern ---
Bei jedem Endpunkt (Quelle/Ziel/zu durchsuchendes Konto) wird ZUERST gefragt,
ob es sich um OneDrive, eine SharePoint-Site oder einen lokalen Pfad/ein
Netzlaufwerk handelt. Bei OneDrive/SharePoint werden DANACH nur die dazu
passenden BEREITS GESPEICHERTEN Konten angeboten (kein erneuter Login
noetig - rclone erneuert das Token automatisch im Hintergrund), oder eine
NEUE Anmeldung. Nach einer neuen Anmeldung wird ein Name dafuer vorgeschlagen
(z.B. "Jane Doe (Personal)") und das Konto - falls gewuenscht - dauerhaft in
accounts.conf gespeichert, damit es beim naechsten Start direkt ausgewaehlt
werden kann. accounts.conf enthaelt langlebige Zugangsdaten und wird NIE ins
Git-Repo committet (siehe .gitignore) und mit restriktiven Dateirechten (nur
Besitzer) angelegt.
Sollte ein gespeichertes Konto nicht mehr funktionieren (z.B. "HTTP Error
401: Unauthorized", weil der Refresh-Token ungueltig geworden ist - etwa nach
Passwortaenderung oder laengerer Inaktivitaet), steht bei der Kontoauswahl
zusaetzlich "Bestehendes Konto neu anmelden" zur Verfuegung - das ersetzt nur
das Token, Drive-ID/-Typ und der Name bleiben erhalten.

--- Werkzeug 1: Kopieren/Migrieren ---
Fragt Quelle UND Ziel unabhaengig voneinander ab (jeweils OneDrive, SharePoint-
Site, oder ein lokaler Pfad/bereits verbundenes Netzlaufwerk), z.B.:

  OneDrive        -> OneDrive
  OneDrive        -> SharePoint-Site
  SharePoint-Site -> OneDrive
  SharePoint-Site -> SharePoint-Site
  OneDrive/SharePoint-Site -> lokaler Pfad/Netzlaufwerk (Backup)
  lokaler Pfad/Netzlaufwerk -> OneDrive/SharePoint-Site (Restore/Upload)

Ein lokaler Pfad/Netzlaufwerk braucht keinen Login und wird nicht als Konto
gespeichert - ein bereits im Dateisystem eingebundenes Netzlaufwerk (macOS:
/Volumes/..., Windows: Laufwerksbuchstabe oder UNC-Pfad) ist fuer rclone
technisch ein ganz normaler lokaler Pfad.

Danach wird gefragt, ob der GESAMTE Inhalt der Quelle kopiert werden soll oder
nur AUSGEWAEHLTE Ordner, und in welchen Ziel-Unterordner (leer = Root).

- Login (bei neuer Anmeldung) IMMER eigenstaendig per 'rclone authorize' (nicht
  rclones eingebauter config-Wizard): dessen automatische Drive-Discovery
  ('/me/drives', Plural) schlaegt fuer OneDrive-Personal-Konten grundsaetzlich
  mit '403 accessDenied' fehl, unabhaengig von Scopes/Permissions. Die
  Drive-ID wird stattdessen direkt ueber Microsoft Graph ermittelt (/me/drive
  fuer den eigenen Account, bzw. /sites/.../drive fuer eine SharePoint-Site)
  und die rclone-Remote wird non-interaktiv mit Token+Drive-ID angelegt.
- Eintraege im Root der Quelle, die aus einem ANDEREN Konto/einer anderen Site
  hierher verknuepft wurden (z.B. per "Add shortcut to My files"), werden
  erkannt und in der Ordnerauswahl deutlich markiert. Bei "gesamten Inhalt
  kopieren" wird explizit gefragt, ob sie ausgeschlossen werden sollen. Der
  "Persoenliche Tresor" (Personal Vault) wird immer automatisch ausgeschlossen
  (per API grundsaetzlich nicht zugaenglich).
- Ist beim Kopieren einzelner Ordner genau EINER ausgewaehlt, landet dessen
  INHALT direkt im Ziel-Unterordner (kein zusaetzlicher gleichnamiger
  Unterordner). Bei MEHREREN ausgewaehlten Ordnern wird pro Ordner ein
  gleichnamiger Unterordner angelegt, damit sich die Inhalte nicht vermischen.
- Jeder Kopiervorgang wird bei Fehlern automatisch bis zu COPY_RETRY_ATTEMPTS
  mal wiederholt (rclone copy ist idempotent - ein erneuter Lauf kopiert nur
  das nach, was beim letzten Versuch fehlte oder fehlerhaft war).
- Fuer SharePoint- UND OneDrive-Business-Ziele wird die Groessen-/Pruefsummen-
  pruefung nach Upload deaktiviert, da diese Backends Office-Dateien
  serverseitig veraendern. Zusaetzlich wird fuer diese Zielarten der
  rclone-Remote-Parameter 'no_versions' gesetzt, der ueberzaehlige, bei jedem
  erneuten Lauf sonst neu angelegte Dateiversionen automatisch wieder
  entfernt.
- Direkt vor dem Kopieren wird eine Zusammenfassung (Quelle, Ziel, Umfang,
  Exclusions, geplante Kopiervorgaenge) angezeigt UND in die Log-Datei
  geschrieben (zusaetzlich zu rclones eigenen Log-Zeilen).
- Nach Abschluss werden Log-Datei und (falls vorhanden) eine extrahierte
  Fehler-Log-Datei automatisch geoeffnet.

--- Werkzeug 2: Duplikate finden (aktuell nur per --cli) ---
Fragt ein Konto ab (gespeichert oder neu), durchsucht es rekursiv per 'rclone
lsjson -R --hash' (bewusst INKLUSIVE Verknuepfungen zu Fremd-Shares) und
erzeugt eine CSV mit drei Kategorien (sichere Duplikate / nur Name gleich /
nur Hash gleich). Die CSV wird direkt nach dem Erstellen automatisch
geoeffnet. Das Tool loescht selbst nichts.

--- Allgemein ---
TLS-Inspection (z.B. durch einen Firmen-Proxy/Firewall wie Cato, Zscaler etc.):
siehe --ca-cert-bundle weiter unten. Ohne Bypass-Regel fuer login.microsoftonline.com /
login.live.com / graph.microsoft.com / *.onedrive.com / *.sharepoint.com gibt es
sonst TLS-Fehler beim Login bzw. Transfer.

Aufruf:
    python3 onedrive-sharepoint-migration-tool.py                (GUI)
    python3 onedrive-sharepoint-migration-tool.py --cli           (Terminal, beide Werkzeuge)
    python3 onedrive-sharepoint-migration-tool.py --ca-cert-bundle /pfad/zum/firmen-ca-bundle.pem
"""

import argparse
import platform
from pathlib import Path

from migration_core import TRANSFERS, CHECKERS

# Faellt beim Fehlen von --ca-cert-bundle automatisch auf das lokale CA-Bundle
# zurueck, FALLS es existiert (TLS-inspizierender Firmenproxy, z.B. Cato) -
# frueher war das Aufgabe des .command-Launchers bzw. (im .app-Bundle) des
# inzwischen entfernten Resources/launch.sh-Trampolins. Seit die .app die
# Binary ohne Argumente direkt aufruft (kein Trampolin mehr, siehe
# build_app.sh), muss dieser Default hier zentral sitzen, sonst schlaegt der
# OAuth-Login beim Start per Finder-Doppelklick mit einem TLS-
# Zertifikatsfehler fehl, obwohl --cli/.command weiterhin funktionieren.
_DEFAULT_CA_CERT_BUNDLE = Path.home() / "combined-ca-bundle.pem"


def _ensure_windows_console() -> None:
    """Sorgt unter Windows dafuer, dass ein Konsolenfenster fuer --cli
    existiert. Die Binary wird bewusst mit console=False (WINDOWS- statt
    CONSOLE-Subsystem, siehe onedrive-sharepoint-migration-tool.spec)
    gebaut - dadurch entsteht beim GUI-Start (kein --cli) ueberhaupt kein
    Konsolenfenster mehr (nicht nur kurz sichtbar und dann versteckt wie in
    einer frueheren Version), aber --cli braucht fuer print()/input()
    trotzdem eins. AllocConsole() legt bei Bedarf eins an; existiert schon
    eines (z.B. bei einem reinen Konsolen-Build), passiert nichts."""
    import ctypes
    import sys
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    if kernel32.GetConsoleWindow():
        return
    if not kernel32.AllocConsole():
        return
    sys.stdout = open("CONOUT$", "w", encoding="utf-8", errors="replace")
    sys.stderr = open("CONOUT$", "w", encoding="utf-8", errors="replace")
    sys.stdin = open("CONIN$", "r", encoding="utf-8", errors="replace")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Kopiert Daten zwischen OneDrive-Accounts und/oder SharePoint-Sites via rclone, oder findet Duplikate."
    )
    parser.add_argument(
        "--cli",
        action="store_true",
        help="Terminal-Oberflaeche statt der grafischen Oberflaeche starten (beide Werkzeuge: Kopieren/Migrieren + Duplikate finden).",
    )
    parser.add_argument(
        "--ca-cert-bundle",
        default=None,
        help="Pfad zum CA-Bundle (PEM) eines TLS-inspizierenden Firmen-Proxys/Firewall (z.B. Cato, Zscaler), falls keine Bypass-Regel fuer die MS-Login/Graph/SharePoint-Domains existiert.",
    )
    parser.add_argument("--transfers", type=int, default=TRANSFERS)
    parser.add_argument("--checkers", type=int, default=CHECKERS)
    args = parser.parse_args()

    if args.ca_cert_bundle is None and _DEFAULT_CA_CERT_BUNDLE.exists():
        args.ca_cert_bundle = str(_DEFAULT_CA_CERT_BUNDLE)

    if args.cli:
        if platform.system() == "Windows":
            _ensure_windows_console()
        import migration_cli
        migration_cli.main(args)
    else:
        import migration_gui
        migration_gui.main(args)


if __name__ == "__main__":
    main()

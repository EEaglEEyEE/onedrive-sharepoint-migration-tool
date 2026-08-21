#!/usr/bin/env python3
"""
Kopiert Daten zwischen OneDrive-Accounts und/oder SharePoint-Sites via rclone.
Fragt beim Start interaktiv Quelle UND Ziel unabhaengig voneinander ab (jeweils
OneDrive oder SharePoint-Site), z.B.:

  OneDrive     -> OneDrive
  OneDrive     -> SharePoint-Site
  SharePoint-Site -> OneDrive
  SharePoint-Site -> SharePoint-Site

Danach wird gefragt, ob der GESAMTE Inhalt der Quelle kopiert werden soll oder
nur AUSGEWAEHLTE Ordner, und in welchen Ziel-Unterordner (leer = Root).

Ersetzt die vorherigen Einzelscripte OneDrive_Migration.py und
Copy_To_SharePoint.py (deren Logik hier zusammengefuehrt ist).

- Login IMMER eigenstaendig per 'rclone authorize' (nicht rclones eingebauter
  config-Wizard): dessen automatische Drive-Discovery ('/me/drives', Plural)
  schlaegt fuer OneDrive-Personal-Konten grundsaetzlich mit '403 accessDenied'
  fehl, unabhaengig von Scopes/Permissions. Die Drive-ID wird stattdessen direkt
  ueber Microsoft Graph ermittelt (/me/drive fuer den eigenen Account, bzw.
  /sites/.../drive fuer eine SharePoint-Site) und die rclone-Remote wird
  non-interaktiv mit Token+Drive-ID angelegt.
- Eintraege im Root der Quelle, die aus einem ANDEREN Konto/einer anderen Site
  hierher verknuepft wurden (z.B. per "Add shortcut to My files"), werden
  erkannt und in der Ordnerauswahl deutlich markiert. Bei "gesamten Inhalt
  kopieren" wird explizit gefragt, ob sie ausgeschlossen werden sollen.
- Ist beim Kopieren einzelner Ordner genau EINER ausgewaehlt, landet dessen
  INHALT direkt im Ziel-Unterordner (kein zusaetzlicher gleichnamiger
  Unterordner). Bei MEHREREN ausgewaehlten Ordnern wird pro Ordner ein
  gleichnamiger Unterordner angelegt, damit sich die Inhalte nicht vermischen.
- Jeder Kopiervorgang wird bei Fehlern automatisch bis zu COPY_RETRY_ATTEMPTS
  mal wiederholt (rclone copy ist idempotent - ein erneuter Lauf kopiert nur
  das nach, was beim letzten Versuch fehlte oder fehlerhaft war).
- Direkt vor dem Kopieren wird eine Zusammenfassung (Quelle, Ziel, Umfang,
  Exclusions, geplante Kopiervorgaenge) angezeigt UND in die Log-Datei
  geschrieben (zusaetzlich zu rclones eigenen Log-Zeilen).
- Nach Abschluss werden Log-Datei und (falls vorhanden) eine extrahierte
  Fehler-Log-Datei automatisch geoeffnet.
- Kein dauerhaft gespeichertes Token - die rclone.conf mit den Zugangsdaten
  wird am Ende geloescht.

TLS-Inspection (z.B. durch einen Firmen-Proxy/Firewall wie Cato, Zscaler etc.):
siehe --ca-cert-bundle weiter unten. Ohne Bypass-Regel fuer login.microsoftonline.com /
login.live.com / graph.microsoft.com / *.onedrive.com / *.sharepoint.com gibt es
sonst TLS-Fehler beim Login bzw. Transfer.

Aufruf:
    python3 onedrive-sharepoint-migration-tool.py
    python3 onedrive-sharepoint-migration-tool.py --ca-cert-bundle /pfad/zum/firmen-ca-bundle.pem
"""

import argparse
import datetime
import json
import os
import platform
import re
import shutil
import ssl
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

TRANSFERS = 8
CHECKERS = 16
COPY_RETRY_ATTEMPTS = 3
GRAPH_ROOT = "https://graph.microsoft.com/v1.0"


def _resolve_rclone_bin() -> str:
    """Als PyInstaller-Bundle gebaut liegt eine passende rclone-Binary direkt
    im Bundle bei (siehe build-Skript) und wird bevorzugt verwendet - dann ist
    kein separates rclone-Setup auf dem Zielsystem noetig. Als normales .py-
    Script (kein Bundle) bleibt es bei der ueblichen PATH-Suche."""
    if getattr(sys, "frozen", False):
        bundled_name = "rclone.exe" if platform.system() == "Windows" else "rclone"
        bundled_path = Path(getattr(sys, "_MEIPASS", "")) / bundled_name
        if bundled_path.exists():
            return str(bundled_path)
    return "rclone"


RCLONE_BIN = _resolve_rclone_bin()


def find_rclone() -> str | None:
    if RCLONE_BIN != "rclone":
        return RCLONE_BIN
    return shutil.which("rclone")


def install_rclone() -> None:
    system = platform.system()
    print("rclone nicht gefunden - versuche automatische Installation...")

    if system == "Windows":
        if shutil.which("winget"):
            subprocess.run(
                ["winget", "install", "Rclone.Rclone", "-e", "--silent"],
                check=False,
            )
            # winget schreibt den neuen PATH-Eintrag nur in die Registry - der
            # bereits laufende Prozess (und damit auch dieses Script) sieht ihn
            # erst nach einem Neustart der Shell. Der von winget angelegte
            # Alias-Ordner wird daher direkt fuer diesen Prozess ergaenzt, damit
            # 'rclone' sofort gefunden wird, ohne dass der Nutzer das Script neu
            # starten muss.
            winget_links = os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\WinGet\Links")
            if os.path.isdir(winget_links):
                os.environ["PATH"] = winget_links + os.pathsep + os.environ.get("PATH", "")
        else:
            print("winget nicht verfuegbar. Bitte manuell installieren: https://rclone.org/downloads/")
            sys.exit(1)
    elif system == "Darwin":
        if shutil.which("brew"):
            subprocess.run(["brew", "install", "rclone"], check=False)
        else:
            print("Homebrew nicht verfuegbar. Bitte manuell installieren: https://rclone.org/downloads/")
            sys.exit(1)
    else:
        print(f"Unbekanntes Betriebssystem '{system}'. Bitte rclone manuell installieren: https://rclone.org/downloads/")
        sys.exit(1)

    if not find_rclone():
        print("rclone-Installation fehlgeschlagen. Bitte manuell installieren: https://rclone.org/downloads/")
        sys.exit(1)


def run(cmd: list[str], env: dict, display_cmd: list[str] | None = None) -> int:
    if display_cmd is None and cmd and cmd[0] == RCLONE_BIN and RCLONE_BIN != "rclone":
        # Im gebuendelten Bundle liegt rclone unter einem haesslichen Temp-
        # Extraktionspfad - fuer die Konsolenausgabe stattdessen 'rclone' zeigen.
        display_cmd = ["rclone", *cmd[1:]]
    print(f"\n$ {' '.join(display_cmd if display_cmd is not None else cmd)}\n")
    result = subprocess.run(cmd, env=env)
    return result.returncode


def copy_to_clipboard(text: str) -> bool:
    """Kopiert Text in die System-Zwischenablage. Gibt False zurueck, wenn das
    auf dieser Plattform nicht moeglich war (z.B. kein xclip/xsel installiert)."""
    system = platform.system()
    try:
        if system == "Darwin":
            subprocess.run(["pbcopy"], input=text, text=True, check=True)
        elif system == "Windows":
            subprocess.run(["clip"], input=text, text=True, check=True)
        else:
            subprocess.run(["xclip", "-selection", "clipboard"], input=text, text=True, check=True)
        return True
    except (OSError, subprocess.CalledProcessError):
        return False


def open_in_viewer(path: Path) -> None:
    """Oeffnet eine Datei mit der Standardanwendung des Betriebssystems."""
    system = platform.system()
    try:
        if system == "Darwin":
            subprocess.run(["open", str(path)], check=False)
        elif system == "Windows":
            os.startfile(str(path))  # type: ignore[attr-defined]
        else:
            subprocess.run(["xdg-open", str(path)], check=False)
    except OSError as exc:
        print(f"Konnte '{path}' nicht automatisch oeffnen: {exc}")


def log_manifest(log_file: Path, message: str) -> None:
    """Schreibt eine gut sichtbare Zeile direkt in die Log-Datei, zusaetzlich zu
    rclones eigenen (dateibezogenen) Log-Zeilen."""
    with open(log_file, "a", encoding="utf-8") as f:
        f.write(f"{datetime.datetime.now():%Y-%m-%d %H:%M:%S} MANIFEST: {message}\n")


def extract_error_lines(log_file: Path) -> tuple[Path, int]:
    """Extrahiert alle ERROR-Zeilen aus dem Haupt-Log in eine eigene Datei,
    damit Fehler nach Abschluss auf einen Blick sichtbar sind."""
    error_file = log_file.with_name(log_file.stem + "_errors.log")
    lines = log_file.read_text(encoding="utf-8", errors="replace").splitlines() if log_file.exists() else []
    error_lines = [line for line in lines if "ERROR" in line]
    error_file.write_text("\n".join(error_lines) + ("\n" if error_lines else ""), encoding="utf-8")
    return error_file, len(error_lines)


def rclone_authorize_onedrive(env: dict, label: str) -> str:
    """Eigenstaendiger OAuth-Login (rclone authorize) fuer OneDrive/SharePoint,
    ohne rclones eingebauten config-Wizard zu durchlaufen. label ist nur fuer die
    Konsolenausgabe."""
    print(f"\n--- OAuth-Login: {label} ---")
    cmd = [RCLONE_BIN, "authorize", "onedrive"]
    print(f"\n$ {' '.join(['rclone', *cmd[1:]])}\n")
    process = subprocess.Popen(
        cmd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )
    output_lines: list[str] = []
    url_copied = False
    for line in process.stdout:
        print(line, end="")
        output_lines.append(line)
        if not url_copied and "following link" in line:
            match = re.search(r"https?://\S+", line)
            if match and copy_to_clipboard(match.group(0)):
                url_copied = True
                print("(Login-URL wurde in die Zwischenablage kopiert)")
    process.wait()

    output = "".join(output_lines)
    marker_start = "Paste the following into your remote machine --->"
    marker_end = "<---End paste"
    if process.returncode != 0 or marker_start not in output or marker_end not in output:
        print(f"\nOAuth-Login ({label}) fehlgeschlagen (Exit Code {process.returncode}), kein Token erhalten.")
        sys.exit(process.returncode or 1)

    return output.split(marker_start, 1)[1].split(marker_end, 1)[0].strip()


def graph_get(url: str, token_json: str, ca_cert_bundle: str | None) -> dict:
    access_token = json.loads(token_json)["access_token"]
    context = ssl.create_default_context(cafile=ca_cert_bundle) if ca_cert_bundle else None
    request = urllib.request.Request(url, headers={"Authorization": f"Bearer {access_token}"})
    with urllib.request.urlopen(request, timeout=15, context=context) as response:
        return json.loads(response.read())


def fetch_own_drive(token_json: str, ca_cert_bundle: str | None) -> tuple[str, str, str]:
    """Ermittelt Drive-ID/-Typ des eingeloggten Accounts ueber Microsoft Graph
    'GET /me/drive' (Singular) - der einzige Drive-Endpunkt, den Microsoft Graph
    fuer OneDrive-Personal-Konten tatsaechlich unterstuetzt (funktioniert genauso
    fuer OneDrive-for-Business-Konten). Die Antwort enthaelt unter "owner" auch
    Name/Mail des Accounts - das spart einen separaten 'GET /me'-Aufruf, der
    die Graph-Berechtigung 'User.Read' braucht, die rclone beim OAuth-Login
    nie anfragt (fuehrte vorher zu einem stillen 403 und "unbekannter Account")."""
    drive = graph_get(f"{GRAPH_ROOT}/me/drive", token_json, ca_cert_bundle)
    owner = drive.get("owner", {}).get("user", {})
    name = owner.get("displayName") or "?"
    email = owner.get("email") or owner.get("id") or "?"
    identity = f"{name} <{email}>"
    return drive["id"], drive.get("driveType", "personal"), identity


# Bekannte Namen des "Persoenlichen Tresors" (Personal Vault) in verschiedenen
# Sprachen. Der Tresor ist per API grundsaetzlich nicht zugaenglich (braucht
# eine interaktive PIN/2FA-Entsperrung, die ein Graph-Token nicht leisten
# kann) - jeder Kopierversuch schlaegt garantiert fehl ("ObjectHandle is
# Invalid"). Wird deshalb immer automatisch ausgeschlossen, nie nur markiert.
KNOWN_LOCKED_VAULT_NAMES = {"persönlicher tresor", "personal vault"}


def list_root_items(token_json: str, drive_id: str, ca_cert_bundle: str | None) -> list[dict]:
    """Listet Root-Eintraege der angegebenen Drive (OneDrive-Account ODER
    SharePoint-Dokumentbibliothek - '/drives/{id}/root/children' funktioniert
    fuer beide) mit Ordner-Flag und einer Kennzeichnung, ob der Eintrag
    tatsaechlich aus einem ANDEREN Konto/einer anderen Site hierher verknuepft
    wurde. Reine remoteItem-Praesenz reicht dafuer nicht als Kriterium, da z.B.
    der "Persoenliche Tresor" (Personal Vault) technisch auch ein remoteItem
    ist, aber trotzdem eigener Speicher bleibt. Zuverlaessig unterscheidbar ist
    das ueber die Besitzer-Drive-ID, die in remoteItem.sharepointIds.siteUrl
    steckt (".../personal/<driveId>")."""
    access_token = json.loads(token_json)["access_token"]
    context = ssl.create_default_context(cafile=ca_cert_bundle) if ca_cert_bundle else None
    own_drive_id_lower = drive_id.lower()
    site_url_re = re.compile(r"/personal/([0-9a-zA-Z]+)")
    url = f"{GRAPH_ROOT}/drives/{drive_id}/root/children?$select=name,folder,remoteItem&$top=200"
    items: list[dict] = []
    while url:
        request = urllib.request.Request(url, headers={"Authorization": f"Bearer {access_token}"})
        with urllib.request.urlopen(request, timeout=15, context=context) as response:
            page = json.loads(response.read())
        for item in page.get("value", []):
            remote = item.get("remoteItem")
            is_foreign = False
            if remote:
                site_url = remote.get("sharepointIds", {}).get("siteUrl", "")
                match = site_url_re.search(site_url)
                remote_drive_id = match.group(1).lower() if match else None
                is_foreign = remote_drive_id != own_drive_id_lower
            is_locked_vault = bool(remote) and item["name"].strip().lower() in KNOWN_LOCKED_VAULT_NAMES
            items.append({
                "name": item["name"],
                "is_folder": "folder" in item,
                "is_foreign": is_foreign,
                "is_locked_vault": is_locked_vault,
            })
        url = page.get("@odata.nextLink")
    return items


def prompt_folder_selection(root_items: list[dict]) -> list[str]:
    """Zeigt alle Ordner (auch fremd verknuepfte, deutlich markiert) an; die
    Auswahl selbst entscheidet, ob ein markierter Ordner mitkopiert wird. Der
    Persoenliche Tresor wird hier erst gar nicht angeboten, da er nie
    kopierbar ist (siehe KNOWN_LOCKED_VAULT_NAMES)."""
    folder_items = [item for item in root_items if item["is_folder"] and not item["is_locked_vault"]]
    print("\nVerfuegbare Ordner:")
    for i, item in enumerate(folder_items, start=1):
        marker = "  [Verknuepfung aus ANDEREM Konto/Site]" if item["is_foreign"] else ""
        print(f"  {i}. {item['name']}{marker}")
    raw = input(
        "\nWelche Ordner sollen kopiert werden? Nummern kommagetrennt (z.B. 1,3,4) oder 'alle': "
    ).strip()

    names = [item["name"] for item in folder_items]
    if raw.lower() in ("alle", "all"):
        return names

    selected: list[str] = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            index = int(part)
        except ValueError:
            print(f"Ungueltige Eingabe ignoriert: '{part}'")
            continue
        if 1 <= index <= len(names):
            selected.append(names[index - 1])
        else:
            print(f"Nummer ausserhalb des Bereichs ignoriert: {index}")

    if not selected:
        print("Keine gueltigen Ordner ausgewaehlt - Abbruch.")
        sys.exit(1)
    return selected


def search_sharepoint_sites(query: str, token_json: str, ca_cert_bundle: str | None) -> list[dict]:
    """Sucht SharePoint-Sites ueber Microsoft Graph 'GET /sites?search={query}'.
    '*' ist die uebliche Konvention, um moeglichst alle fuer den Account
    sichtbaren Sites zurueckzubekommen (statt einer echten Volltextsuche)."""
    encoded_query = urllib.parse.quote(query if query else "*")
    data = graph_get(f"{GRAPH_ROOT}/sites?search={encoded_query}", token_json, ca_cert_bundle)
    return data.get("value", [])


def prompt_site_selection(token_json: str, ca_cert_bundle: str | None) -> dict:
    """Fragt interaktiv einen Suchbegriff ab, listet passende SharePoint-Sites
    auf und laesst den Nutzer eine davon auswaehlen. Bei Bedarf kann die Suche
    mit einem anderen Begriff wiederholt werden."""
    while True:
        query = input(
            "\nSuchbegriff fuer die SharePoint-Site (Enter fuer alle sichtbaren Sites): "
        ).strip()
        try:
            sites = search_sharepoint_sites(query, token_json, ca_cert_bundle)
        except (urllib.error.URLError, KeyError, json.JSONDecodeError) as exc:
            print(f"Suche fehlgeschlagen: {exc}")
            continue

        if not sites:
            print("Keine Sites gefunden - anderen Suchbegriff versuchen.")
            continue

        print("\nGefundene Sites:")
        for i, site in enumerate(sites, start=1):
            name = site.get("displayName") or site.get("name") or "(ohne Namen)"
            print(f"  {i}. {name} - {site.get('webUrl', '')}")

        raw = input("\nNummer der Site waehlen (oder 's' fuer neue Suche): ").strip()
        if raw.lower() == "s":
            continue
        try:
            index = int(raw)
        except ValueError:
            print("Ungueltige Eingabe.")
            continue
        if 1 <= index <= len(sites):
            return sites[index - 1]
        print(f"Nummer ausserhalb des Bereichs: {index}")


def prompt_endpoint_type(label: str) -> str:
    print(f"\n=== {label} ===")
    print("  1. OneDrive")
    print("  2. SharePoint-Site")
    while True:
        choice = input("Auswahl (1/2): ").strip()
        if choice in ("1", "2"):
            return "onedrive" if choice == "1" else "sharepoint"
        print("Ungueltige Eingabe - bitte 1 oder 2 eingeben.")


def resolve_endpoint(env: dict, label: str, ca_cert_bundle: str | None, config_path: Path) -> dict:
    """Fragt interaktiv ab, ob dieser Endpunkt (Quelle/Ziel) ein OneDrive-Account
    oder eine SharePoint-Site ist, fuehrt den passenden Login durch und liefert
    Token/Drive-ID/-Typ plus einen menschenlesbaren Bezeichner fuer die
    Zusammenfassung zurueck."""
    endpoint_type = prompt_endpoint_type(label)
    type_label = "OneDrive" if endpoint_type == "onedrive" else "SharePoint"
    token = rclone_authorize_onedrive(env, f"{label} ({type_label})")

    try:
        if endpoint_type == "onedrive":
            drive_id, drive_type, identity = fetch_own_drive(token, ca_cert_bundle)
            print(f"Gefundene Drive-ID: {drive_id} (Typ: {drive_type})")
            return {
                "token": token,
                "drive_id": drive_id,
                "drive_type": drive_type,
                "kind": "onedrive",
                "identity": f"OneDrive ({identity})",
            }

        site = prompt_site_selection(token, ca_cert_bundle)
        site_name = site.get("displayName") or site.get("name") or "(ohne Namen)"
        print(f"Ausgewaehlte Site: {site_name} - {site.get('webUrl', '')}")
        drive = graph_get(f"{GRAPH_ROOT}/sites/{site['id']}/drive", token, ca_cert_bundle)
        drive_id = drive["id"]
        drive_type = drive.get("driveType", "documentLibrary")
        print(f"Gefundene Dokumentbibliothek-Drive-ID: {drive_id} (Typ: {drive_type})")
        return {
            "token": token,
            "drive_id": drive_id,
            "drive_type": drive_type,
            "kind": "sharepoint",
            "identity": f"SharePoint-Site '{site_name}' ({site.get('webUrl', '')})",
        }
    except (urllib.error.URLError, KeyError, json.JSONDecodeError) as exc:
        print(f"\nKonnte {label} nicht ueber Microsoft Graph aufloesen: {exc}")
        config_path.unlink(missing_ok=True)
        sys.exit(1)


def create_onedrive_remote(
    remote_name: str,
    token_json: str,
    drive_id: str,
    drive_type: str,
    config_path: Path,
    env: dict,
    extra_config: dict[str, str] | None = None,
) -> int:
    """Legt eine rclone-onedrive-Remote non-interaktiv mit vorgegebenem Token
    und Drive-ID an (funktioniert identisch fuer OneDrive- und SharePoint-Drives)
    - rclones eigene Drive-Discovery wird dabei nie aufgerufen."""
    extra_config = extra_config or {}
    cmd = [
        RCLONE_BIN, "config", "create", remote_name, "onedrive",
        "--config", str(config_path),
        "region", "global",
        "token", token_json,
        "drive_id", drive_id,
        "drive_type", drive_type,
    ]
    display_cmd = [
        "rclone", "config", "create", remote_name, "onedrive",
        "--config", str(config_path),
        "region", "global",
        "token", "<redacted>",
        "drive_id", drive_id,
        "drive_type", drive_type,
    ]
    for key, value in extra_config.items():
        cmd += [key, value]
        display_cmd += [key, value]
    cmd.append("--non-interactive")
    display_cmd.append("--non-interactive")
    return run(cmd, env, display_cmd=display_cmd)


def run_copy_with_retry(
    source_spec: str,
    target_spec: str,
    config_path: Path,
    log_file: Path,
    env: dict,
    args,
    extra_args: list[str] | None = None,
) -> int:
    """Fuehrt 'rclone copy' fuer ein Quelle->Ziel-Paar aus und wiederholt bei
    Fehlschlag bis zu COPY_RETRY_ATTEMPTS mal. rclone copy ist idempotent - ein
    erneuter Lauf kopiert nur das nach, was beim vorherigen Versuch fehlte oder
    fehlerhaft war, das genuegt als Retry fuer einzelne fehlgeschlagene Dateien
    (z.B. wegen kurzzeitiger Netzwerkfehler)."""
    extra_args = extra_args or []
    cmd_options = [
        "copy", source_spec, target_spec,
        "--config", str(config_path),
        "--transfers", str(args.transfers),
        "--checkers", str(args.checkers),
        "--checksum",
        "--retries", "5",
        "--low-level-retries", "10",
        "--progress",
        "--log-file", str(log_file),
        "--log-level", "INFO",
    ]
    cmd = [RCLONE_BIN, *cmd_options] + extra_args
    # Exclusions stehen bereits gut lesbar in der Zusammenfassung - im Kommando
    # selbst nur die Anzahl andeuten, um die Ausgabe nicht zuzumuellen.
    display_cmd = ["rclone", *cmd_options] + (
        [f"--exclude(x{len(extra_args) // 2}, siehe Zusammenfassung)"] if extra_args else []
    )

    print(f"\n--- Kopiere {source_spec} -> {target_spec} ---")
    log_manifest(log_file, f"Kopiere {source_spec} -> {target_spec}")
    exit_code = 1
    for attempt in range(1, COPY_RETRY_ATTEMPTS + 1):
        if attempt > 1:
            print(f"\nWiederhole fehlgeschlagene Dateien (Versuch {attempt}/{COPY_RETRY_ATTEMPTS}) fuer {source_spec} -> {target_spec}...")
            log_manifest(log_file, f"Retry {attempt}/{COPY_RETRY_ATTEMPTS}: {source_spec} -> {target_spec}")
        exit_code = run(cmd, env, display_cmd=display_cmd)
        if exit_code == 0:
            break
    return exit_code


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Kopiert Daten zwischen OneDrive-Accounts und/oder SharePoint-Sites via rclone"
    )
    parser.add_argument(
        "--ca-cert-bundle",
        default=None,
        help="Pfad zum CA-Bundle (PEM) eines TLS-inspizierenden Firmen-Proxys/Firewall (z.B. Cato, Zscaler), falls keine Bypass-Regel fuer die MS-Login/Graph/SharePoint-Domains existiert.",
    )
    parser.add_argument("--transfers", type=int, default=TRANSFERS)
    parser.add_argument("--checkers", type=int, default=CHECKERS)
    args = parser.parse_args()

    work_dir = Path.home() / "Claude" / "OneDriveCopy"
    log_dir = Path("C:/Logs") if platform.system() == "Windows" else Path.home() / "Logs"
    work_dir.mkdir(parents=True, exist_ok=True)
    log_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    # Config-Datei je Lauf eindeutig benennen (Timestamp + PID): ein fester,
    # gemeinsamer Pfad wuerde bei zwei gleichzeitig laufenden Aufrufen (z.B. weil
    # ein vorheriger Lauf noch nicht fertig ist) sich gegenseitig ueberschreiben.
    config_path = work_dir / f"rclone_{timestamp}_{os.getpid()}.conf"
    log_file = log_dir / f"copy_{timestamp}.log"

    if not find_rclone():
        install_rclone()

    env = os.environ.copy()
    env["RCLONE_CONFIG"] = str(config_path)

    if args.ca_cert_bundle:
        env["RCLONE_CA_CERT"] = args.ca_cert_bundle

    # Alle Parameter zuerst anzeigen, bevor die interaktiven Abfragen (Quelle/
    # Ziel/Umfang) beginnen - damit von Anfang an klar ist, mit welcher
    # Konfiguration dieser Lauf tatsaechlich arbeitet.
    print("\n=== Parameter ===")
    print(f"CA-Cert-Bundle: {args.ca_cert_bundle or 'keiner (bei TLS-Inspection durch einen Firmen-Proxy/Firewall ggf. noetig)'}")
    print(f"Transfers: {args.transfers}")
    print(f"Checkers: {args.checkers}")
    rclone_display = "im Programm eingebettet" if RCLONE_BIN != "rclone" else find_rclone()
    print(f"rclone: {rclone_display}")
    print(f"Config-Verzeichnis: {work_dir}")
    print(f"Log-Verzeichnis: {log_dir}")
    print(f"Log-Datei (dieser Lauf): {log_file}")
    if not args.ca_cert_bundle:
        print(
            "Hinweis: Falls TLS-Inspection durch einen Firmen-Proxy/Firewall (z.B. Cato, "
            "Zscaler) greift: entweder --ca-cert-bundle setzen oder Bypass fuer "
            "login.microsoftonline.com / login.live.com / graph.microsoft.com / "
            "*.onedrive.com / *.sharepoint.com einrichten."
        )

    # --- Quelle ---
    source_info = resolve_endpoint(env, "Quelle", args.ca_cert_bundle, config_path)
    source_exit = create_onedrive_remote(
        "source", source_info["token"], source_info["drive_id"], source_info["drive_type"], config_path, env,
        extra_config={"disable_site_permission": "true"} if source_info["kind"] == "onedrive" else None,
    )
    if source_exit != 0:
        print(f"\nConfig fuer Quelle fehlgeschlagen (Exit Code {source_exit}).")
        config_path.unlink(missing_ok=True)
        sys.exit(source_exit)

    print("\nErmittle Inhalt der Quelle...")
    try:
        root_items = list_root_items(source_info["token"], source_info["drive_id"], args.ca_cert_bundle)
    except (urllib.error.URLError, KeyError, json.JSONDecodeError) as exc:
        print(f"\nKonnte Ordnerliste nicht ermitteln: {exc}")
        config_path.unlink(missing_ok=True)
        sys.exit(1)

    # --- Umfang: gesamter Inhalt oder ausgewaehlte Ordner ---
    print("\n=== Umfang ===")
    print("  a) Gesamten Inhalt kopieren")
    print("  o) Nur ausgewaehlte Ordner kopieren")
    while True:
        scope_choice = input("Auswahl (a/o): ").strip().lower()
        if scope_choice in ("a", "o"):
            break
        print("Ungueltige Eingabe - bitte 'a' oder 'o' eingeben.")

    selected_folders: list[str] | None = None
    exclude_names: list[str] = []
    if scope_choice == "o":
        selected_folders = prompt_folder_selection(root_items)
        print("Ausgewaehlt: " + ", ".join(selected_folders))
    else:
        vault_names = [item["name"] for item in root_items if item["is_locked_vault"]]
        if vault_names:
            print("\nFolgende Eintraege sind per API nicht zugaenglich (z.B. Persoenlicher Tresor) und werden automatisch uebersprungen:")
            for name in vault_names:
                print(f"  - {name}")
            exclude_names += vault_names

        foreign_names = [item["name"] for item in root_items if item["is_foreign"]]
        if foreign_names:
            print("\nFolgende Eintraege sind Verknuepfungen zu Inhalten aus einem ANDEREN Konto/einer anderen Site:")
            for name in foreign_names:
                print(f"  - {name}")
            answer = input("Diese von der Migration ausschliessen? (j/n, Standard j): ").strip().lower()
            if answer != "n":
                exclude_names += foreign_names
        elif not vault_names:
            print("Keine Verknuepfungen zu fremden Konten/Sites im Root gefunden.")

    # --- Ziel ---
    target_info = resolve_endpoint(env, "Ziel", args.ca_cert_bundle, config_path)
    target_exit = create_onedrive_remote(
        "target", target_info["token"], target_info["drive_id"], target_info["drive_type"], config_path, env,
    )
    if target_exit != 0:
        print(f"\nConfig fuer Ziel fehlgeschlagen (Exit Code {target_exit}).")
        config_path.unlink(missing_ok=True)
        sys.exit(target_exit)

    target_subfolder = input("\nZiel-Unterordner (leer = Root des Ziels): ").strip().strip("/")

    # EIN ausgewaehlter Ordner (oder "gesamter Inhalt") -> Inhalt direkt ins Ziel
    # (kein zusaetzlicher gleichnamiger Unterordner). MEHRERE ausgewaehlte
    # Ordner -> je ein gleichnamiger Unterordner, damit sich die Inhalte nicht
    # vermischen.
    if selected_folders is None:
        copy_pairs = [("source:", f"target:{target_subfolder}" if target_subfolder else "target:")]
    elif len(selected_folders) == 1:
        copy_pairs = [(f"source:{selected_folders[0]}", f"target:{target_subfolder}" if target_subfolder else "target:")]
    else:
        copy_pairs = []
        for folder in selected_folders:
            target_path = f"{target_subfolder}/{folder}" if target_subfolder else folder
            copy_pairs.append((f"source:{folder}", f"target:{target_path}"))

    exclude_args: list[str] = []
    for name in exclude_names:
        exclude_args += ["--exclude", f"{name}/**"]

    # SharePoint modifiziert Office-Dateien (.docx/.xlsx/...) serverseitig kurz
    # nach dem Upload (z.B. eingebettete Kompatibilitaets-Metadaten), wodurch
    # sich Groesse UND Pruefsumme aendern. rclone haelt das faelschlicherweise
    # fuer eine fehlgeschlagene Uebertragung ("corrupted on transfer: sizes/
    # hashes differ") und LOESCHT die gerade hochgeladene (tatsaechlich
    # intakte) Datei wieder - und wiederholt das bei jedem Retry erneut, weil
    # SharePoint die Datei bei jedem erneuten Versuch wieder anders veraendert
    # (nie identisch zur Quelle). --ignore-size/--ignore-checksum sind hier
    # absichtlich gesetzt, um diesen bekannten SharePoint-Effekt nicht mit
    # echter Uebertragungskorruption zu verwechseln.
    copy_extra_args = list(exclude_args)
    if target_info["kind"] == "sharepoint":
        copy_extra_args += ["--ignore-size", "--ignore-checksum"]

    print("\nVerbindung zum Ziel pruefen...")
    connectivity_exit = run([RCLONE_BIN, "lsd", "target:", "--config", str(config_path), "--max-depth", "1"], env)
    if connectivity_exit != 0:
        print(f"\nKonnte Ziel nicht auflisten (Exit Code {connectivity_exit}) - Abbruch.")
        config_path.unlink(missing_ok=True)
        sys.exit(connectivity_exit)

    # --- Zusammenfassung, direkt vor dem eigentlichen Kopieren ---
    print("\n=== Zusammenfassung ===")
    summary_lines = [
        f"Quelle: {source_info['identity']}",
        f"Ziel: {target_info['identity']}",
        f"Umfang: {'gesamter Inhalt' if selected_folders is None else 'ausgewaehlte Ordner: ' + ', '.join(selected_folders)}",
        f"Exclusion: {', '.join(f'{n}/**' for n in exclude_names) if exclude_names else 'keine'}",
    ]
    if target_info["kind"] == "sharepoint":
        summary_lines.append(
            "Hinweis: Ziel ist SharePoint - Groessen- und Pruefsummenpruefung nach Upload "
            "sind deaktiviert (--ignore-size, --ignore-checksum), da SharePoint Office-"
            "Dateien serverseitig veraendert."
        )
    summary_lines.append("Geplante Kopiervorgaenge:")
    summary_lines += [f"  {src} -> {tgt}" for src, tgt in copy_pairs]
    for line in summary_lines:
        print(line)
        log_manifest(log_file, line)

    print("\n=== Kopiervorgang ===")
    overall_copy_exit = 0
    for src, tgt in copy_pairs:
        exit_code = run_copy_with_retry(src, tgt, config_path, log_file, env, args, extra_args=copy_extra_args)
        if exit_code != 0:
            print(f"\n'{src}' -> '{tgt}' blieb nach {COPY_RETRY_ATTEMPTS} Versuchen fehlerhaft (Exit Code {exit_code}).")
            overall_copy_exit = exit_code

    if overall_copy_exit == 0:
        print("\n=== Verifikation (Pruefsummenvergleich) ===")
        # --ignore-size auch hier, sonst meldet 'rclone check' fuer SharePoint-Ziele
        # dieselben serverseitig verursachten Groessenabweichungen als Fehler.
        check_extra_args = ["--ignore-size"] if target_info["kind"] == "sharepoint" else []
        overall_check_exit = 0
        for src, tgt in copy_pairs:
            check_exit = run(
                [
                    RCLONE_BIN, "check", src, tgt,
                    "--config", str(config_path),
                    "--checkers", str(args.checkers),
                    "--log-file", str(log_file),
                    "--log-level", "INFO",
                    *check_extra_args,
                ],
                env,
            )
            if check_exit != 0:
                overall_check_exit = check_exit

        if overall_check_exit == 0:
            print("\nAlles vollstaendig kopiert und verifiziert.")
        else:
            print(f"\nVerifikation hat Abweichungen gefunden - Details im Log: {log_file}")
            print("Script erneut starten, um abweichende/fehlende Dateien nachzukopieren und erneut zu pruefen.")
    else:
        print(f"\nMindestens ein Kopiervorgang blieb fehlerhaft. Details im Log: {log_file}")
        print("Script erneut starten - rclone kopiert nur das nach, was noch fehlt.")

    config_path.unlink(missing_ok=True)
    print("\nrclone.conf mit Zugangsdaten geloescht - naechster Lauf erfordert wieder interaktiven Login.")

    error_file, error_count = extract_error_lines(log_file)
    print(f"\nLog-Datei: {log_file}")
    print(f"Fehlerzeilen im Log: {error_count} (extrahiert nach {error_file})")
    open_in_viewer(log_file)
    if error_count:
        open_in_viewer(error_file)

    sys.exit(overall_copy_exit)


if __name__ == "__main__":
    main()

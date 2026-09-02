"""Reine Logik-Schicht (kein print()/input()) - wird sowohl von migration_cli.py
(bestehende Terminal-Oberflaeche) als auch von migration_gui/ (neue grafische
Oberflaeche) genutzt. Enthaelt Microsoft-Graph-Zugriffe, den dauerhaften
Konten-Speicher (accounts.conf), Pfad-/Hash-Helfer und rclone-Kommandozeilen-
Bausteine, die beide Oberflaechen identisch brauchen."""

import configparser
import csv
import json
import os
import platform
import re
import shutil
import ssl
import subprocess
import sys
import threading
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
from pathlib import Path

TRANSFERS = 8
CHECKERS = 16
COPY_RETRY_ATTEMPTS = 3
GRAPH_ROOT = "https://graph.microsoft.com/v1.0"


class ToolError(Exception):
    """Ersetzt die verstreuten sys.exit()-Aufrufe der urspruenglichen
    CLI-Orchestrierung. Ein sys.exit() in einem GUI-Hintergrund-Thread wuerde
    nur diesen Thread lautlos beenden, ohne dass der Nutzer je einen Fehler zu
    sehen bekommt - ein raise ToolError(...) kann dagegen von jedem Aufrufer
    (CLI-main() genauso wie ein GUI-Callback) sauber abgefangen werden.
    'code' entspricht dem bisherigen Prozess-Exit-Code, damit main() weiterhin
    exakt denselben sys.exit(code) wie vorher machen kann."""

    def __init__(self, message: str, code: int = 1):
        super().__init__(message)
        self.code = code


def _legacy_work_dir() -> Path:
    """Fruehere Logik (Ordner neben der laufenden Binary/dem Skript) - wird
    nur noch fuer die einmalige Migration einer schon vorhandenen
    accounts.conf in _resolve_work_dir() gebraucht, siehe dort."""
    if getattr(sys, "frozen", False):
        exe_path = Path(sys.executable).resolve()
        if ".app/Contents/MacOS" in str(exe_path):
            return exe_path.parent.parent.parent.parent
        return exe_path.parent
    return Path(__file__).resolve().parent


def _default_app_data_dir() -> Path:
    system = platform.system()
    if system == "Windows":
        base = Path(os.environ.get("APPDATA") or Path.home())
    elif system == "Darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_DATA_HOME") or (Path.home() / ".local" / "share"))
    return base / "OneDrive-SharePoint-Migration-Tool"


def _resolve_work_dir() -> Path:
    """Arbeitsverzeichnis (accounts.conf, rclone-Temp-Configs, Logs) liegt in
    einem STABILEN, betriebssystem-ueblichen Nutzerprofil-Verzeichnis (Windows:
    %APPDATA%, macOS: ~/Library/Application Support) - NICHT mehr relativ zur
    laufenden Binary/dem Skript wie zuvor. Der binary-relative Ansatz brach auf
    zwei Arten: einmal beim Verschieben des Projektordners (accounts.conf am
    alten Pfad blieb unsichtbar - der urspruengliche Grund fuer den
    binary-relativen Ansatz ueberhaupt), und - schwerwiegender, real
    beobachtet - unter macOS' "App Translocation": eine aus dem
    Download-/Entpack-Ordner heraus gestartete .app (ohne vorher per Finder
    verschoben zu werden) laeuft dort aus einem VERSTECKTEN, bei JEDEM Start
    ZUFAELLIGEN, SCHREIBGESCHUETZTEN Temp-Pfad - accounts.conf liess sich
    dort weder speichern (schreibgeschuetzt) noch je wiederfinden (naechster
    Start = neuer Zufallspfad). Ein Verzeichnis im Nutzerprofil ist von
    alledem unabhaengig, unabhaengig davon, von wo/wie die App gestartet wird.
    """
    work_dir = _default_app_data_dir()
    accounts_path = work_dir / "accounts.conf"
    if not accounts_path.exists():
        # Einmalige Migration: existiert noch eine accounts.conf am alten,
        # binary-relativen Pfad (von vor diesem Umstieg, oder aus einem
        # regulaeren - nicht translozierten - Lauf), wird sie automatisch
        # uebernommen, damit bereits gespeicherte Konten nicht verloren gehen.
        # Best effort - darf den Start nie verhindern.
        try:
            legacy_accounts = _legacy_work_dir() / "accounts.conf"
            if legacy_accounts.exists():
                work_dir.mkdir(parents=True, exist_ok=True)
                shutil.copy2(legacy_accounts, accounts_path)
        except OSError:
            pass
    return work_dir


WORK_DIR = _resolve_work_dir()
ACCOUNTS_CONFIG_PATH = WORK_DIR / "accounts.conf"
DESKTOP_DIR = Path.home() / "Desktop"

# Schuetzt alle Lese-/Schreibzugriffe auf accounts.conf gegen gleichzeitige
# Zugriffe (die GUI kann Konten aus dem Hauptthread lesen, waehrend ein
# Hintergrund-Thread gerade ein Token zurueckschreibt - configparser macht
# dabei kein atomares Read-Modify-Write). Fuer die weiterhin single-threaded
# CLI ist das ein No-Op-Kosten.
_ACCOUNTS_LOCK = threading.Lock()


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


def open_in_viewer(path) -> None:
    """Oeffnet eine Datei mit der Standardanwendung des Betriebssystems."""
    system = platform.system()
    try:
        if system == "Darwin":
            subprocess.run(["open", str(path)], check=False)
        elif system == "Windows":
            os.startfile(str(path))  # type: ignore[attr-defined]
        else:
            subprocess.run(["xdg-open", str(path)], check=False)
    except OSError:
        pass


def log_manifest(log_file: Path, message: str) -> None:
    """Schreibt eine gut sichtbare Zeile direkt in die Log-Datei, zusaetzlich zu
    rclones eigenen (dateibezogenen) Log-Zeilen."""
    import datetime
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


# ============================================================
# Dauerhafter, benannter Konten-Speicher (accounts.conf)
# ============================================================

def list_saved_accounts() -> list[str]:
    """Namen aller dauerhaft gespeicherten Konten (leer, falls noch keine
    accounts.conf existiert)."""
    with _ACCOUNTS_LOCK:
        if not ACCOUNTS_CONFIG_PATH.exists():
            return []
        parser = configparser.ConfigParser()
        parser.read(ACCOUNTS_CONFIG_PATH)
        return parser.sections()


def load_saved_account(name: str) -> dict:
    with _ACCOUNTS_LOCK:
        parser = configparser.ConfigParser()
        parser.read(ACCOUNTS_CONFIG_PATH)
        return dict(parser[name])


def save_account(name: str, token_json: str, drive_id: str, drive_type: str, extra_config: dict[str, str] | None = None) -> None:
    """Speichert/aktualisiert ein Konto dauerhaft in accounts.conf. Restriktive
    Dateirechte (nur Besitzer), da langlebige Zugangsdaten fuer ggf. mehrere
    Personen darin liegen - diese Datei darf NIE ins Git-Repo (siehe
    .gitignore) oder sonst irgendwo geteilt werden."""
    with _ACCOUNTS_LOCK:
        WORK_DIR.mkdir(parents=True, exist_ok=True)
        parser = configparser.ConfigParser()
        if ACCOUNTS_CONFIG_PATH.exists():
            parser.read(ACCOUNTS_CONFIG_PATH)
        if name not in parser:
            parser.add_section(name)
        parser[name]["type"] = "onedrive"
        parser[name]["region"] = "global"
        parser[name]["token"] = token_json
        parser[name]["drive_id"] = drive_id
        parser[name]["drive_type"] = drive_type
        for key, value in (extra_config or {}).items():
            parser[name][key] = value
        with open(ACCOUNTS_CONFIG_PATH, "w") as f:
            parser.write(f)
        try:
            os.chmod(ACCOUNTS_CONFIG_PATH, 0o600)
        except OSError:
            pass


def sync_account_token(account_name: str, remote_name: str, config_path: Path) -> None:
    """Schreibt das (waehrend des Laufs von rclone ggf. automatisch erneuerte)
    Token aus der temporaeren Lauf-Config zurueck in die dauerhafte
    accounts.conf - manche OAuth-Provider machen Refresh-Tokens nach Gebrauch
    ungueltig (rotating refresh tokens), ohne diesen Sync wuerde ein
    gespeichertes Konto dann nur noch einmal funktionieren."""
    if not account_name:
        return
    run_parser = configparser.ConfigParser()
    run_parser.read(config_path)
    if remote_name not in run_parser or "token" not in run_parser[remote_name]:
        return
    current_token = run_parser[remote_name]["token"]

    with _ACCOUNTS_LOCK:
        accounts_parser = configparser.ConfigParser()
        if ACCOUNTS_CONFIG_PATH.exists():
            accounts_parser.read(ACCOUNTS_CONFIG_PATH)
        if account_name not in accounts_parser:
            return
        accounts_parser[account_name]["token"] = current_token
        with open(ACCOUNTS_CONFIG_PATH, "w") as f:
            accounts_parser.write(f)
        try:
            os.chmod(ACCOUNTS_CONFIG_PATH, 0o600)
        except OSError:
            pass


def account_kind(account: dict) -> str:
    """Leitet aus dem gespeicherten drive_type ab, ob ein Konto ein
    OneDrive- oder ein SharePoint-Konto ist - dieselbe Regel wie ueberall
    sonst im Programm (documentLibrary = SharePoint, alles andere OneDrive)."""
    return "sharepoint" if account.get("drive_type") == "documentLibrary" else "onedrive"


def suggest_account_name(kind: str, drive_type: str, display_name: str) -> str:
    if kind == "sharepoint":
        return f"{display_name} (SharePoint)"
    type_label = {"personal": "Personal", "business": "Business"}.get(drive_type, drive_type)
    return f"{display_name} ({type_label})"


# ============================================================
# Microsoft Graph
# ============================================================

def _graph_fetch(url: str, access_token: str, ca_cert_bundle: str | None, retries: int = 3, on_retry=None) -> dict:
    """Fuehrt einen einzelnen Microsoft-Graph-GET-Aufruf aus, mit ein paar
    automatischen Wiederholungen bei transienten Netzwerkfehlern (Timeout,
    Verbindungsabbruch) - kommt vor allem bei langsamen/gefilterten
    Firmennetzwerken vor und liess das Programm sonst mit einem rohen
    Python-Traceback abstuerzen statt es freundlich zu melden oder einfach
    nochmal zu versuchen. Wirft nach Ausschoepfen der Versuche den letzten
    Fehler weiter (Aufrufer faengt TimeoutError/URLError weiterhin ab).
    on_retry(attempt, retries, exc), falls angegeben, wird statt eines
    print() bei jedem Fehlversuch aufgerufen (CLI druckt, GUI aktualisiert
    einen Status)."""
    context = ssl.create_default_context(cafile=ca_cert_bundle) if ca_cert_bundle else None
    request = urllib.request.Request(url, headers={"Authorization": f"Bearer {access_token}"})
    last_exc: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            with urllib.request.urlopen(request, timeout=30, context=context) as response:
                return json.loads(response.read())
        except (TimeoutError, urllib.error.URLError) as exc:
            last_exc = exc
            if attempt < retries and on_retry is not None:
                on_retry(attempt, retries, exc)
    raise last_exc


def graph_get(url: str, token_json: str, ca_cert_bundle: str | None, on_retry=None) -> dict:
    access_token = json.loads(token_json)["access_token"]
    return _graph_fetch(url, access_token, ca_cert_bundle, on_retry=on_retry)


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
    own_drive_id_lower = drive_id.lower()
    site_url_re = re.compile(r"/personal/([0-9a-zA-Z]+)")
    url = f"{GRAPH_ROOT}/drives/{drive_id}/root/children?$select=name,folder,remoteItem&$top=200"
    items: list[dict] = []
    while url:
        page = _graph_fetch(url, access_token, ca_cert_bundle)
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


def search_sharepoint_sites(query: str, token_json: str, ca_cert_bundle: str | None) -> list[dict]:
    """Sucht SharePoint-Sites ueber Microsoft Graph 'GET /sites?search={query}'.
    '*' ist die uebliche Konvention, um moeglichst alle fuer den Account
    sichtbaren Sites zurueckzubekommen (statt einer echten Volltextsuche)."""
    encoded_query = urllib.parse.quote(query if query else "*")
    data = graph_get(f"{GRAPH_ROOT}/sites?search={encoded_query}", token_json, ca_cert_bundle)
    return data.get("value", [])


def list_local_root_items(path: str) -> list[dict]:
    """Listet die Eintraege im Root eines lokalen Pfads - im selben Format wie
    list_root_items(), damit die Ordnerauswahl-/Ausschluss-Logik unveraendert
    wiederverwendet werden kann. is_foreign/is_locked_vault gibt es lokal
    nicht, bleiben also immer False."""
    items: list[dict] = []
    for entry in sorted(Path(path).iterdir()):
        items.append({
            "name": entry.name,
            "is_folder": entry.is_dir(),
            "is_foreign": False,
            "is_locked_vault": False,
        })
    return items


def join_endpoint_path(base: str, subpath: str) -> str:
    """Haengt einen relativen Unterpfad an eine rclone-Remote ('name:') ODER
    einen rohen lokalen Pfad an - je nachdem, ob 'base' mit ':' endet."""
    if not subpath:
        return base
    if base.endswith(":"):
        return f"{base}{subpath}"
    return f"{base.rstrip('/')}/{subpath}"


def build_copy_argv(
    source_spec: str,
    target_spec: str,
    config_path: Path,
    log_file: Path,
    transfers: int,
    checkers: int,
    progress_args: list[str] | None = None,
) -> list[str]:
    """Baut die gemeinsame Basis der 'rclone copy'-Kommandozeile OHNE
    Exclude-/Ignore-Flags (die haengt der Aufrufer separat an, siehe
    run_copy_with_retry) - CLI und GUI nutzen exakt dieselben Kern-Flags und
    unterscheiden sich nur in progress_args: die CLI uebergibt ['--progress']
    (menschenlesbare Live-Anzeige im Terminal), die GUI uebergibt ['--rc',
    '--rc-addr', '127.0.0.1:<port>', '--rc-no-auth'] (strukturierte Abfrage
    per HTTP statt Text-Parsing). RCLONE_BIN wird bewusst NICHT hier
    vorangestellt - der Aufrufer entscheidet, ob/wie das Kommando angezeigt
    wird."""
    progress_args = progress_args if progress_args is not None else ["--progress"]
    return [
        "copy", source_spec, target_spec,
        "--config", str(config_path),
        "--transfers", str(transfers),
        "--checkers", str(checkers),
        "--checksum",
        "--retries", "5",
        "--low-level-retries", "10",
        *progress_args,
        "--log-file", str(log_file),
        "--log-level", "INFO",
    ]


# ============================================================
# Duplikat-Erkennung (Pfad-/Hash-Helfer)
# ============================================================

def primary_hash(record: dict) -> str | None:
    """Waehlt einen Hash-Typ als Vergleichsbasis. quickxor ist OneDrives
    nativer Hash-Typ und wird bevorzugt; als Fallback wird irgendein anderer
    vom Backend gelieferter Hash-Typ verwendet (falls quickxor fehlt)."""
    hashes = record.get("Hashes") or {}
    if "quickxor" in hashes:
        return hashes["quickxor"]
    for value in hashes.values():
        return value
    return None


def slugify_for_filename(text: str) -> str:
    """Macht einen String dateinamen-sicher (Windows/macOS/Linux) - ersetzt
    verbotene/unhandliche Zeichen durch '_' und kappt Mehrfach-Unterstriche."""
    safe = re.sub(r'[<>:"/\\|?*]', "_", text)
    safe = re.sub(r"\s+", "_", safe.strip())
    safe = re.sub(r"_+", "_", safe)
    return safe.strip("_")


def _dedupe_row(category: str, group_id: int, f: dict, share_label: str) -> dict:
    return {
        "Kategorie": category,
        "Gruppe": group_id,
        "Konto_Share": share_label,
        "Hash": f["_hash"],
        "Dateiname": f["Name"],
        "Pfad": f["Path"],
        "Groesse": f.get("Size", ""),
        "Letzte_Aenderung": f.get("ModTime", ""),
    }


def build_report_rows(files: list[dict], own_label: str, foreign_names: set[str]) -> tuple[list[dict], int]:
    """Baut die drei Kategorien aus den Datei-Eintraegen. Eine Datei kann in
    mehreren Kategorien auftauchen (z.B. Teil eines sicheren Duplikat-Paars
    UND Teil einer Namenskollision mit einer dritten Datei) - das sind
    unterschiedliche, sich nicht ausschliessende Fragen an die Daten, kein
    striktes Partitionieren.

    own_label wird als 'Konto_Share' fuer Dateien im eigenen Speicher
    eingetragen; liegt eine Datei unterhalb eines Root-Ordners aus
    foreign_names (Verknuepfung zu einem Fremd-Konto/einer Fremd-Site), wird
    stattdessen dessen Name eingetragen - so ist pro Zeile direkt erkennbar,
    ob ein Duplikat im eigenen Konto liegt oder in einer verknuepften
    Freigabe. Gibt (rows, skipped_no_hash) zurueck - der Aufrufer entscheidet,
    wie/ob er die uebersprungene Anzahl anzeigt (statt hier selbst zu
    drucken)."""
    def share_label(path: str) -> str:
        top_level = path.split("/", 1)[0]
        return top_level if top_level in foreign_names else own_label

    by_name = defaultdict(list)
    by_hash = defaultdict(list)
    by_name_hash = defaultdict(list)
    skipped_no_hash = 0

    for f in files:
        h = primary_hash(f)
        if h is None:
            skipped_no_hash += 1
            continue
        f["_hash"] = h
        by_name[f["Name"]].append(f)
        by_hash[h].append(f)
        by_name_hash[(f["Name"], h)].append(f)

    rows: list[dict] = []
    group_id = 0

    for group in by_name_hash.values():
        if len(group) < 2:
            continue
        group_id += 1
        rows += [_dedupe_row("1_sicheres_duplikat", group_id, f, share_label(f["Path"])) for f in group]

    for group in by_name.values():
        if len({f["_hash"] for f in group}) < 2:
            continue
        group_id += 1
        rows += [_dedupe_row("2_nur_name_gleich", group_id, f, share_label(f["Path"])) for f in group]

    for group in by_hash.values():
        if len({f["Name"] for f in group}) < 2:
            continue
        group_id += 1
        rows += [_dedupe_row("3_nur_hash_gleich", group_id, f, share_label(f["Path"])) for f in group]

    return rows, skipped_no_hash


def write_dedupe_csv(rows: list[dict], output_path: str) -> None:
    fieldnames = ["Kategorie", "Gruppe", "Konto_Share", "Hash", "Dateiname", "Pfad", "Groesse", "Letzte_Aenderung"]
    # utf-8-sig: Excel unter Windows zeigt Umlaute ohne BOM sonst falsch an.
    with open(output_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


# ============================================================
# Loeschen aus einer hochgeladenen CSV-Liste (z.B. gefilterter Duplikate-Report)
# ============================================================

def read_delete_csv(csv_path: str) -> tuple[list[dict], int]:
    """Liest eine CSV im Format des Duplikate-Reports (write_dedupe_csv) und
    extrahiert eindeutige Dateipfade (Spalte 'Pfad') zum Loeschen - z.B. eine
    in Excel auf die zu loeschenden Zeilen gefilterte Kopie des Reports
    (etwa: alle .arw-Dateien, zu denen im selben Ordner eine .jpg mit
    gleichem Namen existiert). Gibt (rows, skipped) zurueck - rows enthaelt
    dicts mit 'path' und optional 'size' (aus der Spalte 'Groesse', falls
    vorhanden und numerisch), skipped zaehlt Zeilen ohne verwertbaren Pfad.
    Dedupliziert nach Pfad (eine Datei kann im Report in mehreren
    Kategorie-Zeilen auftauchen)."""
    seen: dict[str, dict] = {}
    skipped = 0
    with open(csv_path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            path = (row.get("Pfad") or "").strip()
            if not path:
                skipped += 1
                continue
            if path in seen:
                continue
            raw_size = (row.get("Groesse") or "").strip()
            seen[path] = {"path": path, "size": int(raw_size) if raw_size.isdigit() else None}
    return list(seen.values()), skipped


def delete_files_via_rclone(remote_name: str, paths: list[str], config_path: Path, env: dict) -> tuple[bool, str]:
    """Loescht die angegebenen Dateien (Pfade relativ zur Remote-Wurzel, wie
    sie im Duplikate-Report stehen) in einem Rutsch ueber 'rclone delete
    --files-from'. Landet bei OneDrive/SharePoint ueblicherweise im
    Papierkorb des Kontos (Microsoft Graph loescht standardmaessig dorthin,
    kein sofortiges endgueltiges Loeschen) - das ist aber ein Verhalten der
    jeweiligen Aufbewahrungsrichtlinie und keine harte Garantie dieses Tools.
    Gibt (erfolgreich, kombinierte rclone-Ausgabe) zurueck."""
    list_file = config_path.with_name(config_path.stem + "_delete_list.txt")
    list_file.write_text("\n".join(paths), encoding="utf-8")
    try:
        result = subprocess.run(
            [RCLONE_BIN, "delete", f"{remote_name}:", "--files-from", str(list_file), "--config", str(config_path)],
            env=env, capture_output=True, text=True,
        )
        return result.returncode == 0, (result.stdout + result.stderr)
    finally:
        list_file.unlink(missing_ok=True)

"""Terminal-Oberflaeche (input()/print()) - die urspruengliche, unveraendert
funktionierende CLI. Nutzt ausschliesslich migration_core fuer die eigentliche
Logik. Erreichbar per --cli."""

import configparser
import datetime
import json
import os
import platform
import subprocess
import sys
import urllib.error

from migration_core import *  # noqa: F401,F403 - interne Schwester-Datei, bewusst weit importiert (siehe migration_core.py)


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


def reauthenticate_account(name: str, env: dict) -> None:
    """Fuehrt fuer ein bereits gespeichertes Konto einen kompletten neuen
    OAuth-Login durch und ersetzt NUR das Token (Drive-ID/-Typ/sonstige
    Einstellungen bleiben unveraendert) - fuer den Fall, dass der
    Refresh-Token ungueltig geworden ist (z.B. Passwortaenderung, Widerruf
    durch einen Admin, laengere Inaktivitaet: 'HTTP Error 401: Unauthorized')."""
    account = load_saved_account(name)
    token = rclone_authorize_onedrive(env, f"Erneute Anmeldung: {name}")
    extra_config = {
        k: v for k, v in account.items()
        if k not in ("type", "region", "token", "drive_id", "drive_type")
    }
    save_account(name, token, account["drive_id"], account["drive_type"], extra_config)
    print(f"Konto '{name}' wurde neu angemeldet.")


def prompt_reauth_target(accounts: list[str]) -> str | None:
    print("\nWelches Konto neu anmelden?")
    for i, name in enumerate(accounts, start=1):
        print(f"  {i}. {name}")
    cancel_idx = len(accounts) + 1
    print(f"  {cancel_idx}. Abbrechen")
    while True:
        raw = input(f"Auswahl (1-{cancel_idx}): ").strip()
        try:
            idx = int(raw)
        except ValueError:
            print("Ungueltige Eingabe.")
            continue
        if 1 <= idx <= len(accounts):
            return accounts[idx - 1]
        if idx == cancel_idx:
            return None
        print("Nummer ausserhalb des Bereichs.")


def prompt_account_choice(label: str, env: dict, kind_filter: str) -> str | None:
    """Zeigt nur die gespeicherten Konten des passenden Typs (OneDrive ODER
    SharePoint, je nach vorher gewaehltem Endpunkt-Typ) zur Auswahl, plus
    'neue Anmeldung' und 'bestehendes Konto neu anmelden' (falls z.B. der
    Refresh-Token ungueltig geworden ist). Gibt den Namen des gewaehlten
    gespeicherten Kontos zurueck, oder None fuer eine neue Anmeldung."""
    while True:
        accounts = [
            name for name in list_saved_accounts()
            if account_kind(load_saved_account(name)) == kind_filter
        ]
        if not accounts:
            return None

        print(f"\n=== {label}: Konto waehlen ===")
        for i, name in enumerate(accounts, start=1):
            print(f"  {i}. {name}")
        new_login_idx = len(accounts) + 1
        reauth_idx = len(accounts) + 2
        print(f"  {new_login_idx}. Neue Anmeldung...")
        print(f"  {reauth_idx}. Bestehendes Konto neu anmelden (falls Token abgelaufen/ungueltig)...")
        raw = input(f"Auswahl (1-{reauth_idx}): ").strip()
        try:
            idx = int(raw)
        except ValueError:
            print("Ungueltige Eingabe.")
            continue
        if 1 <= idx <= len(accounts):
            return accounts[idx - 1]
        if idx == new_login_idx:
            return None
        if idx == reauth_idx:
            target = prompt_reauth_target(accounts)
            if target is not None:
                reauthenticate_account(target, env)
            continue
        print("Nummer ausserhalb des Bereichs.")


def prompt_and_save_account(suggested_name: str, token_json: str, drive_id: str, drive_type: str, extra_config: dict[str, str] | None = None) -> str | None:
    """Fragt, ob und unter welchem Namen ein neu angemeldetes Konto dauerhaft
    gespeichert werden soll. Gibt den gespeicherten Namen zurueck, oder None
    falls nicht gespeichert wurde (Konto funktioniert fuer diesen Lauf trotzdem
    normal, wird aber beim naechsten Start nicht zur Auswahl stehen)."""
    raw = input(f"Konto dauerhaft speichern als (Enter fuer '{suggested_name}', 'nein' zum Nicht-Speichern): ").strip()
    if raw.lower() in ("nein", "no", "n"):
        return None
    name = raw or suggested_name
    save_account(name, token_json, drive_id, drive_type, extra_config)
    print(f"Konto gespeichert als '{name}'.")
    return name


def rclone_authorize_onedrive(env: dict, label: str, on_event=None) -> str:
    """Eigenstaendiger OAuth-Login (rclone authorize) fuer OneDrive/SharePoint,
    ohne rclones eingebauten config-Wizard zu durchlaufen. label ist nur fuer die
    Konsolenausgabe. on_event(kind, text), falls angegeben, wird zusaetzlich bei
    semantischen Meilensteinen aufgerufen ("waiting"/"url_copied"/"success"/
    "failed") - fuer eine GUI, die eine einzelne Statuszeile statt roher
    Terminal-Zeilen zeigen will. Ohne on_event (Standardfall, CLI) unveraendert
    wie bisher: jede Zeile wird direkt gedruckt."""
    print(f"\n--- OAuth-Login: {label} ---")
    if on_event:
        on_event("waiting", f"Öffne Browser zum Anmelden ({label})...")
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
                if on_event:
                    on_event("url_copied", "Login-Link kopiert – im Browser einfügen und Anmeldung abschließen...")
    process.wait()

    output = "".join(output_lines)
    marker_start = "Paste the following into your remote machine --->"
    marker_end = "<---End paste"
    if process.returncode != 0 or marker_start not in output or marker_end not in output:
        print(f"\nOAuth-Login ({label}) fehlgeschlagen (Exit Code {process.returncode}), kein Token erhalten.")
        if on_event:
            on_event("failed", f"Anmeldung fehlgeschlagen (Exit Code {process.returncode}).")
        raise ToolError(f"OAuth-Login ({label}) fehlgeschlagen (Exit Code {process.returncode})", process.returncode or 1)

    if on_event:
        on_event("success", "Angemeldet.")
    return output.split(marker_start, 1)[1].split(marker_end, 1)[0].strip()


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
        raise ToolError("Keine gueltigen Ordner ausgewaehlt", 1)
    return selected


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
        except (TimeoutError, urllib.error.URLError, KeyError, json.JSONDecodeError) as exc:
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


def detect_root_exclusions(root_items: list[dict], ask_about_foreign: bool = True) -> list[str]:
    """Ermittelt automatisch auszuschliessende Root-Eintraege: der Persoenliche
    Tresor wird immer ausgeschlossen (per API grundsaetzlich nicht zugaenglich).
    Bei Verknuepfungen zu Inhalten aus einem ANDEREN Konto/einer anderen Site
    wird - falls ask_about_foreign gesetzt ist - gefragt (Standard: ausschliessen);
    ist es False, werden sie nur informativ aufgelistet und bleiben eingeschlossen
    (z.B. beim Duplikat-Scan gewuenscht, um Duplikate AUCH ueber Fremd-Shares
    hinweg zu finden). Gibt die tatsaechlich auszuschliessenden Namen zurueck -
    Aufrufer baut daraus '--exclude "<name>/**"'-Muster."""
    exclude_names: list[str] = []
    vault_names = [item["name"] for item in root_items if item["is_locked_vault"]]
    if vault_names:
        print("\nFolgende Eintraege sind per API nicht zugaenglich (z.B. Persoenlicher Tresor) und werden automatisch uebersprungen:")
        for name in vault_names:
            print(f"  - {name}")
        exclude_names += vault_names

    foreign_names = [item["name"] for item in root_items if item["is_foreign"]]
    if foreign_names and ask_about_foreign:
        print("\nFolgende Eintraege sind Verknuepfungen zu Inhalten aus einem ANDEREN Konto/einer anderen Site:")
        for name in foreign_names:
            print(f"  - {name}")
        answer = input("Diese ausschliessen? (j/n, Standard j): ").strip().lower()
        if answer != "n":
            exclude_names += foreign_names
    elif foreign_names:
        print("\nFolgende Eintraege sind Verknuepfungen zu Inhalten aus einem ANDEREN Konto/einer anderen Site - werden mit einbezogen:")
        for name in foreign_names:
            print(f"  - {name}")
    elif not vault_names:
        print("Keine Verknuepfungen zu fremden Konten/Sites im Root gefunden.")

    return exclude_names


def prompt_endpoint_type(label: str) -> str:
    print(f"\n=== {label} ===")
    print("  1. OneDrive")
    print("  2. SharePoint-Site")
    print("  3. Lokaler Pfad / Netzlaufwerk")
    while True:
        choice = input("Auswahl (1/2/3): ").strip()
        if choice in ("1", "2", "3"):
            return {"1": "onedrive", "2": "sharepoint", "3": "local"}[choice]
        print("Ungueltige Eingabe - bitte 1, 2 oder 3 eingeben.")


def prompt_local_path(label: str) -> str:
    """Fragt einen lokalen Pfad ab - deckt auch Netzlaufwerke ab, die bereits
    im Dateisystem eingebunden sind (macOS: /Volumes/..., Windows: Z:\\... oder
    \\\\server\\share): fuer rclone ist ein eingebundenes Netzlaufwerk technisch
    ein ganz normaler lokaler Pfad, kein eigener Remote-Typ noetig."""
    while True:
        raw = input(f"\nPfad fuer {label} (lokal oder bereits verbundenes Netzlaufwerk): ").strip().strip('"')
        if not raw:
            print("Bitte einen Pfad eingeben.")
            continue
        path = Path(raw).expanduser()
        if not path.exists():
            print(f"Hinweis: '{path}' existiert noch nicht - wird beim Kopieren ggf. automatisch angelegt.")
        return str(path)


def resolve_endpoint(env: dict, label: str, ca_cert_bundle: str | None, config_path: Path) -> dict:
    """Fragt zuerst ab, ob dieser Endpunkt OneDrive, eine SharePoint-Site oder
    ein lokaler Pfad/Netzlaufwerk ist. Bei OneDrive/SharePoint werden danach
    NUR die dazu passenden gespeicherten Konten angeboten; sonst erfolgt ein
    frischer Login mit Angebot, das neue Konto dauerhaft zu speichern.
    Liefert immer einen menschenlesbaren Bezeichner und (falls gespeichert
    bzw. lokal) den Kontonamen/Pfad zurueck."""
    endpoint_type = prompt_endpoint_type(label)
    if endpoint_type == "local":
        path = prompt_local_path(label)
        return {
            "kind": "local",
            "path": path,
            "identity": f"Lokal/Netzlaufwerk ({path})",
            "account_name": None,
        }

    chosen = prompt_account_choice(label, env, endpoint_type)
    if chosen is not None:
        account = load_saved_account(chosen)
        drive_type = account.get("drive_type", "")
        kind = "sharepoint" if drive_type == "documentLibrary" else "onedrive"
        print(f"Verwende gespeichertes Konto: {chosen}")
        return {
            "token": account["token"],
            "drive_id": account["drive_id"],
            "drive_type": drive_type,
            "kind": kind,
            "identity": chosen,
            "account_name": chosen,
        }

    type_label = "OneDrive" if endpoint_type == "onedrive" else "SharePoint"
    token = rclone_authorize_onedrive(env, f"{label} ({type_label})")

    try:
        if endpoint_type == "onedrive":
            drive_id, drive_type, identity = fetch_own_drive(token, ca_cert_bundle)
            print(f"Gefundene Drive-ID: {drive_id} (Typ: {drive_type})")
            display_name = identity.split("<")[0].strip()
            suggested_name = suggest_account_name("onedrive", drive_type, display_name)
            account_name = prompt_and_save_account(
                suggested_name, token, drive_id, drive_type,
                extra_config={"disable_site_permission": "true"},
            )
            return {
                "token": token,
                "drive_id": drive_id,
                "drive_type": drive_type,
                "kind": "onedrive",
                "identity": f"OneDrive ({identity})",
                "account_name": account_name,
            }

        site = prompt_site_selection(token, ca_cert_bundle)
        site_name = site.get("displayName") or site.get("name") or "(ohne Namen)"
        print(f"Ausgewaehlte Site: {site_name} - {site.get('webUrl', '')}")
        drive = graph_get(f"{GRAPH_ROOT}/sites/{site['id']}/drive", token, ca_cert_bundle)
        drive_id = drive["id"]
        drive_type = drive.get("driveType", "documentLibrary")
        print(f"Gefundene Dokumentbibliothek-Drive-ID: {drive_id} (Typ: {drive_type})")
        suggested_name = suggest_account_name("sharepoint", drive_type, site_name)
        account_name = prompt_and_save_account(suggested_name, token, drive_id, drive_type)
        return {
            "token": token,
            "drive_id": drive_id,
            "drive_type": drive_type,
            "kind": "sharepoint",
            "identity": f"SharePoint-Site '{site_name}' ({site.get('webUrl', '')})",
            "account_name": account_name,
        }
    except (TimeoutError, urllib.error.URLError, KeyError, json.JSONDecodeError) as exc:
        print(f"\nKonnte {label} nicht ueber Microsoft Graph aufloesen: {exc}")
        config_path.unlink(missing_ok=True)
        raise ToolError(f"Konnte {label} nicht ueber Microsoft Graph aufloesen: {exc}", 1)


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


def refresh_remote_token(remote_name: str, config_path: Path, env: dict) -> str | None:
    """Erzwingt einen echten rclone-Aufruf gegen die angegebene Remote, damit
    rclone ein abgelaufenes Access-Token automatisch per Refresh-Token
    erneuert - rclone tut das nur bei tatsaechlicher Nutzung, nicht schon beim
    blossen Anlegen der Config (relevant vor allem bei gespeicherten Konten,
    deren Token schon eine Weile nicht mehr verwendet wurde). Direkte
    Microsoft-Graph-Aufrufe wie list_root_items() nutzen das Token roh, ohne
    rclones eigene Refresh-Logik - deshalb muss hier vorher einmal 'rclone
    lsd' laufen. Gibt das (ggf. erneuerte) Token aus der Lauf-Config zurueck,
    oder None wenn die Verbindung fehlschlaegt (z.B. Refresh-Token selbst
    ungueltig geworden)."""
    check = subprocess.run(
        [RCLONE_BIN, "lsd", f"{remote_name}:", "--config", str(config_path), "--max-depth", "1"],
        env=env, capture_output=True, text=True,
    )
    if check.returncode != 0:
        print(f"Verbindungstest fehlgeschlagen:\n{check.stderr.strip()}")
        return None
    parser = configparser.ConfigParser()
    parser.read(config_path)
    if remote_name not in parser or "token" not in parser[remote_name]:
        return None
    return parser[remote_name]["token"]


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
    cmd_options = build_copy_argv(source_spec, target_spec, config_path, log_file, args.transfers, args.checkers)
    cmd = [RCLONE_BIN, *cmd_options, *extra_args]
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


# ============================================================
# Werkzeug 1: Kopieren/Migrieren
# ============================================================

def run_copy_tool(args, env: dict, config_path: Path, log_file: Path) -> int:
    # --- Quelle ---
    source_info = resolve_endpoint(env, "Quelle", args.ca_cert_bundle, config_path)
    if source_info["kind"] == "local":
        print("\nErmittle Inhalt der Quelle...")
        try:
            root_items = list_local_root_items(source_info["path"])
        except OSError as exc:
            print(f"\nKonnte lokalen Pfad nicht lesen: {exc}")
            config_path.unlink(missing_ok=True)
            return 1
    else:
        source_exit = create_onedrive_remote(
            "source", source_info["token"], source_info["drive_id"], source_info["drive_type"], config_path, env,
            extra_config={"disable_site_permission": "true"} if source_info["kind"] == "onedrive" else None,
        )
        if source_exit != 0:
            print(f"\nConfig fuer Quelle fehlgeschlagen (Exit Code {source_exit}).")
            config_path.unlink(missing_ok=True)
            return source_exit

        # Erzwingt eine Token-Erneuerung ueber rclone, BEVOR das Token direkt (ohne
        # rclones eigene Refresh-Logik) fuer die folgenden Microsoft-Graph-Aufrufe
        # verwendet wird - relevant vor allem bei laenger nicht genutzten
        # gespeicherten Konten, deren Access-Token abgelaufen ist.
        refreshed_token = refresh_remote_token("source", config_path, env)
        if refreshed_token is None:
            print("\nVerbindung zur Quelle fehlgeschlagen - Token evtl. abgelaufen/ungueltig. "
                  "Neu starten und beim Konto 'Bestehendes Konto neu anmelden' waehlen.")
            config_path.unlink(missing_ok=True)
            return 1
        source_info["token"] = refreshed_token

        print("\nErmittle Inhalt der Quelle...")
        try:
            root_items = list_root_items(source_info["token"], source_info["drive_id"], args.ca_cert_bundle)
        except (TimeoutError, urllib.error.URLError, KeyError, json.JSONDecodeError) as exc:
            print(f"\nKonnte Ordnerliste nicht ermitteln: {exc}")
            config_path.unlink(missing_ok=True)
            return 1

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
        exclude_names = detect_root_exclusions(root_items)

    # --- Ziel ---
    target_info = resolve_endpoint(env, "Ziel", args.ca_cert_bundle, config_path)
    # SharePoint UND OneDrive for Business erzeugen bei JEDER "aendernden"
    # Operation (Upload/Ueberschreiben, aber auch nur das Setzen der
    # Modification-Time) eine neue Dateiversion, die den Speicherplatz zaehlt
    # - anders als OneDrive Personal, das das nicht tut. rclones onedrive-
    # Backend hat dafuer den dokumentierten Remote-Parameter 'no_versions':
    # damit werden ueberzaehlige Versionen nach jeder aendernden Operation
    # automatisch wieder entfernt (kostet zusaetzliche API-Aufrufe, daher nur
    # fuer diese Zielarten gesetzt). Real beobachtet: 490 GiB unnoetige
    # Versionshistorie bei einem mehrfach wiederholten Migrationslauf gegen
    # ein OneDrive-Business-Ziel, wo JEDE Datei bei jedem erneuten Lauf eine
    # neue (inhaltlich IDENTISCHE) Version bekam. Laut rclone-Doku darf
    # 'no_versions' NICHT gegen OneDrive Personal gesetzt werden ("Onedrive
    # personal can't currently delete versions") - deshalb an dieselbe
    # Bedingung wie needs_ignore_flags gekoppelt.
    needs_ignore_flags = target_info["kind"] == "sharepoint" or target_info.get("drive_type") == "business"
    if target_info["kind"] != "local":
        target_exit = create_onedrive_remote(
            "target", target_info["token"], target_info["drive_id"], target_info["drive_type"], config_path, env,
            extra_config={"no_versions": "true"} if needs_ignore_flags else None,
        )
        if target_exit != 0:
            print(f"\nConfig fuer Ziel fehlgeschlagen (Exit Code {target_exit}).")
            config_path.unlink(missing_ok=True)
            return target_exit

    # source_base/target_base sind entweder die rclone-Remote-Namen ("source:"/
    # "target:") oder - bei lokalen Pfaden/Netzlaufwerken - der rohe Dateisystem-
    # pfad. rclone behandelt einen bereits eingebundenen lokalen Pfad wie jeden
    # anderen Remote, es ist also kein eigener Config-Eintrag noetig.
    source_base = source_info["path"] if source_info["kind"] == "local" else "source:"
    target_base = target_info["path"] if target_info["kind"] == "local" else "target:"

    target_subfolder = input("\nZiel-Unterordner (leer = Root des Ziels): ").strip().strip("/")

    # EIN ausgewaehlter Ordner (oder "gesamter Inhalt") -> Inhalt direkt ins Ziel
    # (kein zusaetzlicher gleichnamiger Unterordner). MEHRERE ausgewaehlte
    # Ordner -> je ein gleichnamiger Unterordner, damit sich die Inhalte nicht
    # vermischen.
    if selected_folders is None:
        copy_pairs = [(source_base, join_endpoint_path(target_base, target_subfolder))]
    elif len(selected_folders) == 1:
        copy_pairs = [(join_endpoint_path(source_base, selected_folders[0]), join_endpoint_path(target_base, target_subfolder))]
    else:
        copy_pairs = []
        for folder in selected_folders:
            target_path = f"{target_subfolder}/{folder}" if target_subfolder else folder
            copy_pairs.append((join_endpoint_path(source_base, folder), join_endpoint_path(target_base, target_path)))

    exclude_args: list[str] = []
    for name in exclude_names:
        exclude_args += ["--exclude", f"{name}/**"]

    # SharePoint modifiziert Office-Dateien (.docx/.xlsx/...) serverseitig kurz
    # nach dem Upload (z.B. eingebettete Kompatibilitaets-Metadaten), wodurch
    # sich Groesse UND Pruefsumme aendern. rclone haelt das faelschlicherweise
    # fuer eine fehlgeschlagene Uebertragung ("corrupted on transfer: sizes/
    # hashes differ") und LOESCHT die gerade hochgeladene (tatsaechlich
    # intakte) Datei wieder. --ignore-size/--ignore-checksum sind hier
    # absichtlich gesetzt, um diesen Effekt nicht mit echter
    # Uebertragungskorruption zu verwechseln (needs_ignore_flags wurde bereits
    # weiter oben berechnet, siehe Kommentar dort zu 'no_versions').
    copy_extra_args = list(exclude_args)
    if needs_ignore_flags:
        copy_extra_args += ["--ignore-size", "--ignore-checksum"]

    if target_info["kind"] == "local":
        try:
            Path(target_info["path"]).mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            print(f"\nKonnte Ziel-Pfad nicht anlegen/beschreiben: {exc} - Abbruch.")
            config_path.unlink(missing_ok=True)
            return 1
    else:
        print("\nVerbindung zum Ziel pruefen...")
        connectivity_exit = run([RCLONE_BIN, "lsd", "target:", "--config", str(config_path), "--max-depth", "1"], env)
        if connectivity_exit != 0:
            print(f"\nKonnte Ziel nicht auflisten (Exit Code {connectivity_exit}) - Abbruch.")
            config_path.unlink(missing_ok=True)
            return connectivity_exit

    # --- Zusammenfassung, direkt vor dem eigentlichen Kopieren ---
    print("\n=== Zusammenfassung ===")
    summary_lines = [
        f"Quelle: {source_info['identity']}",
        f"Ziel: {target_info['identity']}",
        f"Umfang: {'gesamter Inhalt' if selected_folders is None else 'ausgewaehlte Ordner: ' + ', '.join(selected_folders)}",
        f"Exclusion: {', '.join(f'{n}/**' for n in exclude_names) if exclude_names else 'keine'}",
    ]
    if needs_ignore_flags:
        summary_lines.append(
            "Hinweis: Ziel ist SharePoint bzw. OneDrive Business - Groessen- und "
            "Pruefsummenpruefung nach Upload sind deaktiviert (--ignore-size, "
            "--ignore-checksum), da diese Backends Dateien serverseitig veraendern."
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
        # --ignore-size auch hier, sonst meldet 'rclone check' fuer SharePoint-/
        # OneDrive-Business-Ziele dieselben serverseitig verursachten
        # Groessenabweichungen als Fehler.
        check_extra_args = ["--ignore-size"] if needs_ignore_flags else []
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

    if source_info.get("account_name"):
        sync_account_token(source_info["account_name"], "source", config_path)
    if target_info.get("account_name"):
        sync_account_token(target_info["account_name"], "target", config_path)

    config_path.unlink(missing_ok=True)
    print("\nrclone.conf (Lauf-Config) geloescht - dauerhaft gespeicherte Konten bleiben in accounts.conf erhalten.")

    error_file, error_count = extract_error_lines(log_file)
    print(f"\nLog-Datei: {log_file}")
    print(f"Fehlerzeilen im Log: {error_count} (extrahiert nach {error_file})")
    open_in_viewer(log_file)
    if error_count:
        open_in_viewer(error_file)

    return overall_copy_exit


# ============================================================
# Werkzeug 2: Duplikate finden
# ============================================================

def list_files_for_dedupe(remote: str, env: dict, excludes: list[str]) -> list[dict] | None:
    """Ruft 'rclone lsjson' rekursiv mit Hash-Angabe ab und gibt die
    geparsten Datei-Eintraege zurueck (Ordner werden per --files-only
    weggelassen). Gibt None bei Fehler zurueck (Aufrufer entscheidet ueber
    Abbruch)."""
    cmd = [RCLONE_BIN, "lsjson", remote, "-R", "--files-only", "--hash"]
    for pattern in excludes:
        cmd += ["--exclude", pattern]

    print(f"Frage '{remote}' ab (rclone lsjson -R --hash) - bei grossen Strukturen kann das einen Moment dauern...")
    result = subprocess.run(cmd, env=env, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"rclone lsjson fehlgeschlagen (Exit Code {result.returncode}):\n{result.stderr}")
        return None
    return json.loads(result.stdout)


def run_dedupe_tool(args, env: dict, config_path: Path, timestamp: str) -> int:
    scan_info = resolve_endpoint(env, "Zu durchsuchendes Konto", args.ca_cert_bundle, config_path)
    scan_exit = create_onedrive_remote(
        "scan", scan_info["token"], scan_info["drive_id"], scan_info["drive_type"], config_path, env,
        extra_config={"disable_site_permission": "true"} if scan_info["kind"] == "onedrive" else None,
    )
    if scan_exit != 0:
        print(f"\nConfig fehlgeschlagen (Exit Code {scan_exit}).")
        config_path.unlink(missing_ok=True)
        return scan_exit

    refreshed_token = refresh_remote_token("scan", config_path, env)
    if refreshed_token is None:
        print("\nVerbindung zum Konto fehlgeschlagen - Token evtl. abgelaufen/ungueltig. "
              "Neu starten und beim Konto 'Bestehendes Konto neu anmelden' waehlen.")
        config_path.unlink(missing_ok=True)
        return 1
    scan_info["token"] = refreshed_token
    own_label = scan_info.get("account_name") or scan_info["identity"]

    print("\nErmittle Inhalt des Kontos...")
    try:
        root_items = list_root_items(scan_info["token"], scan_info["drive_id"], args.ca_cert_bundle)
    except (TimeoutError, urllib.error.URLError, KeyError, json.JSONDecodeError) as exc:
        print(f"\nKonnte Ordnerliste nicht ermitteln: {exc}")
        config_path.unlink(missing_ok=True)
        return 1
    # Anders als beim Kopieren werden Verknuepfungen zu Fremd-Shares beim
    # Duplikat-Scan NICHT ausgeschlossen (ask_about_foreign=False) - Duplikate
    # sollen bewusst auch ueber geteilte OneDrive-/SharePoint-Verknuepfungen
    # hinweg gefunden werden. Der Persoenliche Tresor bleibt trotzdem
    # ausgeschlossen (per API ohnehin nicht zugaenglich).
    auto_exclude_patterns = [f"{name}/**" for name in detect_root_exclusions(root_items, ask_about_foreign=False)]

    default_output = str(DESKTOP_DIR / f"dedupe_report_{slugify_for_filename(own_label)}_{timestamp}.csv")
    output_path = input(f"\nCSV-Ausgabepfad (Enter fuer '{default_output}'): ").strip() or default_output
    raw_exclude = input("Weitere auszuschliessende Muster, kommagetrennt (Enter fuer keine, z.B. '_Archiv/**'): ").strip()
    excludes = auto_exclude_patterns + ([p.strip() for p in raw_exclude.split(",") if p.strip()] if raw_exclude else [])

    files = list_files_for_dedupe("scan:", env, excludes)
    if files is None:
        config_path.unlink(missing_ok=True)
        return 1
    print(f"{len(files)} Dateien gefunden.")

    foreign_names = {item["name"] for item in root_items if item["is_foreign"]}
    rows, skipped_no_hash = build_report_rows(files, own_label, foreign_names)
    if skipped_no_hash:
        print(f"Hinweis: {skipped_no_hash} Datei(en) ohne Hash vom Backend uebersprungen (kann nicht sicher verglichen werden).")
    write_dedupe_csv(rows, output_path)
    open_in_viewer(output_path)

    groups_by_category: dict[str, set[int]] = defaultdict(set)
    files_by_category: dict[str, int] = defaultdict(int)
    for row in rows:
        groups_by_category[row["Kategorie"]].add(row["Gruppe"])
        files_by_category[row["Kategorie"]] += 1

    print(f"\nReport geschrieben nach: {output_path}")
    for category, label in [
        ("1_sicheres_duplikat", "Sichere Duplikate"),
        ("2_nur_name_gleich", "Nur Name gleich"),
        ("3_nur_hash_gleich", "Nur Hash gleich"),
    ]:
        print(f"  {label}: {len(groups_by_category[category])} Gruppen ({files_by_category[category]} Dateien)")

    if scan_info.get("account_name"):
        sync_account_token(scan_info["account_name"], "scan", config_path)

    config_path.unlink(missing_ok=True)
    print("\nrclone.conf (Lauf-Config) geloescht - dauerhaft gespeicherte Konten bleiben in accounts.conf erhalten.")
    return 0


def main(args) -> None:
    """CLI-Einstiegspunkt - args kommt bereits geparst vom gemeinsamen
    Einstiegspunkt (onedrive-sharepoint-migration-tool.py)."""
    log_dir = Path("C:/Logs") if platform.system() == "Windows" else Path.home() / "Logs"
    WORK_DIR.mkdir(parents=True, exist_ok=True)
    log_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    # Config-Datei je Lauf eindeutig benennen (Timestamp + PID): ein fester,
    # gemeinsamer Pfad wuerde bei zwei gleichzeitig laufenden Aufrufen (z.B. weil
    # ein vorheriger Lauf noch nicht fertig ist) sich gegenseitig ueberschreiben.
    config_path = WORK_DIR / f"rclone_{timestamp}_{os.getpid()}.conf"
    log_file = log_dir / f"copy_{timestamp}.log"

    if not find_rclone():
        install_rclone()

    env = os.environ.copy()
    env["RCLONE_CONFIG"] = str(config_path)

    if args.ca_cert_bundle:
        env["RCLONE_CA_CERT"] = args.ca_cert_bundle

    # Alle Parameter zuerst anzeigen, bevor die interaktiven Abfragen beginnen -
    # damit von Anfang an klar ist, mit welcher Konfiguration dieser Lauf
    # tatsaechlich arbeitet.
    print("\n=== Parameter ===")
    print(f"CA-Cert-Bundle: {args.ca_cert_bundle or 'keiner (bei TLS-Inspection durch einen Firmen-Proxy/Firewall ggf. noetig)'}")
    print(f"Transfers: {args.transfers}")
    print(f"Checkers: {args.checkers}")
    rclone_display = "im Programm eingebettet" if RCLONE_BIN != "rclone" else find_rclone()
    print(f"rclone: {rclone_display}")
    print(f"Arbeitsverzeichnis: {WORK_DIR}")
    print(f"Gespeicherte Konten: {ACCOUNTS_CONFIG_PATH}")
    print(f"Log-Verzeichnis: {log_dir}")
    print(f"Log-Datei (dieser Lauf): {log_file}")
    if not args.ca_cert_bundle:
        print(
            "Hinweis: Falls TLS-Inspection durch einen Firmen-Proxy/Firewall (z.B. Cato, "
            "Zscaler) greift: entweder --ca-cert-bundle setzen oder Bypass fuer "
            "login.microsoftonline.com / login.live.com / graph.microsoft.com / "
            "*.onedrive.com / *.sharepoint.com einrichten."
        )

    print("\n=== Was moechtest du tun? ===")
    print("  1. Kopieren/Migrieren (OneDrive/SharePoint)")
    print("  2. Duplikate finden")
    while True:
        tool_choice = input("Auswahl (1/2): ").strip()
        if tool_choice in ("1", "2"):
            break
        print("Ungueltige Eingabe - bitte 1 oder 2 eingeben.")

    try:
        if tool_choice == "1":
            exit_code = run_copy_tool(args, env, config_path, log_file)
        else:
            exit_code = run_dedupe_tool(args, env, config_path, timestamp)
    except ToolError as exc:
        print(f"\nFehler: {exc}")
        sys.exit(exc.code)

    sys.exit(exit_code)

"""App-Shell und Screen-Router fuer Werkzeug 1 (Kopieren/Migrieren). Treibt
denselben Ablauf wie migration_cli.run_copy_tool() an - nur als Kette von
Bildschirmen/Callbacks statt input()-Abfragen, und mit echtem Fortschritt
(migration_gui.rc_progress) statt --progress-Text im Terminal."""

import datetime
import os
import platform
import queue
import subprocess
import threading
import tkinter.filedialog as filedialog
from collections import defaultdict
from pathlib import Path

import customtkinter as ctk

from migration_core import (
    DESKTOP_DIR,
    GRAPH_ROOT,
    RCLONE_BIN,
    ToolError,
    WORK_DIR,
    account_kind,
    build_report_rows,
    extract_error_lines,
    fetch_own_drive,
    find_rclone,
    graph_get,
    join_endpoint_path,
    list_local_root_items,
    list_root_items,
    list_saved_accounts,
    load_saved_account,
    log_manifest,
    save_account,
    search_sharepoint_sites,
    slugify_for_filename,
    suggest_account_name,
    sync_account_token,
    write_dedupe_csv,
)
from migration_cli import create_onedrive_remote, install_rclone, list_files_for_dedupe, rclone_authorize_onedrive, refresh_remote_token

from migration_gui.rc_progress import CheckJob, CopyJob
from migration_gui.screens import (
    AccountPickerScreen,
    BusyScreen,
    DedupeOptionsScreen,
    DedupeResultsScreen,
    EndpointTypeScreen,
    ErrorScreen,
    HomeScreen,
    OAuthScreen,
    ProgressScreen,
    ResultsScreen,
    SaveAccountScreen,
    ScopeScreen,
    SharePointSearchScreen,
    SummaryScreen,
    TargetSubfolderScreen,
)

ctk.set_appearance_mode("system")
ctk.set_default_color_theme("blue")


def _close_splash() -> None:
    """Schliesst PyInstallers Splash-Screen (Icon + 'Wird geladen...', siehe
    onedrive-sharepoint-migration-tool.spec), sobald das Hauptfenster steht.
    pyi_splash existiert nur in einer gefrorenen Binary MIT aktiviertem
    Splash-Feature - beim Start aus dem Quellcode (kein PyInstaller) oder
    einer ohne Splash gebauten Binary ist das ein No-Op."""
    try:
        import pyi_splash
    except ImportError:
        return
    pyi_splash.close()


class App(ctk.CTk):
    def __init__(self, args):
        super().__init__()
        self.args = args
        self.wizard: dict = {}
        self.title("OneDrive / SharePoint Migration")
        self.geometry("720x600")
        self.minsize(640, 520)

        self.container = ctk.CTkFrame(self, fg_color="transparent")
        self.container.pack(fill="both", expand=True, padx=16, pady=16)
        self.current_frame = None
        self.show_home()
        _close_splash()

    # ------------------------------------------------------------------
    # Navigation / Hintergrund-Ausfuehrung
    # ------------------------------------------------------------------

    def _set_frame(self, frame_cls, **kwargs):
        if self.current_frame is not None:
            self.current_frame.destroy()
        self.current_frame = frame_cls(self.container, self, **kwargs)
        self.current_frame.pack(fill="both", expand=True)
        return self.current_frame

    def _run_async(self, work_fn, on_success, on_error=None) -> None:
        """Fuehrt work_fn in einem Hintergrund-Thread aus; on_success/on_error
        werden ueber self.after() sicher im Tkinter-Hauptthread aufgerufen.
        Faengt bewusst jede Exception ab (nicht nur ToolError) - Netzwerk-/
        Subprozess-Code kann diverse Fehlertypen werfen (TimeoutError,
        URLError, KeyError, JSONDecodeError, OSError, CalledProcessError) und
        ein Hintergrund-Thread darf dabei nie einfach lautlos sterben."""
        def runner():
            try:
                result = work_fn()
            except Exception as exc:  # noqa: BLE001 - bewusst breit, siehe Docstring
                handler = on_error if on_error is not None else (lambda e: self._show_error_and_home(str(e)))
                self.after(0, handler, exc)
                return
            self.after(0, on_success, result)
        threading.Thread(target=runner, daemon=True).start()

    def show_home(self) -> None:
        self.wizard = {}
        self._set_frame(HomeScreen)

    def _show_error_and_home(self, message: str) -> None:
        config_path = self.wizard.get("config_path")
        if config_path is not None:
            Path(config_path).unlink(missing_ok=True)
        self._set_frame(ErrorScreen, message=message)

    # ------------------------------------------------------------------
    # Werkzeug 1: Kopieren/Migrieren
    # ------------------------------------------------------------------

    def start_copy_wizard(self) -> None:
        env = os.environ.copy()
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        config_path = WORK_DIR / f"rclone_{timestamp}_{os.getpid()}.conf"
        log_dir = Path("C:/Logs") if platform.system() == "Windows" else Path.home() / "Logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        log_file = log_dir / f"copy_{timestamp}.log"
        env["RCLONE_CONFIG"] = str(config_path)
        if self.args.ca_cert_bundle:
            env["RCLONE_CA_CERT"] = self.args.ca_cert_bundle
        self.wizard = {"env": env, "config_path": config_path, "log_file": log_file}
        self._resolve_endpoint("source", "Quelle", self._after_source_resolved)

    # --- Endpunkt-Auswahl (fuer Quelle UND Ziel wiederverwendet) ---

    def _resolve_endpoint(self, which: str, label: str, on_complete, allow_local: bool = True) -> None:
        self.wizard[f"{which}_label"] = label
        self.wizard[f"{which}_on_complete"] = on_complete
        self.wizard[f"{which}_allow_local"] = allow_local
        self._set_frame(EndpointTypeScreen, label=label, on_choice=lambda t: self._endpoint_type_chosen(which, t), allow_local=allow_local)

    def _endpoint_type_chosen(self, which: str, endpoint_type: str) -> None:
        if endpoint_type == "local":
            self._pick_local_folder(which)
        else:
            self._show_account_picker(which, endpoint_type)

    def _pick_local_folder(self, which: str) -> None:
        label = self.wizard[f"{which}_label"]
        path = filedialog.askdirectory(title=f"Ordner für {label} wählen")
        if not path:
            self._resolve_endpoint(which, label, self.wizard[f"{which}_on_complete"], allow_local=self.wizard[f"{which}_allow_local"])
            return
        info = {"kind": "local", "path": path, "identity": f"Lokal/Netzlaufwerk ({path})", "account_name": None}
        self._complete_endpoint(which, info)

    def _complete_endpoint(self, which: str, info: dict) -> None:
        self.wizard[f"{which}_info"] = info
        self.wizard[f"{which}_on_complete"](info)

    def _show_account_picker(self, which: str, kind: str) -> None:
        label = self.wizard[f"{which}_label"]
        accounts = [name for name in list_saved_accounts() if account_kind(load_saved_account(name)) == kind]
        self._set_frame(
            AccountPickerScreen,
            label=label, kind=kind, accounts=accounts,
            on_pick_existing=lambda name: self._account_picked(which, name),
            on_new_login=lambda: self._start_oauth(which, kind, label),
            on_reauth=lambda name: self._start_oauth(which, kind, label, reauth_name=name),
            on_back=lambda: self._resolve_endpoint(which, label, self.wizard[f"{which}_on_complete"], allow_local=self.wizard[f"{which}_allow_local"]),
        )

    def _account_picked(self, which: str, name: str) -> None:
        account = load_saved_account(name)
        drive_type = account.get("drive_type", "")
        kind = "sharepoint" if drive_type == "documentLibrary" else "onedrive"
        info = {
            "token": account["token"], "drive_id": account["drive_id"], "drive_type": drive_type,
            "kind": kind, "identity": name, "account_name": name,
        }
        self._complete_endpoint(which, info)

    # --- OAuth-Login (neu oder Re-Authentifizierung eines Kontos) ---

    def _start_oauth(self, which: str, kind: str, label: str, reauth_name: str | None = None) -> None:
        type_label = "OneDrive" if kind == "onedrive" else "SharePoint"
        oauth_label = f"Erneute Anmeldung: {reauth_name}" if reauth_name else f"{label} ({type_label})"
        screen = self._set_frame(OAuthScreen, label=oauth_label)

        def on_event(_kind: str, text: str) -> None:
            self.after(0, screen.update_status, text)

        def work():
            return rclone_authorize_onedrive(self.wizard["env"], oauth_label, on_event=on_event)

        self._run_async(work, on_success=lambda token: self._oauth_finished(which, kind, token, reauth_name))

    def _oauth_finished(self, which: str, kind: str, token: str, reauth_name: str | None) -> None:
        if reauth_name:
            account = load_saved_account(reauth_name)
            extra_config = {k: v for k, v in account.items() if k not in ("type", "region", "token", "drive_id", "drive_type")}
            save_account(reauth_name, token, account["drive_id"], account["drive_type"], extra_config)
            self._show_account_picker(which, kind)
            return
        self._fetch_drive_and_continue(which, kind, token)

    def _fetch_drive_and_continue(self, which: str, kind: str, token: str) -> None:
        label = self.wizard[f"{which}_label"]
        if kind == "onedrive":
            self._set_frame(BusyScreen, text="Ermittle Konto-Informationen...")

            def work():
                return fetch_own_drive(token, self.args.ca_cert_bundle)

            def on_success(result):
                drive_id, drive_type, identity = result
                display_name = identity.split("<")[0].strip()
                suggested = suggest_account_name("onedrive", drive_type, display_name)
                self._show_save_account_dialog(
                    which, token, drive_id, drive_type, f"OneDrive ({identity})", "onedrive",
                    suggested, extra_config={"disable_site_permission": "true"},
                )

            self._run_async(
                work, on_success,
                on_error=lambda exc: self._show_error_and_home(f"Konnte {label} nicht über Microsoft Graph auflösen: {exc}"),
            )
        else:
            self._set_frame(
                SharePointSearchScreen,
                on_search=lambda q: self._search_sites(which, token, q),
                on_pick=lambda site: self._site_picked(which, token, site),
            )

    def _search_sites(self, which: str, token: str, query: str) -> None:
        screen = self.current_frame
        label = self.wizard[f"{which}_label"]

        def work():
            return search_sharepoint_sites(query, token, self.args.ca_cert_bundle)

        self._run_async(work, on_success=screen.set_results, on_error=lambda exc: screen.set_error(str(exc)))

    def _site_picked(self, which: str, token: str, site: dict) -> None:
        label = self.wizard[f"{which}_label"]
        site_name = site.get("displayName") or site.get("name") or "(ohne Namen)"
        self._set_frame(BusyScreen, text=f"Ermittle Dokumentbibliothek für '{site_name}'...")

        def work():
            return graph_get(f"{GRAPH_ROOT}/sites/{site['id']}/drive", token, self.args.ca_cert_bundle)

        def on_success(drive):
            drive_id = drive["id"]
            drive_type = drive.get("driveType", "documentLibrary")
            suggested = suggest_account_name("sharepoint", drive_type, site_name)
            self._show_save_account_dialog(
                which, token, drive_id, drive_type,
                f"SharePoint-Site '{site_name}' ({site.get('webUrl', '')})", "sharepoint",
                suggested, extra_config=None,
            )

        self._run_async(
            work, on_success,
            on_error=lambda exc: self._show_error_and_home(f"Konnte {label} nicht über Microsoft Graph auflösen: {exc}"),
        )

    def _show_save_account_dialog(self, which, token, drive_id, drive_type, identity, kind, suggested_name, extra_config) -> None:
        def on_save(name: str) -> None:
            save_account(name, token, drive_id, drive_type, extra_config)
            self._complete_endpoint(which, {
                "token": token, "drive_id": drive_id, "drive_type": drive_type,
                "kind": kind, "identity": identity, "account_name": name,
            })

        def on_skip() -> None:
            self._complete_endpoint(which, {
                "token": token, "drive_id": drive_id, "drive_type": drive_type,
                "kind": kind, "identity": identity, "account_name": None,
            })

        self._set_frame(SaveAccountScreen, suggested_name=suggested_name, on_save=on_save, on_skip=on_skip)

    # --- Nach Quelle: Inhalt ermitteln, Umfang waehlen ---

    def _after_source_resolved(self, info: dict) -> None:
        self._set_frame(BusyScreen, text="Ermittle Inhalt der Quelle...")
        config_path = self.wizard["config_path"]
        env = self.wizard["env"]

        def work():
            if info["kind"] == "local":
                return list_local_root_items(info["path"])
            exit_code = create_onedrive_remote(
                "source", info["token"], info["drive_id"], info["drive_type"], config_path, env,
                extra_config={"disable_site_permission": "true"} if info["kind"] == "onedrive" else None,
            )
            if exit_code != 0:
                raise ToolError(f"Config für Quelle fehlgeschlagen (Exit Code {exit_code}).", exit_code)
            refreshed = refresh_remote_token("source", config_path, env)
            if refreshed is None:
                raise ToolError(
                    "Verbindung zur Quelle fehlgeschlagen - Token evtl. abgelaufen/ungültig. "
                    "Bitte neu starten und beim Konto 'Neu anmelden' wählen.", 1,
                )
            info["token"] = refreshed
            return list_root_items(info["token"], info["drive_id"], self.args.ca_cert_bundle)

        def on_success(items):
            self.wizard["source_root_items"] = items
            self._show_scope_screen(items)

        self._run_async(work, on_success)

    def _show_scope_screen(self, items: list[dict]) -> None:
        foreign_names = [item["name"] for item in items if item["is_foreign"]]
        vault_names = [item["name"] for item in items if item["is_locked_vault"]]
        self._set_frame(ScopeScreen, items=items, foreign_names=foreign_names, vault_names=vault_names, on_submit=self._scope_chosen)

    def _scope_chosen(self, selected_folders: list[str] | None, exclude_foreign: bool) -> None:
        exclude_names: list[str] = []
        if selected_folders is None:
            items = self.wizard["source_root_items"]
            exclude_names = [item["name"] for item in items if item["is_locked_vault"]]
            if exclude_foreign:
                exclude_names += [item["name"] for item in items if item["is_foreign"]]
        self.wizard["selected_folders"] = selected_folders
        self.wizard["exclude_names"] = exclude_names
        self._resolve_endpoint("target", "Ziel", self._after_target_resolved)

    # --- Nach Ziel: Remote einrichten, Ziel-Unterordner abfragen ---

    def _after_target_resolved(self, info: dict) -> None:
        needs_ignore_flags = info["kind"] == "sharepoint" or info.get("drive_type") == "business"
        self.wizard["needs_ignore_flags"] = needs_ignore_flags
        if info["kind"] == "local":
            self._show_target_subfolder_screen()
            return

        self._set_frame(BusyScreen, text="Richte Ziel-Verbindung ein...")
        config_path = self.wizard["config_path"]
        env = self.wizard["env"]

        def work():
            exit_code = create_onedrive_remote(
                "target", info["token"], info["drive_id"], info["drive_type"], config_path, env,
                extra_config={"no_versions": "true"} if needs_ignore_flags else None,
            )
            if exit_code != 0:
                raise ToolError(f"Config für Ziel fehlgeschlagen (Exit Code {exit_code}).", exit_code)
            check = subprocess.run(
                [RCLONE_BIN, "lsd", "target:", "--config", str(config_path), "--max-depth", "1"],
                env=env, capture_output=True, text=True,
            )
            if check.returncode != 0:
                raise ToolError(f"Konnte Ziel nicht auflisten (Exit Code {check.returncode}).", check.returncode)

        self._run_async(work, on_success=lambda _r: self._show_target_subfolder_screen())

    def _show_target_subfolder_screen(self) -> None:
        self._set_frame(TargetSubfolderScreen, on_submit=self._target_subfolder_chosen)

    def _target_subfolder_chosen(self, subfolder: str) -> None:
        self.wizard["target_subfolder"] = subfolder.strip().strip("/")
        self._build_copy_pairs_and_show_summary()

    # --- Zusammenfassung ---

    def _build_copy_pairs_and_show_summary(self) -> None:
        w = self.wizard
        source_info = w["source_info"]
        target_info = w["target_info"]
        source_base = source_info["path"] if source_info["kind"] == "local" else "source:"
        target_base = target_info["path"] if target_info["kind"] == "local" else "target:"
        selected_folders = w["selected_folders"]
        target_subfolder = w["target_subfolder"]

        if selected_folders is None:
            copy_pairs = [(source_base, join_endpoint_path(target_base, target_subfolder))]
        elif len(selected_folders) == 1:
            copy_pairs = [(join_endpoint_path(source_base, selected_folders[0]), join_endpoint_path(target_base, target_subfolder))]
        else:
            copy_pairs = []
            for folder in selected_folders:
                target_path = f"{target_subfolder}/{folder}" if target_subfolder else folder
                copy_pairs.append((join_endpoint_path(source_base, folder), join_endpoint_path(target_base, target_path)))
        w["copy_pairs"] = copy_pairs

        exclude_args: list[str] = []
        for name in w["exclude_names"]:
            exclude_args += ["--exclude", f"{name}/**"]
        copy_extra_args = list(exclude_args)
        if w["needs_ignore_flags"]:
            copy_extra_args += ["--ignore-size", "--ignore-checksum"]
        w["copy_extra_args"] = copy_extra_args

        if target_info["kind"] == "local":
            try:
                Path(target_info["path"]).mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                self._show_error_and_home(f"Konnte Ziel-Pfad nicht anlegen/beschreiben: {exc}")
                return

        summary_lines = [
            f"Quelle: {source_info['identity']}",
            f"Ziel: {target_info['identity']}",
            f"Umfang: {'gesamter Inhalt' if selected_folders is None else 'ausgewählte Ordner: ' + ', '.join(selected_folders)}",
            f"Exclusion: {', '.join(f'{n}/**' for n in w['exclude_names']) if w['exclude_names'] else 'keine'}",
        ]
        if w["needs_ignore_flags"]:
            summary_lines.append(
                "Hinweis: Ziel ist SharePoint bzw. OneDrive Business - Größen- und "
                "Prüfsummenprüfung nach Upload sind deaktiviert, da diese Backends "
                "Dateien serverseitig verändern."
            )
        summary_lines.append("Geplante Kopiervorgänge:")
        summary_lines += [f"  {src} -> {tgt}" for src, tgt in copy_pairs]
        for line in summary_lines:
            log_manifest(w["log_file"], line)

        self._set_frame(SummaryScreen, lines=summary_lines, on_start=self._start_copy, on_cancel=self.show_home)

    # --- Kopieren + Verifikation ---

    def _start_copy(self) -> None:
        w = self.wizard
        screen = self._set_frame(ProgressScreen, total_pairs=len(w["copy_pairs"]))
        job = CopyJob(
            w["copy_pairs"], w["config_path"], w["log_file"], w["env"],
            self.args.transfers, self.args.checkers, extra_args=w["copy_extra_args"],
        )
        w["copy_job"] = job
        screen.bind_cancel(job.cancel)
        job.start()
        self._poll_copy_job(job, screen)

    def _poll_copy_job(self, job: CopyJob, screen) -> None:
        done_event = None
        try:
            while True:
                event = job.events.get_nowait()
                if event["type"] == "done":
                    done_event = event
                else:
                    screen.handle_event(event)
        except queue.Empty:
            pass
        if done_event is not None:
            self._copy_job_done(done_event)
            return
        self.after(200, self._poll_copy_job, job, screen)

    def _copy_job_done(self, event: dict) -> None:
        if event.get("cancelled"):
            self._finish_run(cancelled=True, copy_exit=event["exit_code"])
            return
        if event["exit_code"] != 0:
            self._finish_run(cancelled=False, copy_exit=event["exit_code"])
            return

        w = self.wizard
        self._set_frame(BusyScreen, text="Verifikation (Prüfsummenvergleich) läuft...")
        check_extra = ["--ignore-size"] if w["needs_ignore_flags"] else []
        job = CheckJob(w["copy_pairs"], w["config_path"], w["log_file"], w["env"], self.args.checkers, extra_args=check_extra)
        job.start()
        self._poll_check_job(job)

    def _poll_check_job(self, job: CheckJob) -> None:
        try:
            event = job.events.get_nowait()
        except queue.Empty:
            self.after(200, self._poll_check_job, job)
            return
        self._finish_run(cancelled=False, copy_exit=0, check_exit=event["exit_code"])

    def _finish_run(self, cancelled: bool, copy_exit: int, check_exit: int = 0) -> None:
        w = self.wizard
        source_info = w["source_info"]
        target_info = w["target_info"]
        if source_info.get("account_name"):
            sync_account_token(source_info["account_name"], "source", w["config_path"])
        if target_info.get("account_name"):
            sync_account_token(target_info["account_name"], "target", w["config_path"])
        Path(w["config_path"]).unlink(missing_ok=True)

        error_file, error_count = extract_error_lines(w["log_file"])
        if cancelled:
            status = "cancelled"
        elif copy_exit != 0:
            status = "copy_failed"
        elif check_exit != 0:
            status = "check_failed"
        else:
            status = "ok"
        self._set_frame(
            ResultsScreen, status=status, log_file=w["log_file"], error_file=error_file, error_count=error_count,
            on_restart=self.show_home, on_quit=self.destroy,
        )

    # ------------------------------------------------------------------
    # Werkzeug 2: Duplikate finden
    # ------------------------------------------------------------------

    def start_dedupe_wizard(self) -> None:
        env = os.environ.copy()
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        config_path = WORK_DIR / f"rclone_{timestamp}_{os.getpid()}.conf"
        if self.args.ca_cert_bundle:
            env["RCLONE_CA_CERT"] = self.args.ca_cert_bundle
        env["RCLONE_CONFIG"] = str(config_path)
        self.wizard = {"env": env, "config_path": config_path, "timestamp": timestamp}
        self._resolve_endpoint("scan", "Zu durchsuchendes Konto", self._after_scan_resolved, allow_local=False)

    def _after_scan_resolved(self, info: dict) -> None:
        self._set_frame(BusyScreen, text="Ermittle Inhalt des Kontos...")
        config_path = self.wizard["config_path"]
        env = self.wizard["env"]

        def work():
            exit_code = create_onedrive_remote(
                "scan", info["token"], info["drive_id"], info["drive_type"], config_path, env,
                extra_config={"disable_site_permission": "true"} if info["kind"] == "onedrive" else None,
            )
            if exit_code != 0:
                raise ToolError(f"Config fehlgeschlagen (Exit Code {exit_code}).", exit_code)
            refreshed = refresh_remote_token("scan", config_path, env)
            if refreshed is None:
                raise ToolError(
                    "Verbindung zum Konto fehlgeschlagen - Token evtl. abgelaufen/ungültig. "
                    "Bitte neu starten und beim Konto 'Neu anmelden' wählen.", 1,
                )
            info["token"] = refreshed
            return list_root_items(info["token"], info["drive_id"], self.args.ca_cert_bundle)

        def on_success(items):
            self.wizard["scan_root_items"] = items
            self._show_dedupe_options_screen(items)

        self._run_async(work, on_success)

    def _show_dedupe_options_screen(self, items: list[dict]) -> None:
        w = self.wizard
        scan_info = w["scan_info"]
        own_label = scan_info.get("account_name") or scan_info["identity"]
        vault_names = [item["name"] for item in items if item["is_locked_vault"]]
        foreign_names = [item["name"] for item in items if item["is_foreign"]]
        default_output = str(DESKTOP_DIR / f"dedupe_report_{slugify_for_filename(own_label)}_{w['timestamp']}.csv")
        self._set_frame(
            DedupeOptionsScreen, default_output=default_output, vault_names=vault_names, foreign_names=foreign_names,
            on_submit=self._run_dedupe_scan,
        )

    def _run_dedupe_scan(self, output_path: str, extra_excludes: list[str]) -> None:
        w = self.wizard
        scan_info = w["scan_info"]
        items = w["scan_root_items"]
        # Anders als beim Kopieren werden Verknuepfungen zu Fremd-Shares beim
        # Duplikat-Scan NICHT ausgeschlossen - Duplikate sollen bewusst auch
        # ueber geteilte OneDrive-/SharePoint-Verknuepfungen hinweg gefunden
        # werden. Der Persoenliche Tresor bleibt trotzdem ausgeschlossen.
        vault_names = [item["name"] for item in items if item["is_locked_vault"]]
        excludes = [f"{name}/**" for name in vault_names] + extra_excludes
        own_label = scan_info.get("account_name") or scan_info["identity"]
        foreign_names = {item["name"] for item in items if item["is_foreign"]}

        self._set_frame(BusyScreen, text="Durchsuche Konto (rclone lsjson -R --hash) - bei vielen Dateien kann das dauern...")

        def work():
            files = list_files_for_dedupe("scan:", w["env"], excludes)
            if files is None:
                raise ToolError("Konnte Konto nicht durchsuchen (rclone lsjson fehlgeschlagen).", 1)
            rows, skipped_no_hash = build_report_rows(files, own_label, foreign_names)
            write_dedupe_csv(rows, output_path)
            if scan_info.get("account_name"):
                sync_account_token(scan_info["account_name"], "scan", w["config_path"])
            Path(w["config_path"]).unlink(missing_ok=True)
            return rows, skipped_no_hash, len(files)

        def on_success(result):
            rows, skipped_no_hash, file_count = result
            groups_by_category = defaultdict(set)
            files_by_category = defaultdict(int)
            for row in rows:
                groups_by_category[row["Kategorie"]].add(row["Gruppe"])
                files_by_category[row["Kategorie"]] += 1
            self._set_frame(
                DedupeResultsScreen, output_path=output_path, file_count=file_count,
                groups_by_category=groups_by_category, files_by_category=files_by_category,
                skipped_no_hash=skipped_no_hash, on_restart=self.show_home, on_quit=self.destroy,
            )

        self._run_async(work, on_success)


def main(args) -> None:
    if not find_rclone():
        install_rclone()
    app = App(args)
    app.mainloop()

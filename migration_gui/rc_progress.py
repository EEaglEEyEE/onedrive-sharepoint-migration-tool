"""Fuehrt eine Liste von rclone-copy-Auftraegen im Hintergrund aus und liefert
Live-Fortschritt ueber rclones eigene --rc (Remote-Control-HTTP-API) - dieselbe
API, die rclones eigene Web-GUI nutzt. Kein Parsen von --progress-Text oder
--use-json-log-Zeilen (beides fragil). Komplett unabhaengig vom CLI-Pfad
(migration_cli.run_copy_with_retry bleibt fuer --progress im Terminal
zustaendig) - beide nutzen aber dieselbe migration_core.build_copy_argv()-Basis."""

import json
import platform
import queue
import socket
import subprocess
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

from migration_core import COPY_RETRY_ATTEMPTS, RCLONE_BIN, build_copy_argv, log_manifest


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _popen_kwargs() -> dict:
    if platform.system() == "Windows":
        return {"creationflags": subprocess.CREATE_NO_WINDOW}
    return {}


class CopyJob:
    """Ein Durchlauf ueber alle geplanten Kopiervorgaenge (copy_pairs), jeweils
    mit bis zu COPY_RETRY_ATTEMPTS Wiederholungen bei Fehlschlag - spiegelt
    migration_cli.run_copy_with_retry()/run_copy_tool()'s Schleife, nur non-
    blockierend und mit strukturierten Fortschritts-Events statt print().
    events ist eine thread-sichere Queue; die GUI liest sie im Hauptthread per
    root.after()-Polling aus (siehe migration_gui/app.py: ProgressScreen)."""

    def __init__(
        self,
        copy_pairs: list[tuple[str, str]],
        config_path: Path,
        log_file: Path,
        env: dict,
        transfers: int,
        checkers: int,
        extra_args: list[str] | None = None,
    ):
        self.copy_pairs = copy_pairs
        self.config_path = config_path
        self.log_file = log_file
        self.env = env
        self.transfers = transfers
        self.checkers = checkers
        self.extra_args = extra_args or []
        self.events: queue.Queue = queue.Queue()
        self._cancel = threading.Event()
        self._process: subprocess.Popen | None = None

    def start(self) -> None:
        threading.Thread(target=self._run, daemon=True).start()

    def cancel(self) -> None:
        self._cancel.set()
        if self._process is not None and self._process.poll() is None:
            self._process.terminate()

    def _poll_stats(self, port: int, pair_index: int, total_pairs: int) -> None:
        url = f"http://127.0.0.1:{port}/core/stats"
        while self._process is not None and self._process.poll() is None and not self._cancel.is_set():
            try:
                request = urllib.request.Request(
                    url, method="POST", data=b"{}", headers={"Content-Type": "application/json"}
                )
                with urllib.request.urlopen(request, timeout=2) as response:
                    stats = json.loads(response.read())
                self.events.put({
                    "type": "progress",
                    "pair_index": pair_index,
                    "total_pairs": total_pairs,
                    "bytes": stats.get("bytes", 0),
                    "total_bytes": stats.get("totalBytes", 0),
                    "speed": stats.get("speed", 0),
                    "eta": stats.get("eta"),
                    "errors": stats.get("errors", 0),
                    "transferring": [t.get("name", "") for t in (stats.get("transferring") or [])],
                })
            except (OSError, ValueError, urllib.error.URLError):
                # rclone-RC-Server ist beim allerersten Poll ggf. noch nicht
                # bereit, oder der Prozess ist zwischen poll() und dem
                # eigentlichen Request beendet - beides einfach im naechsten
                # Intervall erneut versuchen, kein Abbruchgrund.
                pass
            time.sleep(0.7)

    def _run(self) -> None:
        overall_exit = 0
        for pair_index, (src, tgt) in enumerate(self.copy_pairs, start=1):
            if self._cancel.is_set():
                break
            log_manifest(self.log_file, f"Kopiere {src} -> {tgt}")
            self.events.put({
                "type": "pair_start", "pair_index": pair_index,
                "total_pairs": len(self.copy_pairs), "source": src, "target": tgt,
            })

            exit_code = 1
            for attempt in range(1, COPY_RETRY_ATTEMPTS + 1):
                if self._cancel.is_set():
                    break
                if attempt > 1:
                    log_manifest(self.log_file, f"Retry {attempt}/{COPY_RETRY_ATTEMPTS}: {src} -> {tgt}")
                    self.events.put({
                        "type": "retry", "pair_index": pair_index,
                        "attempt": attempt, "max_attempts": COPY_RETRY_ATTEMPTS,
                    })
                port = _free_port()
                argv = build_copy_argv(
                    src, tgt, self.config_path, self.log_file, self.transfers, self.checkers,
                    progress_args=["--rc", "--rc-addr", f"127.0.0.1:{port}", "--rc-no-auth"],
                )
                cmd = [RCLONE_BIN, *argv, *self.extra_args]
                self._process = subprocess.Popen(
                    cmd, env=self.env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **_popen_kwargs()
                )
                poll_thread = threading.Thread(
                    target=self._poll_stats, args=(port, pair_index, len(self.copy_pairs)), daemon=True
                )
                poll_thread.start()
                exit_code = self._process.wait()
                poll_thread.join(timeout=2)
                if exit_code == 0:
                    break

            if exit_code != 0:
                overall_exit = exit_code

        self.events.put({"type": "done", "cancelled": self._cancel.is_set(), "exit_code": overall_exit})


class CheckJob:
    """Fuehrt 'rclone check' fuer alle copy_pairs aus (Verifikationsschritt
    nach dem Kopieren) - kein Live-Fortschritt in Phase 1, nur ein
    unbestimmter Spinner in der GUI waehrend dieser Job laeuft (siehe Plan:
    'Fuer den rclone check-Verifikationsschritt reicht in Phase 1 ein
    einfacher unbestimmter Spinner statt echtem Live-Fortschritt')."""

    def __init__(self, copy_pairs, config_path: Path, log_file: Path, env: dict, checkers: int, extra_args=None):
        self.copy_pairs = copy_pairs
        self.config_path = config_path
        self.log_file = log_file
        self.env = env
        self.checkers = checkers
        self.extra_args = extra_args or []
        self.events: queue.Queue = queue.Queue()

    def start(self) -> None:
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self) -> None:
        overall_exit = 0
        for src, tgt in self.copy_pairs:
            result = subprocess.run(
                [
                    RCLONE_BIN, "check", src, tgt,
                    "--config", str(self.config_path),
                    "--checkers", str(self.checkers),
                    "--log-file", str(self.log_file),
                    "--log-level", "INFO",
                    *self.extra_args,
                ],
                env=self.env, **_popen_kwargs(),
            )
            if result.returncode != 0:
                overall_exit = result.returncode
        self.events.put({"type": "done", "exit_code": overall_exit})

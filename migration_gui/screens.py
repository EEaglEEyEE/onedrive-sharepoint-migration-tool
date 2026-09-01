"""Alle Bildschirme (CTkFrame-Unterklassen) der GUI fuer Werkzeug 1
(Kopieren/Migrieren). Bildschirme enthalten selbst keine Geschaefts-/
Netzwerklogik - sie zeigen Zustand an und melden Nutzer-Entscheidungen ueber
Callbacks an migration_gui.app.App zurueck, die dort die eigentlichen
migration_core/migration_cli-Aufrufe (immer in einem Hintergrund-Thread)
ausloest."""

import customtkinter as ctk

PAD = 12


def format_bytes(n: float) -> str:
    n = n or 0
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} PB"


def format_speed(n: float) -> str:
    return f"{format_bytes(n)}/s"


def format_eta(seconds) -> str:
    if not seconds or seconds < 0:
        return "-"
    seconds = int(seconds)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}h {m}m {s}s" if h else (f"{m}m {s}s" if m else f"{s}s")


class HomeScreen(ctk.CTkFrame):
    def __init__(self, master, app):
        super().__init__(master, fg_color="transparent")
        ctk.CTkLabel(self, text="OneDrive / SharePoint Migration", font=ctk.CTkFont(size=22, weight="bold")).pack(pady=(20, 6))
        ctk.CTkLabel(
            self, text="Was moechtest du tun?", font=ctk.CTkFont(size=14), text_color="gray60"
        ).pack(pady=(0, 24))

        ctk.CTkButton(
            self, text="Kopieren / Migrieren", width=320, height=48,
            font=ctk.CTkFont(size=15, weight="bold"), command=app.start_copy_wizard,
        ).pack(pady=8)

        dedupe_btn = ctk.CTkButton(
            self, text="Duplikate finden", width=320, height=48, state="disabled",
        )
        dedupe_btn.pack(pady=8)
        ctk.CTkLabel(
            self, text="Duplikate finden ist aktuell nur per Terminal (--cli) verfuegbar.",
            font=ctk.CTkFont(size=11), text_color="gray60",
        ).pack(pady=(0, 8))


class BusyScreen(ctk.CTkFrame):
    def __init__(self, master, app, text: str):
        super().__init__(master, fg_color="transparent")
        ctk.CTkLabel(self, text=text, font=ctk.CTkFont(size=15)).pack(pady=(140, 16))
        bar = ctk.CTkProgressBar(self, width=280, mode="indeterminate")
        bar.pack()
        bar.start()


class ErrorScreen(ctk.CTkFrame):
    def __init__(self, master, app, message: str):
        super().__init__(master, fg_color="transparent")
        ctk.CTkLabel(self, text="Fehler", font=ctk.CTkFont(size=20, weight="bold"), text_color="#d9534f").pack(pady=(60, 12))
        ctk.CTkLabel(self, text=message, wraplength=560, justify="left").pack(padx=20, pady=8)
        ctk.CTkButton(self, text="Zurueck zum Start", command=app.show_home).pack(pady=24)


class EndpointTypeScreen(ctk.CTkFrame):
    def __init__(self, master, app, label: str, on_choice):
        super().__init__(master, fg_color="transparent")
        ctk.CTkLabel(self, text=label, font=ctk.CTkFont(size=20, weight="bold")).pack(pady=(20, 4))
        ctk.CTkLabel(self, text="Wovon/wohin soll migriert werden?", text_color="gray60").pack(pady=(0, 20))
        for text, value in (("OneDrive", "onedrive"), ("SharePoint-Site", "sharepoint"), ("Lokaler Pfad / Netzlaufwerk", "local")):
            ctk.CTkButton(self, text=text, width=320, height=44, command=lambda v=value: on_choice(v)).pack(pady=6)


class AccountPickerScreen(ctk.CTkFrame):
    def __init__(self, master, app, label: str, kind: str, accounts: list[str], on_pick_existing, on_new_login, on_reauth, on_back):
        super().__init__(master, fg_color="transparent")
        type_label = "OneDrive" if kind == "onedrive" else "SharePoint"
        ctk.CTkLabel(self, text=f"{label}: Konto waehlen ({type_label})", font=ctk.CTkFont(size=20, weight="bold")).pack(pady=(20, 4))

        if accounts:
            scroll = ctk.CTkScrollableFrame(self, width=460, height=220)
            scroll.pack(pady=12)
            for name in accounts:
                row = ctk.CTkFrame(scroll, fg_color="transparent")
                row.pack(fill="x", pady=4)
                ctk.CTkLabel(row, text=name, anchor="w").pack(side="left", padx=(4, 8), fill="x", expand=True)
                ctk.CTkButton(row, text="Neu anmelden", width=110, fg_color="transparent", border_width=1,
                              command=lambda n=name: on_reauth(n)).pack(side="right", padx=4)
                ctk.CTkButton(row, text="Verwenden", width=100,
                              command=lambda n=name: on_pick_existing(n)).pack(side="right", padx=4)
        else:
            ctk.CTkLabel(self, text="Noch keine gespeicherten Konten dieses Typs.", text_color="gray60").pack(pady=12)

        ctk.CTkButton(self, text="Neue Anmeldung...", width=320, height=44, command=on_new_login).pack(pady=(12, 6))
        ctk.CTkButton(self, text="Zurueck", width=320, fg_color="transparent", border_width=1, command=on_back).pack(pady=4)


class OAuthScreen(ctk.CTkFrame):
    def __init__(self, master, app, label: str):
        super().__init__(master, fg_color="transparent")
        ctk.CTkLabel(self, text=f"Anmeldung: {label}", font=ctk.CTkFont(size=20, weight="bold")).pack(pady=(60, 16))
        self.status_label = ctk.CTkLabel(self, text="Starte...", wraplength=520, font=ctk.CTkFont(size=14))
        self.status_label.pack(pady=8)
        bar = ctk.CTkProgressBar(self, width=280, mode="indeterminate")
        bar.pack(pady=12)
        bar.start()

    def update_status(self, text: str) -> None:
        self.status_label.configure(text=text)


class SharePointSearchScreen(ctk.CTkFrame):
    def __init__(self, master, app, on_search, on_pick):
        super().__init__(master, fg_color="transparent")
        self.on_search = on_search
        self.on_pick = on_pick
        ctk.CTkLabel(self, text="SharePoint-Site suchen", font=ctk.CTkFont(size=20, weight="bold")).pack(pady=(20, 12))

        search_row = ctk.CTkFrame(self, fg_color="transparent")
        search_row.pack(pady=(0, 12))
        self.entry = ctk.CTkEntry(search_row, width=340, placeholder_text="Suchbegriff (leer = alle sichtbaren Sites)")
        self.entry.pack(side="left", padx=(0, 8))
        self.entry.bind("<Return>", lambda _e: self._search())
        ctk.CTkButton(search_row, text="Suchen", width=90, command=self._search).pack(side="left")

        self.status_label = ctk.CTkLabel(self, text="", text_color="gray60")
        self.status_label.pack()
        self.results_frame = ctk.CTkScrollableFrame(self, width=520, height=260)
        self.results_frame.pack(pady=8)

        self.after(50, self._search)

    def _search(self) -> None:
        for child in self.results_frame.winfo_children():
            child.destroy()
        self.status_label.configure(text="Suche laeuft...")
        self.on_search(self.entry.get().strip())

    def set_results(self, sites: list[dict]) -> None:
        for child in self.results_frame.winfo_children():
            child.destroy()
        if not sites:
            self.status_label.configure(text="Keine Sites gefunden - anderen Suchbegriff versuchen.")
            return
        self.status_label.configure(text=f"{len(sites)} Site(s) gefunden:")
        for site in sites:
            name = site.get("displayName") or site.get("name") or "(ohne Namen)"
            row = ctk.CTkFrame(self.results_frame, fg_color="transparent")
            row.pack(fill="x", pady=3)
            text = ctk.CTkLabel(row, text=f"{name}\n{site.get('webUrl', '')}", anchor="w", justify="left", font=ctk.CTkFont(size=12))
            text.pack(side="left", padx=4, fill="x", expand=True)
            ctk.CTkButton(row, text="Auswaehlen", width=100, command=lambda s=site: self.on_pick(s)).pack(side="right", padx=4)

    def set_error(self, message: str) -> None:
        self.status_label.configure(text=f"Suche fehlgeschlagen: {message}")


class SaveAccountScreen(ctk.CTkFrame):
    def __init__(self, master, app, suggested_name: str, on_save, on_skip):
        super().__init__(master, fg_color="transparent")
        ctk.CTkLabel(self, text="Konto dauerhaft speichern?", font=ctk.CTkFont(size=20, weight="bold")).pack(pady=(60, 4))
        ctk.CTkLabel(
            self, text="Damit steht das Konto beim naechsten Start direkt zur Auswahl,\nohne erneuten Login.",
            text_color="gray60", justify="center",
        ).pack(pady=(0, 16))
        self.entry = ctk.CTkEntry(self, width=340)
        self.entry.insert(0, suggested_name)
        self.entry.pack(pady=8)
        self.entry.bind("<Return>", lambda _e: on_save(self.entry.get().strip() or suggested_name))

        row = ctk.CTkFrame(self, fg_color="transparent")
        row.pack(pady=16)
        ctk.CTkButton(row, text="Speichern", width=150, command=lambda: on_save(self.entry.get().strip() or suggested_name)).pack(side="left", padx=6)
        ctk.CTkButton(row, text="Nicht speichern", width=150, fg_color="transparent", border_width=1, command=on_skip).pack(side="left", padx=6)


class ScopeScreen(ctk.CTkFrame):
    def __init__(self, master, app, items: list[dict], foreign_names: list[str], vault_names: list[str], on_submit):
        super().__init__(master, fg_color="transparent")
        self.on_submit = on_submit
        self.folder_items = [item for item in items if item["is_folder"] and not item["is_locked_vault"]]

        ctk.CTkLabel(self, text="Umfang", font=ctk.CTkFont(size=20, weight="bold")).pack(pady=(16, 4))
        if vault_names:
            ctk.CTkLabel(
                self, text=f"Hinweis: '{', '.join(vault_names)}' ist per API nicht zugaenglich und wird immer uebersprungen.",
                text_color="gray60", font=ctk.CTkFont(size=11),
            ).pack(pady=(0, 8))

        self.mode = ctk.StringVar(value="whole")
        mode_row = ctk.CTkFrame(self, fg_color="transparent")
        mode_row.pack(pady=8)
        ctk.CTkRadioButton(mode_row, text="Gesamten Inhalt kopieren", variable=self.mode, value="whole", command=self._render).pack(side="left", padx=10)
        ctk.CTkRadioButton(mode_row, text="Nur ausgewaehlte Ordner", variable=self.mode, value="selected", command=self._render).pack(side="left", padx=10)

        self.exclude_foreign_var = ctk.BooleanVar(value=True)
        self.foreign_names = foreign_names

        self.body = ctk.CTkFrame(self, fg_color="transparent")
        self.body.pack(fill="both", expand=True, pady=8)

        self.error_label = ctk.CTkLabel(self, text="", text_color="#d9534f")
        self.error_label.pack()
        ctk.CTkButton(self, text="Weiter", width=200, command=self._submit).pack(pady=12)

        self.checkbox_vars: dict[str, ctk.BooleanVar] = {}
        self._render()

    def _render(self) -> None:
        for child in self.body.winfo_children():
            child.destroy()
        if self.mode.get() == "whole":
            if self.foreign_names:
                ctk.CTkLabel(
                    self.body, text="Folgende Eintraege sind Verknuepfungen zu Inhalten aus einem anderen Konto/einer anderen Site:",
                    text_color="gray60", font=ctk.CTkFont(size=12),
                ).pack(pady=(4, 2))
                ctk.CTkLabel(self.body, text=", ".join(self.foreign_names), font=ctk.CTkFont(size=12)).pack()
                ctk.CTkCheckBox(self.body, text="Diese ausschliessen", variable=self.exclude_foreign_var).pack(pady=10)
            else:
                ctk.CTkLabel(self.body, text="Keine Verknuepfungen zu fremden Konten/Sites im Root gefunden.", text_color="gray60").pack(pady=10)
        else:
            scroll = ctk.CTkScrollableFrame(self.body, width=480, height=220)
            scroll.pack()
            self.checkbox_vars = {}
            for item in self.folder_items:
                var = ctk.BooleanVar(value=False)
                self.checkbox_vars[item["name"]] = var
                text = item["name"] + ("  [Verknuepfung aus anderem Konto/Site]" if item["is_foreign"] else "")
                ctk.CTkCheckBox(scroll, text=text, variable=var).pack(anchor="w", pady=2, padx=4)

    def _submit(self) -> None:
        self.error_label.configure(text="")
        if self.mode.get() == "whole":
            self.on_submit(None, self.exclude_foreign_var.get())
            return
        selected = [name for name, var in self.checkbox_vars.items() if var.get()]
        if not selected:
            self.error_label.configure(text="Bitte mindestens einen Ordner auswaehlen.")
            return
        self.on_submit(selected, False)


class TargetSubfolderScreen(ctk.CTkFrame):
    def __init__(self, master, app, on_submit):
        super().__init__(master, fg_color="transparent")
        ctk.CTkLabel(self, text="Ziel-Unterordner", font=ctk.CTkFont(size=20, weight="bold")).pack(pady=(60, 4))
        ctk.CTkLabel(self, text="Leer lassen fuer den Root des Ziels.", text_color="gray60").pack(pady=(0, 16))
        self.entry = ctk.CTkEntry(self, width=340, placeholder_text="z.B. Backup/2026")
        self.entry.pack(pady=8)
        self.entry.bind("<Return>", lambda _e: on_submit(self.entry.get()))
        ctk.CTkButton(self, text="Weiter", width=200, command=lambda: on_submit(self.entry.get())).pack(pady=16)


class SummaryScreen(ctk.CTkFrame):
    def __init__(self, master, app, lines: list[str], on_start, on_cancel):
        super().__init__(master, fg_color="transparent")
        ctk.CTkLabel(self, text="Zusammenfassung", font=ctk.CTkFont(size=20, weight="bold")).pack(pady=(16, 8))
        box = ctk.CTkTextbox(self, width=560, height=280)
        box.pack(pady=8)
        box.insert("1.0", "\n".join(lines))
        box.configure(state="disabled")
        row = ctk.CTkFrame(self, fg_color="transparent")
        row.pack(pady=12)
        ctk.CTkButton(row, text="Kopieren starten", width=200, font=ctk.CTkFont(weight="bold"), command=on_start).pack(side="left", padx=6)
        ctk.CTkButton(row, text="Abbrechen", width=150, fg_color="transparent", border_width=1, command=on_cancel).pack(side="left", padx=6)


class ProgressScreen(ctk.CTkFrame):
    def __init__(self, master, app, total_pairs: int):
        super().__init__(master, fg_color="transparent")
        self.total_pairs = total_pairs
        self._cancel_cb = None

        ctk.CTkLabel(self, text="Kopiervorgang laeuft...", font=ctk.CTkFont(size=20, weight="bold")).pack(pady=(20, 4))
        self.pair_label = ctk.CTkLabel(self, text="", text_color="gray60")
        self.pair_label.pack(pady=(0, 12))

        self.bar = ctk.CTkProgressBar(self, width=480)
        self.bar.set(0)
        self.bar.pack(pady=8)

        self.status_label = ctk.CTkLabel(self, text="Bereite vor...", font=ctk.CTkFont(size=13))
        self.status_label.pack(pady=4)

        self.files_label = ctk.CTkLabel(self, text="", text_color="gray60", font=ctk.CTkFont(size=11), wraplength=500, justify="left")
        self.files_label.pack(pady=4)

        self.retry_label = ctk.CTkLabel(self, text="", text_color="#e0a800")
        self.retry_label.pack(pady=4)

        self.cancel_btn = ctk.CTkButton(self, text="Abbrechen", fg_color="transparent", border_width=1, command=self._on_cancel_clicked)
        self.cancel_btn.pack(pady=16)

    def bind_cancel(self, cancel_fn) -> None:
        self._cancel_cb = cancel_fn

    def _on_cancel_clicked(self) -> None:
        if self._cancel_cb:
            self._cancel_cb()
        self.cancel_btn.configure(state="disabled", text="Wird abgebrochen...")

    def handle_event(self, event: dict) -> None:
        kind = event["type"]
        if kind == "pair_start":
            self.pair_label.configure(text=f"Kopiervorgang {event['pair_index']}/{event['total_pairs']}: {event['source']} -> {event['target']}")
            self.retry_label.configure(text="")
            self.bar.set(0)
        elif kind == "progress":
            total = event.get("total_bytes") or 0
            done = event.get("bytes") or 0
            if total > 0:
                self.bar.set(min(done / total, 1.0))
                self.status_label.configure(
                    text=f"{format_bytes(done)} / {format_bytes(total)}  -  {format_speed(event.get('speed'))}  -  ETA {format_eta(event.get('eta'))}"
                )
            else:
                self.status_label.configure(text=f"{format_bytes(done)} kopiert  -  {format_speed(event.get('speed'))}")
            transferring = event.get("transferring") or []
            self.files_label.configure(text="\n".join(transferring[:3]))
        elif kind == "retry":
            self.retry_label.configure(text=f"Wiederhole fehlgeschlagene Dateien (Versuch {event['attempt']}/{event['max_attempts']})...")


class ResultsScreen(ctk.CTkFrame):
    def __init__(self, master, app, status: str, log_file, error_file, error_count: int, on_restart, on_quit):
        super().__init__(master, fg_color="transparent")
        titles = {
            "ok": ("Fertig", "#2fa84f"),
            "cancelled": ("Abgebrochen", "#e0a800"),
            "copy_failed": ("Kopiervorgang fehlerhaft", "#d9534f"),
            "check_failed": ("Verifikation fand Abweichungen", "#d9534f"),
        }
        title, color = titles.get(status, ("Fertig", "#2fa84f"))
        ctk.CTkLabel(self, text=title, font=ctk.CTkFont(size=22, weight="bold"), text_color=color).pack(pady=(40, 8))

        messages = {
            "ok": "Alles vollstaendig kopiert und verifiziert.",
            "cancelled": "Der Vorgang wurde abgebrochen. Erneut starten, um fehlende Dateien nachzukopieren.",
            "copy_failed": f"Mindestens ein Kopiervorgang blieb fehlerhaft. Details im Log: {log_file}",
            "check_failed": f"Verifikation hat Abweichungen gefunden. Details im Log: {log_file}\nErneut starten, um abweichende/fehlende Dateien nachzukopieren.",
        }
        ctk.CTkLabel(self, text=messages.get(status, ""), wraplength=560, justify="center").pack(pady=8, padx=20)
        ctk.CTkLabel(self, text=f"Fehlerzeilen im Log: {error_count}", text_color="gray60").pack(pady=(0, 16))

        row = ctk.CTkFrame(self, fg_color="transparent")
        row.pack(pady=8)
        ctk.CTkButton(row, text="Log oeffnen", width=140, command=lambda: app_open(log_file)).pack(side="left", padx=6)
        if error_count:
            ctk.CTkButton(row, text="Fehler-Log oeffnen", width=160, command=lambda: app_open(error_file)).pack(side="left", padx=6)

        row2 = ctk.CTkFrame(self, fg_color="transparent")
        row2.pack(pady=16)
        ctk.CTkButton(row2, text="Neuer Lauf", width=150, command=on_restart).pack(side="left", padx=6)
        ctk.CTkButton(row2, text="Beenden", width=150, fg_color="transparent", border_width=1, command=on_quit).pack(side="left", padx=6)


def app_open(path) -> None:
    from migration_core import open_in_viewer
    open_in_viewer(path)

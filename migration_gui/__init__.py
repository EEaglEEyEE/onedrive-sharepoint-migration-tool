"""Grafische Oberflaeche (CustomTkinter) fuer Werkzeug 1 (Kopieren/Migrieren).
Nutzt migration_core direkt sowie einzelne, strukturell bereits GUI-taugliche
Funktionen aus migration_cli (rclone_authorize_onedrive mit on_event-Callback,
create_onedrive_remote, refresh_remote_token, install_rclone) - diese Funktionen
enthalten zwar print()-Aufrufe, aber keine input()-Aufrufe, sind also aus einem
Hintergrund-Thread heraus sicher wiederverwendbar (die print()-Ausgabe landet im
gepackten GUI-Modus einfach ungenutzt im verborgenen Konsolen-Stream)."""

from migration_gui.app import main  # noqa: F401 - Reexport fuer "import migration_gui; migration_gui.main(args)"

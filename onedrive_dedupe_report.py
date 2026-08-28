#!/usr/bin/env python3
"""
Findet Duplikate in einem rclone-Remote (z.B. OneDrive) ueber den gesamten
Verzeichnisbaum hinweg - nicht nur pro Ordner wie 'rclone dedupe'. Analysiert
den Output von 'rclone lsjson -R --hash' und unterscheidet drei Kategorien:

  1. Sichere Duplikate - gleicher Name UND gleicher Hash (identischer Inhalt
     an unterschiedlichen Orten)
  2. Nur Name gleich   - gleicher Dateiname, aber unterschiedlicher Inhalt
     (potenzielle Versionskonflikte, manuell pruefen)
  3. Nur Hash gleich   - identischer Inhalt, aber unterschiedlicher Dateiname
     (Kopien mit anderem Namen)

Ergebnis als eine CSV-Datei mit Kategorie-Spalte (eine Zeile pro Datei, eine
Gruppen-ID verbindet zusammengehoerige Zeilen - so bleibt die Datei auch bei
Gruppen mit mehr als zwei Mitgliedern eine saubere Tabelle statt raggediger
Pfad_1/Pfad_2/...-Spalten).

Setzt voraus, dass rclone bereits gegen die Quelle authentifiziert ist (z.B.
ein bestehender Remote 'onedrive:' aus 'rclone config'). Loest KEINEN eigenen
OAuth-Login aus - anders als OneDrive_Copy in diesem Projekt, das bewusst mit
ephemeren, non-interaktiv angelegten Remotes arbeitet.

Aufruf:
    python3 onedrive_dedupe_report.py --remote onedrive: --output report.csv
    python3 onedrive_dedupe_report.py --remote onedrive:Ordner --output report.csv --ca-cert-bundle /pfad/zum/bundle.pem
    python3 onedrive_dedupe_report.py --remote onedrive: --output report.csv --exclude "_Archiv/**"
"""

import argparse
import csv
import json
import os
import platform
import shutil
import subprocess
import sys
from collections import defaultdict
from pathlib import Path


def _resolve_rclone_bin() -> str:
    """Als PyInstaller-Bundle gebaut liegt eine passende rclone-Binary direkt
    im Bundle bei (siehe onedrive-sharepoint-migration-tool.py fuer denselben
    Mechanismus) und wird bevorzugt verwendet - dann ist kein separates
    rclone-Setup auf dem Zielsystem noetig. Als normales .py-Script bleibt es
    bei der ueblichen PATH-Suche."""
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


def list_files(remote: str, ca_cert_bundle: str | None, excludes: list[str]) -> list[dict]:
    """Ruft 'rclone lsjson' rekursiv mit Hash-Angabe ab und gibt die
    geparsten Datei-Eintraege zurueck (Ordner werden per --files-only
    weggelassen)."""
    cmd = [RCLONE_BIN, "lsjson", remote, "-R", "--files-only", "--hash"]
    for pattern in excludes:
        cmd += ["--exclude", pattern]

    env = os.environ.copy()
    if ca_cert_bundle:
        env["RCLONE_CA_CERT"] = ca_cert_bundle

    print(f"Frage '{remote}' ab (rclone lsjson -R --hash) - bei grossen Strukturen kann das einen Moment dauern...")
    result = subprocess.run(cmd, env=env, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"rclone lsjson fehlgeschlagen (Exit Code {result.returncode}):\n{result.stderr}")
        sys.exit(result.returncode)
    return json.loads(result.stdout)


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


def _row(category: str, group_id: int, f: dict) -> dict:
    return {
        "Kategorie": category,
        "Gruppe": group_id,
        "Hash": f["_hash"],
        "Dateiname": f["Name"],
        "Pfad": f["Path"],
        "Groesse": f.get("Size", ""),
        "Letzte_Aenderung": f.get("ModTime", ""),
    }


def build_report_rows(files: list[dict]) -> list[dict]:
    """Baut die drei Kategorien aus den Datei-Eintraegen. Eine Datei kann in
    mehreren Kategorien auftauchen (z.B. Teil eines sicheren Duplikat-Paars
    UND Teil einer Namenskollision mit einer dritten Datei) - das sind
    unterschiedliche, sich nicht ausschliessende Fragen an die Daten, kein
    striktes Partitionieren."""
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

    if skipped_no_hash:
        print(f"Hinweis: {skipped_no_hash} Datei(en) ohne Hash vom Backend uebersprungen (kann nicht sicher verglichen werden).")

    rows: list[dict] = []
    group_id = 0

    for group in by_name_hash.values():
        if len(group) < 2:
            continue
        group_id += 1
        rows += [_row("1_sicheres_duplikat", group_id, f) for f in group]

    for group in by_name.values():
        if len({f["_hash"] for f in group}) < 2:
            continue
        group_id += 1
        rows += [_row("2_nur_name_gleich", group_id, f) for f in group]

    for group in by_hash.values():
        if len({f["Name"] for f in group}) < 2:
            continue
        group_id += 1
        rows += [_row("3_nur_hash_gleich", group_id, f) for f in group]

    return rows


def write_csv(rows: list[dict], output_path: str) -> None:
    fieldnames = ["Kategorie", "Gruppe", "Hash", "Dateiname", "Pfad", "Groesse", "Letzte_Aenderung"]
    # utf-8-sig: Excel unter Windows zeigt Umlaute ohne BOM sonst falsch an.
    with open(output_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Findet Duplikate in einem rclone-Remote (z.B. OneDrive) ueber den gesamten Verzeichnisbaum und erzeugt einen CSV-Report."
    )
    parser.add_argument(
        "--remote", required=True,
        help="rclone-Remote inkl. optionalem Pfad, z.B. 'onedrive:' oder 'onedrive:Ordner'. Muss bereits per 'rclone config' eingerichtet sein.",
    )
    parser.add_argument("--output", default="dedupe_report.csv", help="Pfad der zu erzeugenden CSV-Datei (Standard: dedupe_report.csv).")
    parser.add_argument(
        "--ca-cert-bundle", default=None,
        help="Pfad zum CA-Bundle (PEM) eines TLS-inspizierenden Firmen-Proxys/Firewall (z.B. Cato, Zscaler), falls noetig.",
    )
    parser.add_argument(
        "--exclude", action="append", default=[], metavar="MUSTER",
        help="rclone-Filtermuster, das von der Analyse ausgeschlossen wird (mehrfach nutzbar), z.B. '_Archiv/**'.",
    )
    args = parser.parse_args()

    if not find_rclone():
        print("rclone wurde nicht gefunden. Bitte installieren: https://rclone.org/downloads/")
        sys.exit(1)

    rclone_display = "im Programm eingebettet" if RCLONE_BIN != "rclone" else find_rclone()
    print("=== Parameter ===")
    print(f"Remote: {args.remote}")
    print(f"Output: {args.output}")
    print(f"CA-Cert-Bundle: {args.ca_cert_bundle or 'keiner'}")
    print(f"Exclude: {', '.join(args.exclude) if args.exclude else 'keine'}")
    print(f"rclone: {rclone_display}")
    print()

    files = list_files(args.remote, args.ca_cert_bundle, args.exclude)
    print(f"{len(files)} Dateien gefunden.")

    rows = build_report_rows(files)
    write_csv(rows, args.output)

    groups_by_category: dict[str, set[int]] = defaultdict(set)
    files_by_category: dict[str, int] = defaultdict(int)
    for row in rows:
        groups_by_category[row["Kategorie"]].add(row["Gruppe"])
        files_by_category[row["Kategorie"]] += 1

    print(f"\nReport geschrieben nach: {args.output}")
    for category, label in [
        ("1_sicheres_duplikat", "Sichere Duplikate"),
        ("2_nur_name_gleich", "Nur Name gleich"),
        ("3_nur_hash_gleich", "Nur Hash gleich"),
    ]:
        print(f"  {label}: {len(groups_by_category[category])} Gruppen ({files_by_category[category]} Dateien)")


if __name__ == "__main__":
    main()

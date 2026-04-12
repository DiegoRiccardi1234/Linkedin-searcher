import argparse
import csv
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.db import Database


def parse_bool_applied(value: str) -> bool:
    text = (value or "").strip().lower()
    return text in {"si", "s", "yes", "y", "mandata", "true", "1"}


def run_migration(csv_path: Path, db_path: Path) -> dict:
    db = Database(db_path)
    migrated = 0
    skipped = 0

    with open(csv_path, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f, delimiter=";")
        for row in reader:
            titolo = row.get("Titolo", "").strip()
            azienda = row.get("Azienda", "").strip()
            if not titolo or not azienda:
                skipped += 1
                continue

            payload = {
                "titolo": titolo,
                "azienda": azienda,
                "descrizione": row.get("Riassunto AI", "") or "",
                "sede": row.get("Sede", "") or "",
                "fonte": row.get("Fonte", "") or "legacy-csv",
                "link": row.get("Link", "") or "",
                "ricerca_usata": row.get("Ricerca usata", "") or "legacy",
                "modalita": row.get("Modalità", "") or "Legacy",
            }
            job_id, _, _ = db.upsert_job(payload)

            analysis = {
                "punteggio": row.get("Punteggio AI", "0") or "0",
                "consiglio": row.get("Consiglio", "") or "",
                "programmazione_richiesta": row.get("Programmazione richiesta", "?") or "?",
                "smart_working": row.get("Smart Working", "?") or "?",
                "contratto": row.get("Contratto", "?") or "?",
                "junior_friendly": row.get("Junior Friendly", "?") or "?",
                "anni_esperienza_richiesti": row.get("Anni esperienza richiesti", "?") or "?",
                "punti_forza_per_diego": row.get("Punti forza per Diego", "?") or "?",
                "punti_deboli_per_diego": row.get("Punti deboli per Diego", "?") or "?",
                "riassunto": row.get("Riassunto AI", "?") or "?",
                "ral_stimata": row.get("RAL Stimata AI", "Non stimabile") or "Non stimabile",
                "reputazione_azienda": row.get("Reputazione Azienda", "?") or "?",
                "adatta_neolaureati": row.get("Adatta Neolaureati", "?") or "?",
                "note_azienda": row.get("Note Azienda", "?") or "?",
                "stipendio_min": row.get("Stipendio Min (jobspy)", "N/D") or "N/D",
                "stipendio_max": row.get("Stipendio Max (jobspy)", "N/D") or "N/D",
            }
            db.update_job_analysis(job_id, analysis)

            if parse_bool_applied(row.get("Mandata candidatura?", "")):
                db.set_job_action(job_id, "applied", "Import da CSV storico")

            migrated += 1

    stats = {
        "csv": str(csv_path),
        "db": str(db_path),
        "migrated": migrated,
        "skipped": skipped,
    }
    db.close()
    return stats


def main() -> None:
    parser = argparse.ArgumentParser(description="Migra CSV storico in SQLite")
    parser.add_argument("--csv", required=True, help="Path CSV storico")
    parser.add_argument("--db", default="data/searcher.db", help="Path DB sqlite")
    args = parser.parse_args()

    csv_path = Path(args.csv)
    db_path = Path(args.db)
    db_path.parent.mkdir(parents=True, exist_ok=True)

    if not csv_path.exists():
        raise FileNotFoundError(f"CSV non trovato: {csv_path}")

    result = run_migration(csv_path, db_path)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

"""
One-time migration: copies every row from the old SQLite database
(legacy_flask_sqlite_backup/data_sqlite_backup/clarity.db) into the new
PostgreSQL database (database.py / CLARITY_DATABASE_URL).

Run once, after `database.py`'s schema has been created (importing it does
this automatically). Safe to re-run: each insert is ON CONFLICT DO NOTHING,
so already-migrated rows are skipped rather than duplicated.
"""
import sqlite3
import sys

import database as db

SQLITE_PATH = "legacy_flask_sqlite_backup/data_sqlite_backup/clarity.db"

# (table, columns in insertion order) — FK-safe order: parents before children
TABLES = [
    ("model_versions", ["version", "path", "created_at", "parent_version", "metrics_json", "is_champion", "notes"]),
    ("patients", ["id", "mrn", "full_name", "date_of_birth", "sex", "age", "previous_skin_condition",
                  "phone", "email", "notes", "consent_research", "created_at", "updated_at"]),
    ("scans", ["id", "patient_id", "image_path", "image_sha256", "captured_at", "model_version", "skin_type_pred",
               "skin_type_conf", "probs_json", "severity_json", "overall_score", "triage_status", "image_quality",
               "cv_metrics_json", "routine_json", "embedding", "embedding_version"]),
    ("annotations", ["id", "scan_id", "annotator", "verified_skin_type", "severity_json", "notes", "created_at"]),
    ("consultations", ["id", "patient_id", "scan_id", "fee_usd", "payment_status", "status", "ai_suggestions_json",
                        "doctor_name", "symptoms", "diagnosis", "cure", "prescription", "previous_condition",
                        "verified_skin_type", "verified_severity_json", "followup_days", "followup_due_at",
                        "followup_scan_id", "created_at", "paid_at", "completed_at"]),
    ("training_runs", ["id", "started_at", "finished_at", "base_version", "new_version", "n_train", "n_user_samples",
                        "metrics_json", "promoted", "reason", "status"]),
    ("audit_log", ["id", "ts", "actor", "action", "entity", "entity_id", "details"]),
]

# tables with a SERIAL/IDENTITY primary key that must be resynced after explicit-id inserts
SEQUENCES = {"annotations": "id", "training_runs": "id", "audit_log": "id"}


def main():
    sconn = sqlite3.connect(SQLITE_PATH)
    sconn.row_factory = sqlite3.Row

    with db.connect() as pconn:
        for table, cols in TABLES:
            rows = sconn.execute(f"SELECT {', '.join(cols)} FROM {table}").fetchall()
            n = 0
            for r in rows:
                values = [r[c] for c in cols]
                placeholders = ", ".join(["%s"] * len(cols))
                conflict_col = "version" if table == "model_versions" else "id"
                pconn.execute(
                    f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({placeholders})"
                    f" ON CONFLICT ({conflict_col}) DO NOTHING",
                    values)
                n += 1
            print(f"{table}: migrated {n} rows")

        for table, col in SEQUENCES.items():
            pconn.execute(
                f"SELECT setval(pg_get_serial_sequence('{table}', '{col}'), COALESCE((SELECT MAX({col}) FROM {table}), 1))")
        print("sequences resynced")


if __name__ == "__main__":
    main()

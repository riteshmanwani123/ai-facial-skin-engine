"""
database.py — PostgreSQL data-access layer for Clarity AI Face Engine.

Storage:
    PostgreSQL database `clarity` (connection: CLARITY_DATABASE_URL, default
    "postgresql:///clarity" — local Unix-socket connection as the current OS user).
    Image bytes are never stored here — see storage.py (the Image Storage layer);
    this module stores only each image's relative path and sha256.

Tables
    patients        demographic record + research-consent flag
    scans           every analysis: prediction, scores, triage, CV metrics, embedding
    annotations     clinician-verified ground truth for a scan (used for learning)
    model_versions  registry of trained models, which one is the "champion"
    training_runs   history of every (re)training attempt and whether it was promoted
    consultations   paid doctor consultations: the doctor's structured verdict + follow-up
    audit_log       who did what, when (hospital traceability)

Only scans from patients with consent_research = 1 (and, unless CLARITY_LEARN_ANONYMOUS=0,
anonymous scans with no patient record) are ever used for learning.

Every function signature here matches the previous SQLite-backed database.py
(kept at legacy_flask_sqlite_backup/database_sqlite.py) so callers — server.py,
train_v2.py, continual_learning.py, backend/main.py — did not need to change.
"""
import os
import json
import time
import uuid
from datetime import datetime, timezone, timedelta
from contextlib import contextmanager

import numpy as np
import psycopg
from psycopg.rows import dict_row

import storage

_raw_dsn = os.environ.get("CLARITY_DATABASE_URL") or os.environ.get("DATABASE_URL") or "postgresql:///clarity"
if "@" in _raw_dsn and "?" not in _raw_dsn and "localhost" not in _raw_dsn and "127.0.0.1" not in _raw_dsn:
    DB_DSN = _raw_dsn + "?sslmode=require"
else:
    DB_DSN = _raw_dsn
# Anonymous scans (no patient attached) may be used for learning unless disabled
LEARN_FROM_ANONYMOUS = os.environ.get("CLARITY_LEARN_ANONYMOUS", "1") == "1"

# Kept for callers that still reference these (e.g. server.py's retrain log path)
DATA_DIR = storage.DATA_DIR
SCAN_DIR = storage.SCAN_DIR

SCHEMA = """
CREATE TABLE IF NOT EXISTS patients (
    id               TEXT PRIMARY KEY,
    mrn              TEXT UNIQUE,
    full_name        TEXT NOT NULL,
    date_of_birth    TEXT,
    sex              TEXT,
    age              INTEGER,
    previous_skin_condition TEXT,
    intake_json      TEXT,
    phone            TEXT,
    email            TEXT,
    notes            TEXT,
    consent_research INTEGER NOT NULL DEFAULT 0,
    created_at       TEXT NOT NULL,
    updated_at       TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS scans (
    id                 TEXT PRIMARY KEY,
    patient_id         TEXT REFERENCES patients(id) ON DELETE SET NULL,
    image_path         TEXT NOT NULL,
    image_sha256       TEXT NOT NULL,
    captured_at        TEXT NOT NULL,
    model_version      TEXT,
    skin_type_pred     TEXT,
    skin_type_conf     REAL,
    probs_json         TEXT,
    severity_json      TEXT,
    overall_score      INTEGER,
    triage_status      TEXT,
    image_quality      TEXT,
    cv_metrics_json    TEXT,
    routine_json       TEXT,
    embedding          BYTEA,
    embedding_version  TEXT
);
CREATE INDEX IF NOT EXISTS idx_scans_patient ON scans(patient_id, captured_at);
CREATE INDEX IF NOT EXISTS idx_scans_sha ON scans(image_sha256);

-- One row per captured angle of a scan (front, left, right, up, down). The front view reuses the file
-- already referenced by scans.image_path. redaction_json is the audit trail of what was covered
-- (detector status + the exact eye polygons) — see privacy.py. Every stored image here is the
-- eye-covered version; no unredacted image is ever written.
CREATE TABLE IF NOT EXISTS scan_views (
    id             SERIAL PRIMARY KEY,
    scan_id        TEXT NOT NULL REFERENCES scans(id) ON DELETE CASCADE,
    view           TEXT NOT NULL,
    image_path     TEXT NOT NULL,
    image_sha256   TEXT NOT NULL,
    redaction_json TEXT,
    created_at     TEXT NOT NULL,
    UNIQUE (scan_id, view)
);

CREATE TABLE IF NOT EXISTS annotations (
    id                 SERIAL PRIMARY KEY,
    scan_id            TEXT NOT NULL REFERENCES scans(id) ON DELETE CASCADE,
    annotator          TEXT NOT NULL,
    verified_skin_type TEXT,
    severity_json      TEXT,
    notes              TEXT,
    created_at         TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ann_scan ON annotations(scan_id);

CREATE TABLE IF NOT EXISTS model_versions (
    version        TEXT PRIMARY KEY,
    path           TEXT NOT NULL,
    created_at     TEXT NOT NULL,
    parent_version TEXT,
    metrics_json   TEXT,
    is_champion    INTEGER NOT NULL DEFAULT 0,
    notes          TEXT
);

CREATE TABLE IF NOT EXISTS training_runs (
    id              SERIAL PRIMARY KEY,
    started_at      TEXT NOT NULL,
    finished_at     TEXT,
    base_version    TEXT,
    new_version     TEXT,
    n_train         INTEGER,
    n_user_samples  INTEGER,
    metrics_json    TEXT,
    promoted        INTEGER,
    reason          TEXT,
    status          TEXT NOT NULL DEFAULT 'running'
);

CREATE TABLE IF NOT EXISTS consultations (
    id                  TEXT PRIMARY KEY,
    patient_id          TEXT NOT NULL REFERENCES patients(id) ON DELETE CASCADE,
    scan_id             TEXT NOT NULL REFERENCES scans(id),
    fee_usd             REAL NOT NULL DEFAULT 99,
    payment_status      TEXT NOT NULL DEFAULT 'pending',
    status              TEXT NOT NULL DEFAULT 'awaiting_payment',
    ai_suggestions_json TEXT,
    doctor_name         TEXT,
    symptoms            TEXT,
    diagnosis           TEXT,
    cure                TEXT,
    prescription        TEXT,
    previous_condition  TEXT,
    verified_skin_type  TEXT,
    verified_severity_json TEXT,
    followup_days       INTEGER,
    followup_due_at     TEXT,
    followup_scan_id    TEXT REFERENCES scans(id),
    created_at          TEXT NOT NULL,
    paid_at             TEXT,
    completed_at        TEXT
);
CREATE INDEX IF NOT EXISTS idx_consult_patient ON consultations(patient_id, created_at);
CREATE INDEX IF NOT EXISTS idx_consult_status ON consultations(status);

-- Login accounts. role='patient' accounts link to one row in patients(); role='clinician'
-- accounts are doctors/staff and have no patient link. Pre-existing patient records created
-- before accounts existed (walk-in/staff-assisted scans) simply have no linked user — that's
-- fine, they stay usable from the clinic dashboard, they just can't log in themselves.
CREATE TABLE IF NOT EXISTS users (
    id              TEXT PRIMARY KEY,
    email           TEXT NOT NULL UNIQUE,
    password_hash   TEXT NOT NULL,
    full_name       TEXT NOT NULL,
    role            TEXT NOT NULL,  -- 'patient' | 'clinician'
    patient_id      TEXT REFERENCES patients(id) ON DELETE SET NULL,
    created_at      TEXT NOT NULL,
    last_login_at   TEXT
);
CREATE INDEX IF NOT EXISTS idx_users_email ON users(email);

CREATE TABLE IF NOT EXISTS audit_log (
    id        SERIAL PRIMARY KEY,
    ts        TEXT NOT NULL,
    actor     TEXT,
    action    TEXT NOT NULL,
    entity    TEXT,
    entity_id TEXT,
    details   TEXT
);

-- Consultation appointments (see scheduling.py). Times are UTC ISO strings. A doctor can hold only one
-- 'booked' appointment per start time — enforced here, so two patients can never get the same doctor+slot.
CREATE TABLE IF NOT EXISTS appointments (
    id               TEXT PRIMARY KEY,
    consultation_id  TEXT NOT NULL REFERENCES consultations(id) ON DELETE CASCADE,
    patient_id       TEXT NOT NULL REFERENCES patients(id) ON DELETE CASCADE,
    doctor_id        TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    start_at         TEXT NOT NULL,
    end_at           TEXT NOT NULL,
    status           TEXT NOT NULL DEFAULT 'booked',   -- booked | cancelled | completed
    cancel_reason    TEXT,
    created_at       TEXT NOT NULL,
    updated_at       TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_appt_doctor_slot ON appointments(doctor_id, start_at) WHERE status = 'booked';
CREATE INDEX IF NOT EXISTS idx_appt_consult ON appointments(consultation_id);
CREATE INDEX IF NOT EXISTS idx_appt_start ON appointments(start_at);

-- Follow-up check-in scans after a completed consultation: day 15, 30 and 45 (see journey.py). One per day marker.
CREATE TABLE IF NOT EXISTS followups (
    id              TEXT PRIMARY KEY,
    consultation_id TEXT NOT NULL REFERENCES consultations(id) ON DELETE CASCADE,
    day             INTEGER NOT NULL,
    scan_id         TEXT NOT NULL REFERENCES scans(id) ON DELETE CASCADE,
    created_at      TEXT NOT NULL,
    UNIQUE (consultation_id, day)
);

-- Time the clinic (doctor_id NULL) or one doctor cannot take bookings: holidays, leave, training.
CREATE TABLE IF NOT EXISTS schedule_blocks (
    id          TEXT PRIMARY KEY,
    doctor_id   TEXT REFERENCES users(id) ON DELETE CASCADE,
    start_at    TEXT NOT NULL,
    end_at      TEXT NOT NULL,
    reason      TEXT,
    created_by  TEXT,
    created_at  TEXT NOT NULL
);
"""


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@contextmanager
def connect():
    conn = None
    last_err = None
    for attempt in range(5):
        try:
            conn = psycopg.connect(DB_DSN, row_factory=dict_row, autocommit=False)
            break
        except Exception as e:
            last_err = e
            if attempt < 4:
                time.sleep(2)
    if conn is None:
        if not os.environ.get("DATABASE_URL") and not os.environ.get("CLARITY_DATABASE_URL"):
            print("\n" + "="*70)
            print("[CRITICAL CONFIG ERROR] DATABASE_URL environment variable is missing on Render!")
            print("Please add DATABASE_URL in your clarity-backend -> Environment settings on Render.")
            print("="*70 + "\n")
        raise last_err

    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db():
    storage.ensure_dirs()
    try:
        with connect() as c:
            c.execute(SCHEMA)
            # patients created before the sign-up questionnaire existed
            c.execute("ALTER TABLE patients ADD COLUMN IF NOT EXISTS intake_json TEXT")
            _init_identity(c)
            _backfill_followups(c)
            _seed_demo_users(c)
            # Ensure production champion model is registered
            v2_path = os.path.join(storage.ROOT, "models", "clarity_v2.1.pt")
            if os.path.exists(v2_path):
                has_champ = c.execute("SELECT version FROM model_versions WHERE is_champion = 1").fetchone()
                if not has_champ:
                    c.execute(
                        "INSERT INTO model_versions(version, path, created_at, is_champion, notes) "
                        "VALUES ('2.1', 'models/clarity_v2.1.pt', %s, 1, 'Production v2.1 champion') "
                        "ON CONFLICT (version) DO UPDATE SET is_champion = 1", (now(),)
                    )
        print("[database] Database initialized successfully.")
        return True
    except Exception as e:
        print(f"[database] Note: database initialization skipped or pending: {e}")
        return False


def is_healthy():
    try:
        with connect() as c:
            c.execute("SELECT 1")
            return True
    except Exception:
        return False


# Every account gets a unique, human-readable ID by role (PT-000012 patient, DR-00003 doctor, AD-00001 admin) and every
# patient record a unique MRN (CL-000012). They come from Postgres sequences, so they are never reused or duplicated.
ROLES = ("patient", "clinician", "admin")
CODE_PREFIX = {"patient": "PT", "clinician": "DR", "admin": "AD"}


def _init_identity(c):
    c.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS user_code TEXT")
    for seq in ("user_code_patient", "user_code_clinician", "user_code_admin", "patient_mrn"):
        c.execute(f"CREATE SEQUENCE IF NOT EXISTS {seq}")
    for r in c.execute("SELECT id, role FROM users WHERE user_code IS NULL ORDER BY created_at").fetchall():
        c.execute("UPDATE users SET user_code = %s WHERE id = %s", (_next_user_code(c, r["role"]), r["id"]))
    for r in c.execute("SELECT id FROM patients WHERE mrn IS NULL OR mrn = '' ORDER BY created_at").fetchall():
        c.execute("UPDATE patients SET mrn = %s WHERE id = %s", (_next_mrn(c), r["id"]))
    c.execute("CREATE UNIQUE INDEX IF NOT EXISTS uq_users_code ON users(user_code)")
    c.execute("""DO $$ BEGIN
        ALTER TABLE users ADD CONSTRAINT users_role_chk CHECK (role IN ('patient','clinician','admin'));
    EXCEPTION WHEN duplicate_object THEN NULL; END $$""")


def _backfill_followups(c):
    """Follow-up scans recorded before day-15/30/45 check-ins existed count as the first (day-15) check-in."""
    for r in c.execute("SELECT id, followup_scan_id FROM consultations WHERE followup_scan_id IS NOT NULL"
                       " AND id NOT IN (SELECT consultation_id FROM followups)").fetchall():
        c.execute("INSERT INTO followups(id, consultation_id, day, scan_id, created_at) VALUES (%s,%s,15,%s,%s) ON CONFLICT DO NOTHING",
                  (str(uuid.uuid4()), r["id"], r["followup_scan_id"], now()))


def _seed_demo_users(c):
    """Ensure default demo accounts exist for immediate 1-click evaluation."""
    import auth
    demos = [
        ("admin@clarity.local", "Admin12345", "System Administrator", "admin"),
        ("doctor@clarity.local", "Doctor12345", "Dr. Tanya Unni", "clinician"),
    ]
    for email, pwd, name, role in demos:
        existing = c.execute("SELECT id FROM users WHERE email = %s", (email,)).fetchone()
        if not existing:
            uid = str(uuid.uuid4())
            c.execute(
                "INSERT INTO users(id, email, password_hash, full_name, role, patient_id, created_at, user_code) "
                "VALUES (%s, %s, %s, %s, %s, NULL, %s, %s)",
                (uid, email, auth.hash_password(pwd), name, role, now(), _next_user_code(c, role))
            )
            audit("seed", "user", uid, actor="system", details={"role": role}, conn=c)

    p_email = "amantadikademo@gmail.com"
    existing_p = c.execute("SELECT id FROM users WHERE email = %s", (p_email,)).fetchone()
    if not existing_p:
        pid = str(uuid.uuid4())
        mrn = _next_mrn(c)
        c.execute(
            "INSERT INTO patients(id, mrn, full_name, email, consent_research, created_at, updated_at) "
            "VALUES (%s, %s, %s, %s, 1, %s, %s)",
            (pid, mrn, "Aman Tadika", p_email, now(), now())
        )
        uid = str(uuid.uuid4())
        c.execute(
            "INSERT INTO users(id, email, password_hash, full_name, role, patient_id, created_at, user_code) "
            "VALUES (%s, %s, %s, %s, 'patient', %s, %s, %s)",
            (uid, p_email, auth.hash_password("12345678"), "Aman Tadika", pid, now(), _next_user_code(c, "patient"))
        )
        audit("seed", "patient_user", uid, actor="system", details={"patient_id": pid}, conn=c)


def _next_user_code(c, role):
    n = c.execute(f"SELECT nextval('user_code_{role}') AS n").fetchone()["n"]
    return f"{CODE_PREFIX[role]}-{n:05d}"


def _next_mrn(c):
    return f"CL-{c.execute('SELECT nextval(%s) AS n', ('patient_mrn',)).fetchone()['n']:06d}"


def _row(r):
    if r is None:
        return None
    d = dict(r)
    d.pop("embedding", None)
    for k in list(d):
        if k.endswith("_json") and d[k]:
            d[k[:-5]] = json.loads(d.pop(k))
        elif k.endswith("_json"):
            d[k[:-5]] = None
            d.pop(k)
    return d


def _patient_row(r):
    """A patients row as a dict, plus a readable form of the sign-up questionnaire for the doctor's view."""
    p = _row(r)
    if p and p.get("intake"):
        import intake as _intake
        p["intake_display"] = [{"label": k, "value": v} for k, v in _intake.describe(p["intake"])]
    return p


def audit(action, entity=None, entity_id=None, actor="system", details=None, conn=None):
    q = "INSERT INTO audit_log(ts, actor, action, entity, entity_id, details) VALUES (%s,%s,%s,%s,%s,%s)"
    args = (now(), actor, action, entity, entity_id, json.dumps(details) if details else None)
    if conn is not None:
        conn.execute(q, args)
    else:
        with connect() as c:
            c.execute(q, args)


# ----------------------------------------------------------------- patients
PATIENT_FIELDS = ["mrn", "full_name", "date_of_birth", "sex", "age", "previous_skin_condition",
                   "phone", "email", "notes", "consent_research"]


def create_patient(full_name, actor="system", **fields):
    pid = str(uuid.uuid4())
    data = {k: fields.get(k) for k in PATIENT_FIELDS if k != "full_name"}
    data["consent_research"] = 1 if data.get("consent_research") in (1, True, "1", "true", "yes", "on") else 0
    ts = now()
    with connect() as c:
        c.execute(
            "INSERT INTO patients(id, full_name, mrn, date_of_birth, sex, age, previous_skin_condition, phone,"
            " email, notes, consent_research, created_at, updated_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (pid, full_name, data["mrn"] or _next_mrn(c), data["date_of_birth"], data["sex"], data["age"] or None,
             data["previous_skin_condition"], data["phone"], data["email"], data["notes"],
             data["consent_research"], ts, ts))
        audit("create", "patient", pid, actor, conn=c)
    return get_patient(pid)


def set_patient_intake(pid, intake, actor="signup"):
    """Stores the (already sanitised, see intake.clean_intake) sign-up questionnaire on the patient."""
    with connect() as c:
        c.execute("UPDATE patients SET intake_json = %s, updated_at = %s WHERE id = %s", (json.dumps(intake), now(), pid))
        audit("update", "patient", pid, actor, {"intake_saved": True, "keys": sorted(intake)}, conn=c)


def update_patient(pid, actor="system", **fields):
    sets, args = [], []
    for k in PATIENT_FIELDS:
        if k in fields:
            v = fields[k]
            if k == "consent_research":
                v = 1 if v in (1, True, "1", "true", "yes", "on") else 0
            sets.append(f"{k} = %s")
            args.append(v)
    if not sets:
        return get_patient(pid)
    sets.append("updated_at = %s")
    args += [now(), pid]
    with connect() as c:
        c.execute(f"UPDATE patients SET {', '.join(sets)} WHERE id = %s", args)
        audit("update", "patient", pid, actor, {k: fields[k] for k in fields if k in PATIENT_FIELDS}, conn=c)
    return get_patient(pid)


def get_patient(pid):
    with connect() as c:
        p = _patient_row(c.execute("SELECT * FROM patients WHERE id = %s", (pid,)).fetchone())
        if p:
            p["scan_count"] = c.execute("SELECT COUNT(*) AS n FROM scans WHERE patient_id = %s", (pid,)).fetchone()["n"]
    return p


def list_patients(search=None, limit=200):
    q = ("SELECT p.*, COUNT(s.id) AS scan_count, MAX(s.captured_at) AS last_scan_at, "
         "(SELECT overall_score FROM scans s2 WHERE s2.patient_id = p.id ORDER BY captured_at DESC LIMIT 1) AS last_score "
         "FROM patients p LEFT JOIN scans s ON s.patient_id = p.id ")
    args = []
    if search:
        q += "WHERE p.full_name ILIKE %s OR p.mrn ILIKE %s OR p.phone ILIKE %s OR p.email ILIKE %s "
        args = [f"%{search}%"] * 4
    q += "GROUP BY p.id ORDER BY COALESCE(MAX(s.captured_at), p.created_at) DESC LIMIT %s"
    args.append(limit)
    with connect() as c:
        return [_row(r) for r in c.execute(q, args).fetchall()]


def delete_patient(pid, actor="system", delete_images=True):
    """Right-to-erasure: removes the patient, their scans, annotations and images."""
    with connect() as c:
        rows = c.execute("SELECT image_path FROM scans WHERE patient_id = %s", (pid,)).fetchall()
        rows += c.execute("SELECT v.image_path FROM scan_views v JOIN scans s ON s.id = v.scan_id"
                          " WHERE s.patient_id = %s", (pid,)).fetchall()
        c.execute("DELETE FROM scans WHERE patient_id = %s", (pid,))
        c.execute("DELETE FROM patients WHERE id = %s", (pid,))
        audit("delete", "patient", pid, actor, {"scans_removed": len(rows)}, conn=c)
    if delete_images:
        for r in rows:
            storage.delete_image(r["image_path"])
        storage.delete_owner_folder(pid)


# -------------------------------------------------------------------- scans
def save_scan(pil_image, result, patient_id=None, model_version=None, embedding=None,
              embedding_version=None, actor="system"):
    """Stores the image via storage.py and the full analysis result in the DB. Returns scan id."""
    sid = str(uuid.uuid4())
    rel_path, sha = storage.save_image(pil_image, patient_id, sid)
    emb = None if embedding is None else np.asarray(embedding, np.float32).tobytes()
    with connect() as c:
        c.execute(
            "INSERT INTO scans(id, patient_id, image_path, image_sha256, captured_at, model_version, skin_type_pred,"
            " skin_type_conf, probs_json, severity_json, overall_score, triage_status, image_quality, cv_metrics_json,"
            " routine_json, embedding, embedding_version) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (sid, patient_id, rel_path, sha, now(), model_version, result.get("skin_type"),
             result.get("skin_type_confidence"), json.dumps(result.get("skin_type_probabilities")),
             json.dumps(result.get("severity_scores")), result.get("overall_health_score"),
             (result.get("triage") or {}).get("status"),
             ((result.get("cv_metrics") or {}).get("image_quality") or {}).get("overall_quality"),
             json.dumps(result.get("cv_metrics")), json.dumps(result.get("routine")), emb, embedding_version))
        audit("create", "scan", sid, actor, {"patient_id": patient_id}, conn=c)
    return sid


def get_scan(sid):
    with connect() as c:
        s = _row(c.execute("SELECT * FROM scans WHERE id = %s", (sid,)).fetchone())
        if s:
            s["annotations"] = [_row(r) for r in c.execute(
                "SELECT * FROM annotations WHERE scan_id = %s ORDER BY created_at", (sid,)).fetchall()]
    return s


def scan_image_path(sid):
    with connect() as c:
        r = c.execute("SELECT image_path FROM scans WHERE id = %s", (sid,)).fetchone()
    return storage.abs_path(r["image_path"]) if r else None


def list_scans(patient_id=None, limit=100):
    q = ("SELECT s.id, s.patient_id, p.full_name, s.captured_at, s.model_version, s.skin_type_pred, s.skin_type_conf,"
         " s.overall_score, s.triage_status, s.image_quality, s.severity_json,"
         " (SELECT verified_skin_type FROM annotations a WHERE a.scan_id = s.id ORDER BY a.created_at DESC LIMIT 1) AS verified_skin_type"
         " FROM scans s LEFT JOIN patients p ON p.id = s.patient_id ")
    args = []
    if patient_id:
        q += "WHERE s.patient_id = %s "
        args.append(patient_id)
    q += "ORDER BY s.captured_at DESC LIMIT %s"
    args.append(limit)
    with connect() as c:
        return [_row(r) for r in c.execute(q, args).fetchall()]


def update_scan_embedding(sid, embedding, version):
    with connect() as c:
        c.execute("UPDATE scans SET embedding = %s, embedding_version = %s WHERE id = %s",
                  (np.asarray(embedding, np.float32).tobytes(), version, sid))


# -------------------------------------------------------------- scan views
VIEW_KEYS = ("front", "left", "right", "up", "down")


def register_front_view(scan_id, redaction=None):
    """The front view is the image already saved for the scan; record it in scan_views without a second file."""
    with connect() as c:
        s = c.execute("SELECT image_path, image_sha256 FROM scans WHERE id = %s", (scan_id,)).fetchone()
        c.execute("INSERT INTO scan_views(scan_id, view, image_path, image_sha256, redaction_json, created_at)"
                  " VALUES (%s,'front',%s,%s,%s,%s) ON CONFLICT (scan_id, view) DO NOTHING",
                  (scan_id, s["image_path"], s["image_sha256"], json.dumps(redaction) if redaction else None, now()))


def save_scan_view(scan_id, patient_id, view, pil_image, redaction=None):
    """Stores one extra (already eye-covered) angle for a scan. Returns the relative image path."""
    if view not in VIEW_KEYS or view == "front":
        raise ValueError(f"view must be one of {VIEW_KEYS[1:]}")
    rel_path, sha = storage.save_image(pil_image, patient_id, f"{scan_id}_{view}")
    with connect() as c:
        c.execute("INSERT INTO scan_views(scan_id, view, image_path, image_sha256, redaction_json, created_at)"
                  " VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT (scan_id, view) DO UPDATE SET image_path=EXCLUDED.image_path,"
                  " image_sha256=EXCLUDED.image_sha256, redaction_json=EXCLUDED.redaction_json",
                  (scan_id, view, rel_path, sha, json.dumps(redaction) if redaction else None, now()))
    return rel_path


def list_scan_views(scan_id):
    with connect() as c:
        rows = c.execute("SELECT view, image_sha256, redaction_json, created_at FROM scan_views WHERE scan_id = %s", (scan_id,)).fetchall()
    order = {k: i for i, k in enumerate(VIEW_KEYS)}
    out = [_row(r) for r in rows]
    out.sort(key=lambda r: order.get(r["view"], 99))
    return out


def get_view_redaction(scan_id, view="front"):
    """The audit record for one view: detector status plus the exact polygons that were covered."""
    with connect() as c:
        r = c.execute("SELECT redaction_json FROM scan_views WHERE scan_id = %s AND view = %s", (scan_id, view)).fetchone()
    return json.loads(r["redaction_json"]) if r and r["redaction_json"] else None


def scan_view_image_path(scan_id, view):
    with connect() as c:
        r = c.execute("SELECT image_path FROM scan_views WHERE scan_id = %s AND view = %s", (scan_id, view)).fetchone()
    return storage.abs_path(r["image_path"]) if r else None


# -------------------------------------------------------------- annotations
def add_annotation(scan_id, annotator, verified_skin_type=None, severity=None, notes=None):
    with connect() as c:
        c.execute("INSERT INTO annotations(scan_id, annotator, verified_skin_type, severity_json, notes, created_at)"
                  " VALUES (%s,%s,%s,%s,%s,%s)",
                  (scan_id, annotator, verified_skin_type, json.dumps(severity) if severity else None, notes, now()))
        audit("verify", "scan", scan_id, annotator, {"skin_type": verified_skin_type, "severity": severity}, conn=c)


def verified_samples(embedding_version=None, consent_only=True, since=None):
    """
    Latest clinician annotation per scan, joined with the scan. Used by the learning loop.
    Returns list of dicts with image path, patient id, labels, and embedding (np.ndarray or None).
    """
    q = ("SELECT s.id AS scan_id, s.patient_id, s.image_path, s.embedding, s.embedding_version, s.skin_type_pred,"
         " a.verified_skin_type, a.severity_json, a.created_at AS verified_at"
         " FROM annotations a JOIN scans s ON s.id = a.scan_id"
         " LEFT JOIN patients p ON p.id = s.patient_id"
         " WHERE a.id = (SELECT MAX(id) FROM annotations a2 WHERE a2.scan_id = a.scan_id)")
    args = []
    if consent_only:
        q += " AND (p.consent_research = 1" + (" OR s.patient_id IS NULL)" if LEARN_FROM_ANONYMOUS else ")")
    if since:
        q += " AND a.created_at > %s"
        args.append(since)
    out = []
    with connect() as c:
        for r in c.execute(q, args).fetchall():
            d = dict(r)
            emb = d.pop("embedding")
            ok = emb is not None and (embedding_version is None or d["embedding_version"] == embedding_version)
            d["embedding"] = np.frombuffer(bytes(emb), np.float32) if ok else None
            d["severity"] = json.loads(d.pop("severity_json")) if d.get("severity_json") else {}
            d["abs_image_path"] = storage.abs_path(d["image_path"])
            out.append(d)
    return out


# ----------------------------------------------------------- model registry
def register_model(version, path, metrics=None, parent_version=None, notes=None, make_champion=False):
    with connect() as c:
        c.execute(
            "INSERT INTO model_versions(version, path, created_at, parent_version, metrics_json, is_champion, notes)"
            " VALUES (%s,%s,%s,%s,%s,0,%s)"
            " ON CONFLICT (version) DO UPDATE SET path=EXCLUDED.path, created_at=EXCLUDED.created_at,"
            " parent_version=EXCLUDED.parent_version, metrics_json=EXCLUDED.metrics_json, notes=EXCLUDED.notes",
            (version, path, now(), parent_version, json.dumps(metrics) if metrics else None, notes))
        if make_champion:
            c.execute("UPDATE model_versions SET is_champion = CASE WHEN version = %s THEN 1 ELSE 0 END", (version,))
        audit("register", "model", version, details={"champion": make_champion}, conn=c)


def set_champion(version):
    with connect() as c:
        c.execute("UPDATE model_versions SET is_champion = CASE WHEN version = %s THEN 1 ELSE 0 END", (version,))
        audit("promote", "model", version, conn=c)


def get_champion():
    with connect() as c:
        return _row(c.execute("SELECT * FROM model_versions WHERE is_champion = 1").fetchone())


def list_models():
    with connect() as c:
        return [_row(r) for r in c.execute("SELECT * FROM model_versions ORDER BY created_at DESC").fetchall()]


def start_training_run(base_version, n_train, n_user):
    with connect() as c:
        cur = c.execute("INSERT INTO training_runs(started_at, base_version, n_train, n_user_samples, status)"
                        " VALUES (%s,%s,%s,%s,'running') RETURNING id", (now(), base_version, n_train, n_user))
        return cur.fetchone()["id"]


def finish_training_run(run_id, new_version, metrics, promoted, reason, status="done"):
    with connect() as c:
        c.execute("UPDATE training_runs SET finished_at=%s, new_version=%s, metrics_json=%s, promoted=%s, reason=%s,"
                  " status=%s WHERE id=%s", (now(), new_version, json.dumps(metrics), int(bool(promoted)), reason, status, run_id))


def last_training_run():
    with connect() as c:
        return _row(c.execute("SELECT * FROM training_runs ORDER BY id DESC LIMIT 1").fetchone())


def dashboard_stats():
    with connect() as c:
        g = lambda q, *a: c.execute(q, a).fetchone()["n"]
        last = c.execute("SELECT finished_at FROM training_runs WHERE status='done' ORDER BY id DESC LIMIT 1").fetchone()
        since = last["finished_at"] if last else "0"
        return {
            "patients": g("SELECT COUNT(*) AS n FROM patients"),
            "scans": g("SELECT COUNT(*) AS n FROM scans"),
            "scans_today": g("SELECT COUNT(*) AS n FROM scans WHERE substr(captured_at,1,10) = %s", now()[:10]),
            "high_risk_scans": g("SELECT COUNT(*) AS n FROM scans WHERE triage_status = 'HIGH_RISK_CLINICAL'"),
            "verified_scans": g("SELECT COUNT(DISTINCT scan_id) AS n FROM annotations"),
            "verified_since_last_training": g("SELECT COUNT(DISTINCT scan_id) AS n FROM annotations WHERE created_at > %s", since),
            "model_disagreements": g(
                "SELECT COUNT(*) AS n FROM scans s WHERE EXISTS (SELECT 1 FROM annotations a WHERE a.scan_id = s.id"
                " AND a.verified_skin_type IS NOT NULL AND a.verified_skin_type != s.skin_type_pred)"),
            "skin_type_distribution": {r["skin_type_pred"]: r["n"] for r in c.execute(
                "SELECT skin_type_pred, COUNT(*) AS n FROM scans GROUP BY skin_type_pred").fetchall() if r["skin_type_pred"]},
        }


# ------------------------------------------------------------- consultations
def create_consultation(patient_id, scan_id, fee_usd=99.0, ai_suggestions=None):
    cid = str(uuid.uuid4())
    with connect() as c:
        c.execute(
            "INSERT INTO consultations(id, patient_id, scan_id, fee_usd, payment_status, status,"
            " ai_suggestions_json, created_at) VALUES (%s,%s,%s,%s,'pending','awaiting_payment',%s,%s)",
            (cid, patient_id, scan_id, fee_usd, json.dumps(ai_suggestions) if ai_suggestions else None, now()))
        audit("create", "consultation", cid, "system", {"patient_id": patient_id, "scan_id": scan_id}, conn=c)
    return get_consultation(cid)


def get_consultation(cid):
    with connect() as c:
        con = _row(c.execute("SELECT * FROM consultations WHERE id = %s", (cid,)).fetchone())
        if con:
            con["patient"] = _patient_row(c.execute("SELECT * FROM patients WHERE id = %s", (con["patient_id"],)).fetchone())
    return con


def list_consultations(status=None, patient_id=None, limit=200):
    q = "SELECT * FROM consultations WHERE 1=1"
    args = []
    if status:
        q += " AND status = %s"
        args.append(status)
    if patient_id:
        q += " AND patient_id = %s"
        args.append(patient_id)
    q += " ORDER BY created_at DESC LIMIT %s"
    args.append(limit)
    with connect() as c:
        rows = [_row(r) for r in c.execute(q, args).fetchall()]
        for con in rows:
            con["patient"] = _patient_row(c.execute("SELECT * FROM patients WHERE id = %s", (con["patient_id"],)).fetchone())
    return rows


def mark_consultation_paid(cid, actor="patient"):
    with connect() as c:
        c.execute("UPDATE consultations SET payment_status='paid_demo', status='awaiting_doctor', paid_at=%s"
                  " WHERE id=%s AND status='awaiting_payment'", (now(), cid))
        audit("pay_demo", "consultation", cid, actor, conn=c)
    return get_consultation(cid)


def complete_consultation(cid, doctor_name, symptoms=None, diagnosis=None, cure=None, prescription=None,
                          previous_condition=None, followup_days=30, verified_skin_type=None, verified_severity=None):
    # Check-ins are fixed at day 15, 30 and 45 counted in calendar days from the completion date (journey.py);
    # followup_due_at records the first one. followup_days from older callers is ignored.
    import journey
    followup_days = journey.FOLLOWUP_DAYS[0]
    due = journey.opens_at(datetime.now(timezone.utc), followup_days)
    with connect() as c:
        c.execute(
            "UPDATE consultations SET status='completed', doctor_name=%s, symptoms=%s, diagnosis=%s, cure=%s,"
            " prescription=%s, previous_condition=%s, verified_skin_type=%s, verified_severity_json=%s,"
            " followup_days=%s, followup_due_at=%s, completed_at=%s WHERE id=%s AND status='awaiting_doctor'",
            (doctor_name, symptoms, diagnosis, cure, prescription, previous_condition, verified_skin_type,
             json.dumps(verified_severity) if verified_severity else None, followup_days, due, now(), cid))
        audit("complete", "consultation", cid, doctor_name, {"followup_days": followup_days}, conn=c)
    return get_consultation(cid)


def attach_followup_scan(cid, scan_id):
    with connect() as c:
        c.execute("UPDATE consultations SET followup_scan_id=%s WHERE id=%s AND status='completed'"
                  " AND followup_scan_id IS NULL", (scan_id, cid))
        audit("followup_scan", "consultation", cid, "system", {"scan_id": scan_id}, conn=c)
    return get_consultation(cid)


def add_followup(cid, day, scan_id):
    """Records the day-15/30/45 check-in scan (and keeps the legacy followup_scan_id pointing at the first one)."""
    with connect() as c:
        c.execute("INSERT INTO followups(id, consultation_id, day, scan_id, created_at) VALUES (%s,%s,%s,%s,%s)",
                  (str(uuid.uuid4()), cid, int(day), scan_id, now()))
        c.execute("UPDATE consultations SET followup_scan_id = %s WHERE id = %s AND followup_scan_id IS NULL", (scan_id, cid))
        audit("followup_scan", "consultation", cid, "system", {"scan_id": scan_id, "day": int(day)}, conn=c)


def followups_for(cids):
    """{consultation_id: [{'day', 'scan_id', 'created_at'}...]} in day order, for one or many consultations."""
    if not cids:
        return {}
    with connect() as c:
        rows = c.execute("SELECT consultation_id, day, scan_id, created_at FROM followups WHERE consultation_id = ANY(%s) ORDER BY day",
                         (list(cids),)).fetchall()
    out = {}
    for r in rows:
        out.setdefault(r["consultation_id"], []).append({"day": r["day"], "scan_id": r["scan_id"], "created_at": r["created_at"]})
    return out


def patient_consultations(pid):
    with connect() as c:
        return [_row(r) for r in c.execute("SELECT * FROM consultations WHERE patient_id = %s ORDER BY created_at DESC", (pid,)).fetchall()]


def patient_baseline_scans(pid):
    """A patient's scans excluding follow-up check-ins, newest first (id, captured_at)."""
    with connect() as c:
        return [dict(r) for r in c.execute(
            "SELECT s.id, s.captured_at FROM scans s WHERE s.patient_id = %s"
            " AND s.id NOT IN (SELECT scan_id FROM followups)"
            " AND s.id NOT IN (SELECT followup_scan_id FROM consultations WHERE followup_scan_id IS NOT NULL)"
            " ORDER BY s.captured_at DESC", (pid,)).fetchall()]


def completed_consultations(limit=500):
    with connect() as c:
        rows = [_row(r) for r in c.execute("SELECT * FROM consultations WHERE status='completed' ORDER BY completed_at DESC LIMIT %s", (limit,)).fetchall()]
        for con in rows:
            con["patient"] = _patient_row(c.execute("SELECT * FROM patients WHERE id = %s", (con["patient_id"],)).fetchone())
    return rows


def pending_followups(limit=200):
    with connect() as c:
        rows = [_row(r) for r in c.execute(
            "SELECT * FROM consultations WHERE status='completed' AND followup_scan_id IS NULL"
            " AND followup_due_at IS NOT NULL AND followup_due_at <= %s ORDER BY followup_due_at LIMIT %s",
            (now(), limit)).fetchall()]
        for con in rows:
            con["patient"] = _patient_row(c.execute("SELECT * FROM patients WHERE id = %s", (con["patient_id"],)).fetchone())
    return rows


# ------------------------------------------------------------------- users
def create_user(email, password_hash, full_name, role, patient_id=None):
    if role not in ROLES:
        raise ValueError(f"role must be one of {ROLES}")
    uid = str(uuid.uuid4())
    with connect() as c:
        c.execute("INSERT INTO users(id, email, password_hash, full_name, role, patient_id, created_at, user_code)"
                  " VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                  (uid, email.strip().lower(), password_hash, full_name, role, patient_id, now(), _next_user_code(c, role)))
        audit("create", "user", uid, actor="system", details={"role": role}, conn=c)
    return get_user(uid)


def get_user(uid):
    with connect() as c:
        return _row(c.execute("SELECT * FROM users WHERE id = %s", (uid,)).fetchone())


def get_user_by_email(email):
    with connect() as c:
        return _row(c.execute("SELECT * FROM users WHERE email = %s", (email.strip().lower(),)).fetchone())


def touch_user_login(uid):
    with connect() as c:
        c.execute("UPDATE users SET last_login_at = %s WHERE id = %s", (now(), uid))


def list_users(role=None):
    q, args = "SELECT * FROM users", []
    if role:
        q += " WHERE role = %s"
        args.append(role)
    q += " ORDER BY created_at DESC"
    with connect() as c:
        return [_row(r) for r in c.execute(q, args).fetchall()]


def delete_user(uid):
    with connect() as c:
        c.execute("DELETE FROM users WHERE id = %s", (uid,))


try:
    init_db()
except Exception as e:
    print(f"[database] Startup notice: {e}")

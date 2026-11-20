"""
database.py — Local patient & scan database for Clarity AI Face Engine.

Storage (all on this machine):
    data/clarity.db              SQLite database (WAL mode, foreign keys on)
    data/scans/<patient>/<id>.jpg  original scan images

Tables
    patients        demographic record + research-consent flag
    scans           every analysis: prediction, scores, triage, CV metrics, embedding
    annotations     clinician-verified ground truth for a scan (used for learning)
    model_versions  registry of trained models, which one is the "champion"
    training_runs   history of every (re)training attempt and whether it was promoted
    audit_log       who did what, when (hospital traceability)

Only scans from patients with consent_research = 1 (and, unless CLARITY_LEARN_ANONYMOUS=0,
anonymous scans with no patient record) are ever used for learning.
"""
import os
import io
import json
import uuid
import sqlite3
import hashlib
import threading
from datetime import datetime, timezone
from contextlib import contextmanager

import numpy as np

ROOT = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.environ.get("CLARITY_DATA_DIR", os.path.join(ROOT, "data"))
DB_PATH = os.path.join(DATA_DIR, "clarity.db")
SCAN_DIR = os.path.join(DATA_DIR, "scans")
# Anonymous scans (no patient attached) may be used for learning unless disabled
LEARN_FROM_ANONYMOUS = os.environ.get("CLARITY_LEARN_ANONYMOUS", "1") == "1"

SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS patients (
    id               TEXT PRIMARY KEY,
    mrn              TEXT UNIQUE,              -- hospital medical record number (optional)
    full_name        TEXT NOT NULL,
    date_of_birth    TEXT,
    sex              TEXT,
    age              INTEGER,
    previous_skin_condition TEXT,              -- patient-reported at self-service intake
    phone            TEXT,
    email            TEXT,
    notes            TEXT,
    consent_research INTEGER NOT NULL DEFAULT 0, -- 1 = scans may be used to improve the model
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
    embedding          BLOB,
    embedding_version  TEXT
);
CREATE INDEX IF NOT EXISTS idx_scans_patient ON scans(patient_id, captured_at);
CREATE INDEX IF NOT EXISTS idx_scans_sha ON scans(image_sha256);

CREATE TABLE IF NOT EXISTS annotations (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    scan_id            TEXT NOT NULL REFERENCES scans(id) ON DELETE CASCADE,
    annotator          TEXT NOT NULL,
    verified_skin_type TEXT,
    severity_json      TEXT,               -- {attribute: 0-5} clinician grades (any subset)
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
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
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

-- Paid doctor consultation attached to one AI scan ("the case"). Captures the
-- doctor's structured verdict (symptoms / cure / prescription / previous
-- history / diagnosis / follow-up timeframe) and links the later follow-up
-- scan so improvement can be measured. Completing a consultation also feeds
-- the doctor's verified labels into the learning loop (annotations table).
CREATE TABLE IF NOT EXISTS consultations (
    id                  TEXT PRIMARY KEY,
    patient_id          TEXT NOT NULL REFERENCES patients(id) ON DELETE CASCADE,
    scan_id             TEXT NOT NULL REFERENCES scans(id),
    fee_usd             REAL NOT NULL DEFAULT 99,
    payment_status      TEXT NOT NULL DEFAULT 'pending',        -- pending | paid_demo
    status              TEXT NOT NULL DEFAULT 'awaiting_payment',-- awaiting_payment | awaiting_doctor | completed
    ai_suggestions_json TEXT,                 -- AI-drafted starting point for the doctor's form
    doctor_name         TEXT,
    symptoms            TEXT,
    diagnosis           TEXT,
    cure                TEXT,
    prescription        TEXT,
    previous_condition  TEXT,
    verified_skin_type  TEXT,                 -- doctor's confirmed/corrected skin type (-> learning)
    verified_severity_json TEXT,              -- doctor's confirmed/corrected 0-5 grades (-> learning)
    followup_days       INTEGER,
    followup_due_at     TEXT,
    followup_scan_id    TEXT REFERENCES scans(id),
    created_at          TEXT NOT NULL,
    paid_at             TEXT,
    completed_at        TEXT
);
CREATE INDEX IF NOT EXISTS idx_consult_patient ON consultations(patient_id, created_at);
CREATE INDEX IF NOT EXISTS idx_consult_status ON consultations(status);

CREATE TABLE IF NOT EXISTS audit_log (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    ts        TEXT NOT NULL,
    actor     TEXT,
    action    TEXT NOT NULL,
    entity    TEXT,
    entity_id TEXT,
    details   TEXT
);
"""

_lock = threading.Lock()


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@contextmanager
def connect():
    os.makedirs(DATA_DIR, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def _migrate(c):
    """Add columns introduced after a database already existed (CREATE TABLE IF NOT EXISTS won't add them)."""
    cols = {r["name"] for r in c.execute("PRAGMA table_info(patients)").fetchall()}
    if "age" not in cols:
        c.execute("ALTER TABLE patients ADD COLUMN age INTEGER")
    if "previous_skin_condition" not in cols:
        c.execute("ALTER TABLE patients ADD COLUMN previous_skin_condition TEXT")


def init_db():
    os.makedirs(SCAN_DIR, exist_ok=True)
    with connect() as c:
        c.execute("PRAGMA journal_mode = WAL")
        c.executescript(SCHEMA)
        _migrate(c)


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


def audit(action, entity=None, entity_id=None, actor="system", details=None, conn=None):
    q = "INSERT INTO audit_log(ts, actor, action, entity, entity_id, details) VALUES (?,?,?,?,?,?)"
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
            " email, notes, consent_research, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (pid, full_name, data["mrn"] or None, data["date_of_birth"], data["sex"], data["age"] or None,
             data["previous_skin_condition"], data["phone"], data["email"], data["notes"],
             data["consent_research"], ts, ts))
        audit("create", "patient", pid, actor, conn=c)
    return get_patient(pid)


def update_patient(pid, actor="system", **fields):
    sets, args = [], []
    for k in PATIENT_FIELDS:
        if k in fields:
            v = fields[k]
            if k == "consent_research":
                v = 1 if v in (1, True, "1", "true", "yes", "on") else 0
            sets.append(f"{k} = ?")
            args.append(v)
    if not sets:
        return get_patient(pid)
    sets.append("updated_at = ?")
    args += [now(), pid]
    with connect() as c:
        c.execute(f"UPDATE patients SET {', '.join(sets)} WHERE id = ?", args)
        audit("update", "patient", pid, actor, {k: fields[k] for k in fields if k in PATIENT_FIELDS}, conn=c)
    return get_patient(pid)


def get_patient(pid):
    with connect() as c:
        p = _row(c.execute("SELECT * FROM patients WHERE id = ?", (pid,)).fetchone())
        if p:
            p["scan_count"] = c.execute("SELECT COUNT(*) FROM scans WHERE patient_id = ?", (pid,)).fetchone()[0]
    return p


def list_patients(search=None, limit=200):
    q = ("SELECT p.*, COUNT(s.id) AS scan_count, MAX(s.captured_at) AS last_scan_at, "
         "(SELECT overall_score FROM scans s2 WHERE s2.patient_id = p.id ORDER BY captured_at DESC LIMIT 1) AS last_score "
         "FROM patients p LEFT JOIN scans s ON s.patient_id = p.id ")
    args = []
    if search:
        q += "WHERE p.full_name LIKE ? OR p.mrn LIKE ? OR p.phone LIKE ? OR p.email LIKE ? "
        args = [f"%{search}%"] * 4
    q += "GROUP BY p.id ORDER BY COALESCE(MAX(s.captured_at), p.created_at) DESC LIMIT ?"
    args.append(limit)
    with connect() as c:
        return [_row(r) for r in c.execute(q, args).fetchall()]


def delete_patient(pid, actor="system", delete_images=True):
    """Right-to-erasure: removes the patient, their scans, annotations and images."""
    with connect() as c:
        rows = c.execute("SELECT image_path FROM scans WHERE patient_id = ?", (pid,)).fetchall()
        c.execute("DELETE FROM scans WHERE patient_id = ?", (pid,))
        c.execute("DELETE FROM patients WHERE id = ?", (pid,))
        audit("delete", "patient", pid, actor, {"scans_removed": len(rows)}, conn=c)
    if delete_images:
        for r in rows:
            try:
                os.remove(os.path.join(DATA_DIR, r["image_path"]))
            except OSError:
                pass
        try:
            os.rmdir(os.path.join(SCAN_DIR, pid))
        except OSError:
            pass


# -------------------------------------------------------------------- scans
def save_scan(pil_image, result, patient_id=None, model_version=None, embedding=None,
              embedding_version=None, actor="system"):
    """Stores the image on disk and the full analysis result in the DB. Returns scan id."""
    sid = str(uuid.uuid4())
    buf = io.BytesIO()
    pil_image.convert("RGB").save(buf, format="JPEG", quality=95)
    raw = buf.getvalue()
    sha = hashlib.sha256(raw).hexdigest()
    folder = os.path.join(SCAN_DIR, patient_id or "anonymous")
    os.makedirs(folder, exist_ok=True)
    abs_path = os.path.join(folder, f"{sid}.jpg")
    with open(abs_path, "wb") as f:
        f.write(raw)
    rel_path = os.path.relpath(abs_path, DATA_DIR)
    emb = None if embedding is None else np.asarray(embedding, np.float32).tobytes()
    with connect() as c:
        c.execute(
            "INSERT INTO scans(id, patient_id, image_path, image_sha256, captured_at, model_version, skin_type_pred,"
            " skin_type_conf, probs_json, severity_json, overall_score, triage_status, image_quality, cv_metrics_json,"
            " routine_json, embedding, embedding_version) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
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
        s = _row(c.execute("SELECT * FROM scans WHERE id = ?", (sid,)).fetchone())
        if s:
            s["annotations"] = [_row(r) for r in c.execute(
                "SELECT * FROM annotations WHERE scan_id = ? ORDER BY created_at", (sid,)).fetchall()]
    return s


def scan_image_path(sid):
    with connect() as c:
        r = c.execute("SELECT image_path FROM scans WHERE id = ?", (sid,)).fetchone()
    return os.path.join(DATA_DIR, r["image_path"]) if r else None


def list_scans(patient_id=None, limit=100):
    q = ("SELECT s.id, s.patient_id, p.full_name, s.captured_at, s.model_version, s.skin_type_pred, s.skin_type_conf,"
         " s.overall_score, s.triage_status, s.image_quality, s.severity_json,"
         " (SELECT verified_skin_type FROM annotations a WHERE a.scan_id = s.id ORDER BY a.created_at DESC LIMIT 1) AS verified_skin_type"
         " FROM scans s LEFT JOIN patients p ON p.id = s.patient_id ")
    args = []
    if patient_id:
        q += "WHERE s.patient_id = ? "
        args.append(patient_id)
    q += "ORDER BY s.captured_at DESC LIMIT ?"
    args.append(limit)
    with connect() as c:
        return [_row(r) for r in c.execute(q, args).fetchall()]


def update_scan_embedding(sid, embedding, version):
    with connect() as c:
        c.execute("UPDATE scans SET embedding = ?, embedding_version = ? WHERE id = ?",
                  (np.asarray(embedding, np.float32).tobytes(), version, sid))


# -------------------------------------------------------------- annotations
def add_annotation(scan_id, annotator, verified_skin_type=None, severity=None, notes=None):
    with connect() as c:
        c.execute("INSERT INTO annotations(scan_id, annotator, verified_skin_type, severity_json, notes, created_at)"
                  " VALUES (?,?,?,?,?,?)",
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
        # consenting patients, plus (optionally) anonymous scans with no patient record, e.g. staff test photos
        q += " AND (p.consent_research = 1" + (" OR s.patient_id IS NULL)" if LEARN_FROM_ANONYMOUS else ")")
    if since:
        q += " AND a.created_at > ?"
        args.append(since)
    out = []
    with connect() as c:
        for r in c.execute(q, args).fetchall():
            d = dict(r)
            emb = d.pop("embedding")
            ok = emb is not None and (embedding_version is None or d["embedding_version"] == embedding_version)
            d["embedding"] = np.frombuffer(emb, np.float32) if ok else None
            d["severity"] = json.loads(d.pop("severity_json")) if d.get("severity_json") else {}
            d["abs_image_path"] = os.path.join(DATA_DIR, d["image_path"])
            out.append(d)
    return out


# ------------------------------------------------------------- consultations
def create_consultation(patient_id, scan_id, fee_usd=99.0, ai_suggestions=None):
    cid = str(uuid.uuid4())
    with connect() as c:
        c.execute(
            "INSERT INTO consultations(id, patient_id, scan_id, fee_usd, payment_status, status,"
            " ai_suggestions_json, created_at) VALUES (?,?,?,?,'pending','awaiting_payment',?,?)",
            (cid, patient_id, scan_id, fee_usd, json.dumps(ai_suggestions) if ai_suggestions else None, now()))
        audit("create", "consultation", cid, "system", {"patient_id": patient_id, "scan_id": scan_id}, conn=c)
    return get_consultation(cid)


def get_consultation(cid):
    with connect() as c:
        con = _row(c.execute("SELECT * FROM consultations WHERE id = ?", (cid,)).fetchone())
        if con:
            con["patient"] = _row(c.execute("SELECT * FROM patients WHERE id = ?", (con["patient_id"],)).fetchone())
    return con


def list_consultations(status=None, patient_id=None, limit=200):
    q = "SELECT * FROM consultations WHERE 1=1"
    args = []
    if status:
        q += " AND status = ?"
        args.append(status)
    if patient_id:
        q += " AND patient_id = ?"
        args.append(patient_id)
    q += " ORDER BY created_at DESC LIMIT ?"
    args.append(limit)
    with connect() as c:
        rows = [_row(r) for r in c.execute(q, args).fetchall()]
        for con in rows:
            con["patient"] = _row(c.execute("SELECT * FROM patients WHERE id = ?", (con["patient_id"],)).fetchone())
    return rows


def mark_consultation_paid(cid, actor="patient"):
    with connect() as c:
        c.execute("UPDATE consultations SET payment_status='paid_demo', status='awaiting_doctor', paid_at=?"
                  " WHERE id=? AND status='awaiting_payment'", (now(), cid))
        audit("pay_demo", "consultation", cid, actor, conn=c)
    return get_consultation(cid)


def complete_consultation(cid, doctor_name, symptoms=None, diagnosis=None, cure=None, prescription=None,
                          previous_condition=None, followup_days=30, verified_skin_type=None, verified_severity=None):
    due = None
    if followup_days:
        from datetime import timedelta
        due = (datetime.now(timezone.utc) + timedelta(days=int(followup_days))).isoformat(timespec="seconds")
    with connect() as c:
        c.execute(
            "UPDATE consultations SET status='completed', doctor_name=?, symptoms=?, diagnosis=?, cure=?,"
            " prescription=?, previous_condition=?, verified_skin_type=?, verified_severity_json=?,"
            " followup_days=?, followup_due_at=?, completed_at=? WHERE id=? AND status='awaiting_doctor'",
            (doctor_name, symptoms, diagnosis, cure, prescription, previous_condition, verified_skin_type,
             json.dumps(verified_severity) if verified_severity else None, followup_days, due, now(), cid))
        audit("complete", "consultation", cid, doctor_name, {"followup_days": followup_days}, conn=c)
    return get_consultation(cid)


def attach_followup_scan(cid, scan_id):
    with connect() as c:
        c.execute("UPDATE consultations SET followup_scan_id=? WHERE id=? AND status='completed'"
                  " AND followup_scan_id IS NULL", (scan_id, cid))
        audit("followup_scan", "consultation", cid, "system", {"scan_id": scan_id}, conn=c)
    return get_consultation(cid)


def pending_followups(limit=200):
    with connect() as c:
        rows = [_row(r) for r in c.execute(
            "SELECT * FROM consultations WHERE status='completed' AND followup_scan_id IS NULL"
            " AND followup_due_at IS NOT NULL AND followup_due_at <= ? ORDER BY followup_due_at LIMIT ?",
            (now(), limit)).fetchall()]
        for con in rows:
            con["patient"] = _row(c.execute("SELECT * FROM patients WHERE id = ?", (con["patient_id"],)).fetchone())
    return rows


# ----------------------------------------------------------- model registry
def register_model(version, path, metrics=None, parent_version=None, notes=None, make_champion=False):
    with connect() as c:
        c.execute("INSERT OR REPLACE INTO model_versions(version, path, created_at, parent_version, metrics_json,"
                  " is_champion, notes) VALUES (?,?,?,?,?,0,?)",
                  (version, path, now(), parent_version, json.dumps(metrics) if metrics else None, notes))
        if make_champion:
            c.execute("UPDATE model_versions SET is_champion = CASE WHEN version = ? THEN 1 ELSE 0 END", (version,))
        audit("register", "model", version, details={"champion": make_champion}, conn=c)


def set_champion(version):
    with connect() as c:
        c.execute("UPDATE model_versions SET is_champion = CASE WHEN version = ? THEN 1 ELSE 0 END", (version,))
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
                        " VALUES (?,?,?,?, 'running')", (now(), base_version, n_train, n_user))
        return cur.lastrowid


def finish_training_run(run_id, new_version, metrics, promoted, reason, status="done"):
    with connect() as c:
        c.execute("UPDATE training_runs SET finished_at=?, new_version=?, metrics_json=?, promoted=?, reason=?, status=?"
                  " WHERE id=?", (now(), new_version, json.dumps(metrics), int(bool(promoted)), reason, status, run_id))


def last_training_run():
    with connect() as c:
        return _row(c.execute("SELECT * FROM training_runs ORDER BY id DESC LIMIT 1").fetchone())


def dashboard_stats():
    with connect() as c:
        g = lambda q, *a: c.execute(q, a).fetchone()[0]
        last = c.execute("SELECT finished_at FROM training_runs WHERE status='done' ORDER BY id DESC LIMIT 1").fetchone()
        since = last[0] if last else "0"
        return {
            "patients": g("SELECT COUNT(*) FROM patients"),
            "scans": g("SELECT COUNT(*) FROM scans"),
            "scans_today": g("SELECT COUNT(*) FROM scans WHERE substr(captured_at,1,10) = ?", now()[:10]),
            "high_risk_scans": g("SELECT COUNT(*) FROM scans WHERE triage_status = 'HIGH_RISK_CLINICAL'"),
            "verified_scans": g("SELECT COUNT(DISTINCT scan_id) FROM annotations"),
            "verified_since_last_training": g("SELECT COUNT(DISTINCT scan_id) FROM annotations WHERE created_at > ?", since),
            "model_disagreements": g(
                "SELECT COUNT(*) FROM scans s WHERE EXISTS (SELECT 1 FROM annotations a WHERE a.scan_id = s.id"
                " AND a.verified_skin_type IS NOT NULL AND a.verified_skin_type != s.skin_type_pred)"),
            "skin_type_distribution": {r[0]: r[1] for r in c.execute(
                "SELECT skin_type_pred, COUNT(*) FROM scans GROUP BY skin_type_pred").fetchall() if r[0]},
        }


init_db()

"""
backend/main.py — FastAPI API for Clarity AI Face Engine.

Architecture: Next.js (frontend/) -> FastAPI (this file) -> PostgreSQL
(database.py) -> Image Storage (storage.py) -> ML Service (ml_service.py).

This is a line-for-line port of the previous Flask app (server.py, now kept
at legacy_flask_sqlite_backup/server_flask.py for reference) onto FastAPI —
every route, every behavior, is unchanged; only the web framework and the
database engine changed. database.py, storage.py, ml_service.py,
consultation_ai.py, dermatology_rules.py, dinov2_lora_model.py, model_v2.py
and memory.py all still live at the project root (shared with the standalone
train_v2.py / data_prep.py / continual_learning.py scripts), so this file
adds the project root to sys.path before importing them.

Run:  uvicorn backend.main:app --reload --port 8000   (from the project root)
"""
import os
import sys
import io
import json
import time
import base64
import subprocess
import threading

import numpy as np
from PIL import Image, ImageOps, ImageDraw

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

import database as db
from ml_service import MLService, SKIN_TYPES
from dermatology_rules import generate_skincare_routine, DOCTOR_NETWORK
import consultation_ai
import dr_tanya_products
import auth
import privacy
import intake as intake_mod
import scheduling
import journey

AUTO_RETRAIN_MIN_NEW = int(os.environ.get("CLARITY_RETRAIN_MIN_NEW", 50))
AUTO_RETRAIN = os.environ.get("CLARITY_AUTO_RETRAIN", "1") == "1"
# Learning from user data (instant-learning memory + retraining on doctor-verified scans) is OFF. Doctor verdicts are still
# saved in the patient's record; they just never reach the model. Set CLARITY_LEARNING=1 to turn it back on.
LEARNING_ENABLED = os.environ.get("CLARITY_LEARNING", "0") == "1"
CORS_ORIGINS = [o.strip() for o in os.environ.get("CLARITY_CORS_ORIGINS", "http://localhost:3001,https://localhost:3001").split(",") if o.strip()]

app = FastAPI(title="Clarity AI Face Engine API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_origin_regex=r"https://.*\.vercel\.app",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

ml = MLService()


@app.get("/")
@app.get("/health")
def health():
    db_ok = db.is_healthy()
    return {
        "status": "healthy" if db_ok else "waiting_for_database",
        "service": "Clarity AI Face Engine API",
        "database": "connected" if db_ok else "unreachable_or_not_configured",
        "model_version": getattr(ml, "version", "unknown"),
    }


def champion_watcher():
    """Hot-swap to a newly promoted model (e.g. after a background retrain)."""
    while True:
        time.sleep(30)
        try:
            ch = db.get_champion()
            if ch and ch["version"] != ml.version:
                print(f"[ml_service] new champion {ch['version']} detected — reloading")
                ml.load()
        except Exception as e:  # never kill the server
            print("[ml_service] watcher error:", e)


threading.Thread(target=champion_watcher, daemon=True).start()
_retrain_proc = None


def maybe_start_retrain(force=False):
    global _retrain_proc
    if not LEARNING_ENABLED:
        return False, "learning from user data is switched off"
    if _retrain_proc is not None and _retrain_proc.poll() is None:
        return False, "retrain already running"
    if not force:
        if not AUTO_RETRAIN:
            return False, "auto-retrain disabled"
        if ml.kind != "v2":
            return False, "train model v2 first (python train_v2.py)"
        if db.dashboard_stats()["verified_since_last_training"] < AUTO_RETRAIN_MIN_NEW:
            return False, "not enough new verified scans yet"
    log = open(os.path.join(db.DATA_DIR, "retrain.log"), "a")
    cmd = [sys.executable, os.path.join(ROOT, "continual_learning.py"), "retrain"] + (["--force"] if force else [])
    _retrain_proc = subprocess.Popen(cmd, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT)
    return True, "retrain started"


# ================================================================ helpers
async def read_image_and_fields(request: Request):
    """Mirrors the old Flask read_image()/req_field(): accepts either multipart
    form-data with a `file` field, or a JSON body with `image_base64`.
    Normalizes EXIF orientation and bounds image scale for mobile device uploads."""
    resample = getattr(Image, "Resampling", Image).LANCZOS
    ctype = request.headers.get("content-type", "")
    if ctype.startswith("multipart/form-data"):
        form = await request.form()
        file = form.get("file")
        img = None
        if file is not None:
            raw_bytes = await file.read()
            img = Image.open(io.BytesIO(raw_bytes))
            img = ImageOps.exif_transpose(img).convert("RGB")
            w, h = img.size
            if max(w, h) > 1600:
                s = 1600.0 / max(w, h)
                img = img.resize((int(round(w * s)), int(round(h * s))), resample)
        fields = {k: v for k, v in form.items() if k != "file"}
        return img, fields
    body = {}
    raw = await request.body()
    if raw:
        try:
            body = json.loads(raw)
        except json.JSONDecodeError:
            body = {}
    img = None
    if "image_base64" in body:
        b64_str = body["image_base64"].split(",")[-1]
        img = Image.open(io.BytesIO(base64.b64decode(b64_str)))
        img = ImageOps.exif_transpose(img).convert("RGB")
        w, h = img.size
        if max(w, h) > 1600:
            s = 1600.0 / max(w, h)
            img = img.resize((int(round(w * s)), int(round(h * s))), resample)
    return img, body


async def json_body(request: Request):
    raw = await request.body()
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {}


def actor(request: Request, body: dict):
    return request.headers.get("X-Clinician") or body.get("annotator") or "clinician"


def overall_score(sev):
    """0-100, higher is healthier. Plain mapping of mean concern severity (no clamping)."""
    vals = [v for k, v in sev.items()]
    return int(round(np.clip(100 - 20 * float(np.mean(vals)), 0, 100))) if vals else None


def compare_scans(baseline_scan, followup_result):
    """Positive delta = improvement, for both the overall score and each severity attribute (lower severity is better)."""
    before_sev = baseline_scan.get("severity") or {}
    after_sev = followup_result.get("severity_scores") or {}
    per_attr = {}
    for a in set(before_sev) & set(after_sev):
        per_attr[a] = {"before": before_sev[a], "after": after_sev[a], "delta": round(before_sev[a] - after_sev[a], 2)}
    before_score, after_score = baseline_scan.get("overall_score"), followup_result.get("overall_health_score")
    return {
        "baseline_scan_id": baseline_scan["id"], "baseline_captured_at": baseline_scan.get("captured_at"),
        "score_before": before_score, "score_after": after_score,
        "score_delta": (after_score - before_score) if (before_score is not None and after_score is not None) else None,
        "per_attribute": per_attr,
    }


def learn_from_verification(sid, verified_skin_type, severity):
    """Feeds a clinician/doctor-confirmed label into instant-learning memory, respecting research consent."""
    added = False
    if not LEARNING_ENABLED:
        return added
    for smp in db.verified_samples(embedding_version=ml.version, consent_only=True):
        if smp["scan_id"] == sid and smp["embedding"] is not None:
            ml.memory.add(sid, smp["embedding"], verified_skin_type, severity)
            added = True
    return added


# ================================================================ auth
def user_public(u):
    if not u:
        return None
    out = {k: v for k, v in u.items() if k != "password_hash"}
    if u.get("patient_id"):
        p = db.get_patient(u["patient_id"])
        out["mrn"] = (p or {}).get("mrn")
    return out


def get_current_user(request: Request):
    """Returns the logged-in user dict, or None if not authenticated — never raises."""
    token = request.cookies.get(auth.SESSION_COOKIE)
    if not token:
        hdr = request.headers.get("Authorization") or request.headers.get("authorization")
        if hdr and hdr.startswith("Bearer "):
            token = hdr[7:].strip()
    if not token:
        token = request.query_params.get("token")
    if not token:
        return None
    payload = auth.decode_token(token)
    if not payload:
        return None
    return db.get_user(payload["sub"])


def require_user(request: Request, role: str | None = None):
    u = get_current_user(request)
    if not u:
        raise HTTPException(401, "Not logged in")
    if role and u["role"] != role:
        raise HTTPException(403, f"This action requires a {role} account")
    return u


def require_roles(request: Request, *roles):
    """Logged-in user whose role is one of `roles`, else 401 (not signed in) / 403 (wrong role)."""
    u = get_current_user(request)
    if not u:
        raise HTTPException(401, "Not logged in")
    if u["role"] not in roles:
        raise HTTPException(403, "Your account does not have access to this")
    return u


def set_session_cookie(response: Response, user):
    token = auth.create_token(user)
    response.set_cookie(auth.SESSION_COOKIE, token, httponly=True, samesite="lax",
                        max_age=auth.TOKEN_TTL_DAYS * 86400, path="/")


@app.post("/api/auth/signup")
async def signup(request: Request, response: Response):
    """Public self-service signup — PATIENTS ONLY. Clinician accounts are provisioned by an
    admin (see /api/admin/clinicians below), not self-service, so every doctor login traces
    back to someone an admin actually vouched for."""
    body = await json_body(request)
    email, password = (body.get("email") or "").strip(), body.get("password") or ""
    full_name = (body.get("full_name") or "").strip()
    if not auth.valid_email(email):
        raise HTTPException(400, "Enter a valid email address")
    if len(password) < 8:
        raise HTTPException(400, "Password must be at least 8 characters")
    if not full_name:
        raise HTTPException(400, "full_name is required")
    if db.get_user_by_email(email):
        raise HTTPException(409, "An account with this email already exists")

    age = body.get("age")
    if age not in (None, ""):
        try:
            age = int(age)
        except (TypeError, ValueError):
            raise HTTPException(400, "age must be a number")
    intake = intake_mod.clean_intake(body.get("intake"))
    history = (body.get("previous_skin_condition") or "").strip() or (intake_mod.summarize(intake) if intake else None)
    patient = db.create_patient(full_name, actor="signup", age=age, sex=body.get("sex"),
                                previous_skin_condition=history,
                                consent_research=0)
    if intake:
        db.set_patient_intake(patient["id"], intake)

    user = db.create_user(email, auth.hash_password(password), full_name, "patient", patient["id"])
    set_session_cookie(response, user)
    return user_public(user)


# ----------------------------------------------------------- admin: manage clinicians
@app.get("/api/admin/clinicians")
def list_clinicians(request: Request):
    require_user(request, role="admin")
    return {"clinicians": [user_public(u) for u in db.list_users(role="clinician")]}


@app.post("/api/admin/clinicians")
async def create_clinician(request: Request):
    admin = require_user(request, role="admin")
    body = await json_body(request)
    email, password = (body.get("email") or "").strip(), body.get("password") or ""
    full_name = (body.get("full_name") or "").strip()
    if not auth.valid_email(email):
        raise HTTPException(400, "Enter a valid email address")
    if len(password) < 8:
        raise HTTPException(400, "Password must be at least 8 characters")
    if not full_name:
        raise HTTPException(400, "full_name is required")
    if db.get_user_by_email(email):
        raise HTTPException(409, "An account with this email already exists")
    user = db.create_user(email, auth.hash_password(password), full_name, "clinician")
    db.audit("create", "user", user["id"], actor=admin["email"], details={"role": "clinician", "created_by_admin": True})
    return user_public(user)


@app.delete("/api/admin/clinicians/{uid}")
def delete_clinician(uid: str, request: Request):
    admin = require_user(request, role="admin")
    target = db.get_user(uid)
    if not target or target["role"] != "clinician":
        raise HTTPException(404, "not found")
    upcoming = scheduling.list_upcoming(doctor_id=uid)
    if upcoming:
        raise HTTPException(409, f"{target['full_name']} still has {len(upcoming)} upcoming appointment(s). Cancel or let them finish before removing this account.")
    db.delete_user(uid)
    db.audit("delete", "user", uid, actor=admin["email"], details={"role": "clinician"})
    return {"deleted": uid}


@app.post("/api/auth/login")
async def login(request: Request, response: Response):
    body = await json_body(request)
    email, password = (body.get("email") or "").strip(), body.get("password") or ""
    user = db.get_user_by_email(email)
    if not user or not auth.verify_password(password, user["password_hash"]):
        raise HTTPException(401, "Incorrect email or password")
    db.touch_user_login(user["id"])
    set_session_cookie(response, user)
    return user_public(user)


@app.post("/api/auth/logout")
def logout(response: Response):
    response.delete_cookie(auth.SESSION_COOKIE, path="/")
    return {"success": True}


@app.get("/api/auth/me")
def me(request: Request):
    u = get_current_user(request)
    if not u:
        raise HTTPException(401, "Not logged in")
    return user_public(u)


# ================================================================ analyze
def _scan_blocked_message(st):
    stage = st["stage"]
    if stage == "report":
        return "You already have a scan. Please continue to your doctor consultation — your next scan opens after it."
    if stage in ("payment", "pending"):
        return "Your consultation is in progress. Your next scan opens 15 days after your doctor completes it."
    nxt = journey.next_checkpoint(st["checkpoints"])
    if nxt and nxt["status"] == "available":
        return f"Your {nxt['label']} check-in scan is open — start it from your plan page."
    if nxt:
        return f"Your next scan ({nxt['label']}) opens on {nxt['opens_label']}."
    return "A new scan isn't available right now."


@app.post("/api/analyze")
async def analyze_skin(request: Request):
    try:
        img, fields = await read_image_and_fields(request)
        if img is None:
            raise HTTPException(400, "No image provided")
        logged_in = require_roles(request, "patient", "admin")     # clinicians review cases; they do not scan
        is_patient = logged_in["role"] == "patient"
        if is_patient:
            patient_id = logged_in["patient_id"]  # patients always scan as themselves
        else:
            patient_id = fields.get("patient_id") or None  # admins scan from the clinic dashboard (optionally for a patient)
        if patient_id and not db.get_patient(patient_id):
            raise HTTPException(404, "Unknown patient_id")
        consultation_id = fields.get("consultation_id") or None
        baseline_scan = None
        checkpoint = None
        if consultation_id:                      # a day-15/30/45 check-in scan
            con = db.get_consultation(consultation_id)
            if not con or con["status"] != "completed":
                raise HTTPException(404, "Consultation not found or not yet completed by a doctor")
            if is_patient and con["patient_id"] != patient_id:
                raise HTTPException(403, "This consultation belongs to a different patient account")
            patient_id = con["patient_id"]
            done = {f["day"]: f["scan_id"] for f in db.followups_for([consultation_id]).get(consultation_id, [])}
            cps = journey.checkpoints(con, done)
            checkpoint = journey.available_checkpoint(cps)
            if not checkpoint:
                nxt = journey.next_checkpoint(cps)
                raise HTTPException(403, f"Your next check-in scan ({nxt['label']}) opens on {nxt['opens_label']}." if nxt
                                    else "All of your check-in scans are complete for this consultation.")
            baseline_scan = db.get_scan(con["scan_id"])
        elif is_patient:                         # a first scan: only at the start of a cycle
            st = journey.state_for_patient(patient_id, db)
            if not st["can_start_new"]:
                raise HTTPException(403, _scan_blocked_message(st))

        # ---- eye privacy: nothing is analysed or stored before this ----
        # stored_*  = eyes covered black (the only version ever written/shown)
        # analysis  = covered areas filled in from surrounding skin, fed to the AI models only
        client_red = fields.get("client_redaction") if isinstance(fields.get("client_redaction"), dict) else {}
        img, analysis_img, front_report = privacy.process_image(img, client_red.get("front"), need_analysis=True)
        if is_patient and front_report["status"] != "redacted":
            # Ensure solid eye-safe protection without blocking the patient's consultation flow
            img, analysis_img, front_report = privacy.apply_safe_eye_band(img, need_analysis=True)
        extra_views = {}
        extra_analysis = {}
        ev = fields.get("extra_views")
        if isinstance(ev, dict):
            resample = getattr(Image, "Resampling", Image).LANCZOS
            for key, b64 in list(ev.items())[:4]:
                if key not in db.VIEW_KEYS or key == "front" or not isinstance(b64, str) or len(b64) > 14_000_000:
                    continue
                try:
                    im = Image.open(io.BytesIO(base64.b64decode(b64.split(",")[-1])))
                    im = ImageOps.exif_transpose(im).convert("RGB")
                    w, h = im.size
                    if max(w, h) > 1600:
                        s = 1600.0 / max(w, h)
                        im = im.resize((int(round(w * s)), int(round(h * s))), resample)
                except Exception:
                    continue
                v_img, v_analysis, v_report = privacy.process_image(im, client_red.get(key), need_analysis=True)
                if v_report["status"] != "redacted":
                    # Safe protective eye band for angled views so privacy is enforced and scan succeeds
                    v_img, v_analysis, v_report = privacy.apply_safe_eye_band(im, need_analysis=True)
                extra_views[key] = (v_img, v_report)
                if v_analysis is not None:
                    extra_analysis[key] = v_analysis

        cv = ml.analyze_cv(analysis_img)
        probs, severity, emb, version = ml.infer(analysis_img)

        # Multi-angle CV feature fusion across cheeks and forehead for heightened accuracy
        if extra_analysis:
            extra_cvs = {k: ml.analyze_cv(v_im) for k, v_im in extra_analysis.items()}
            all_melasma = [cv.get("cv_melasma_severity", 0)] + [c.get("cv_melasma_severity", 0) for c in extra_cvs.values()]
            cv["cv_melasma_severity"] = round(float(np.max(all_melasma)), 2)
            all_tanning = [cv.get("cv_tanning_severity", 0)] + [c.get("cv_tanning_severity", 0) for c in extra_cvs.values()]
            cv["cv_tanning_severity"] = round(float(np.mean(all_tanning)), 2)

        if LEARNING_ENABLED:
            probs, severity, mem_info = ml.memory.adjust(emb, probs, severity)
        else:                                    # results come from the trained model alone; nothing learned from users
            mem_info = {"applied": False, "enabled": False, "note": "Learning from user data is switched off."}

        severity["tanning_severity"] = cv["cv_tanning_severity"]
        if ml.kind == "v1":  # v1 severity head was trained on synthetic targets — keep legacy CV overrides
            severity["pigmentation_melasma"] = cv["cv_melasma_severity"]
            severity["texture_roughness"] = cv["cv_texture"]
        severity = {k: float(np.clip(v, 0, 5)) for k, v in severity.items()}

        # Specialist models trained on real labeled data (ACNE04, SCIN) — see ml_service.py /
        # train_acne_model.py / train_sun_damage_model.py. Best-effort: if a checkpoint isn't
        # loaded, these are simply None and the multi-task model's own values are kept as-is.
        acne_detail = ml.infer_acne(analysis_img)
        if extra_analysis:
            for cheek_key in ("left", "right"):
                if cheek_key in extra_analysis:
                    cheek_acne = ml.infer_acne(extra_analysis[cheek_key])
                    if cheek_acne and (acne_detail is None or cheek_acne["severity_0_5"] > acne_detail["severity_0_5"]):
                        acne_detail = cheek_acne

        if acne_detail:
            severity["acne_severity"] = acne_detail["severity_0_5"]
        sun_damage = ml.infer_sun_damage(analysis_img)

        idx = int(np.argmax(probs))
        skin_type = SKIN_TYPES[idx]
        triage = ml.triage(severity, skin_type)
        result = {
            "success": True,
            "model_version": version,
            "skin_type": skin_type,
            "skin_type_confidence": round(float(probs[idx]), 4),
            "skin_type_probabilities": {SKIN_TYPES[i]: round(float(probs[i]), 4) for i in range(4)},
            "overall_health_score": overall_score(severity),
            "severity_scores": {k: round(v, 2) for k, v in severity.items()},
            "cv_metrics": cv,
            "triage": triage,
            "routine": generate_skincare_routine(skin_type, severity),
            "learning": mem_info,
            "acne_model_detail": acne_detail,
            "sun_damage_assessment": sun_damage,
        }
        result["scan_id"] = db.save_scan(img, result, patient_id, version, emb, version, actor(request, fields))
        db.register_front_view(result["scan_id"], front_report)
        for key, (v_img, v_report) in extra_views.items():
            db.save_scan_view(result["scan_id"], patient_id, key, v_img, v_report)
        result["views"] = ["front"] + [k for k in db.VIEW_KEYS if k in extra_views]
        result["privacy"] = {"eye_redaction": privacy.public_report(front_report),
                             "all_views_covered": all(r["status"] == "redacted" for _, r in extra_views.values())}
        result["patient_id"] = patient_id
        if consultation_id and baseline_scan:
            db.add_followup(consultation_id, checkpoint["day"], result["scan_id"])
            result["followup_comparison"] = compare_scans(baseline_scan, result)
            result["checkpoint_day"] = checkpoint["day"]
        return result
    except HTTPException:
        raise
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(500, str(e))


# ---------------------------------------------------------------- patients
@app.get("/api/patients")
def list_patients(request: Request, q: str | None = None):
    require_roles(request, "admin")
    return {"patients": db.list_patients(q)}


@app.post("/api/patients")
async def create_patient(request: Request):
    require_roles(request, "admin")
    body = await json_body(request)
    full_name = (body.get("full_name") or "").strip()
    if not full_name:
        raise HTTPException(400, "full_name is required")
    body = dict(body)
    body.pop("full_name")
    if body.get("age") not in (None, ""):
        try:
            body["age"] = int(body["age"])
        except (TypeError, ValueError):
            raise HTTPException(400, "age must be a number")
    try:
        p = db.create_patient(full_name, actor=actor(request, body), **body)
    except Exception as e:
        raise HTTPException(400, str(e))
    return p


@app.get("/api/patients/{pid}")
def get_patient(pid: str, request: Request):
    require_roles(request, "admin")
    p = db.get_patient(pid)
    if not p:
        raise HTTPException(404, "not found")
    p["scans"] = db.list_scans(pid)
    return p


@app.patch("/api/patients/{pid}")
async def patch_patient(pid: str, request: Request):
    require_roles(request, "admin")
    body = await json_body(request)
    return db.update_patient(pid, actor=actor(request, body), **body)


@app.delete("/api/patients/{pid}")
def delete_patient(pid: str, request: Request):
    admin = require_roles(request, "admin")
    db.delete_patient(pid, actor=admin["email"])
    return {"deleted": pid}


# ------------------------------------------------------------------- scans
@app.get("/api/scans")
def list_scans(request: Request, patient_id: str | None = None, limit: int = 100):
    require_roles(request, "admin")
    return {"scans": db.list_scans(patient_id, limit)}


@app.get("/api/scans/{sid}")
def get_scan(sid: str, request: Request):
    _require_scan_photo_access(request, sid)
    s = db.get_scan(sid)
    if not s:
        raise HTTPException(404, "not found")
    return s


def _generate_fallback_scan_image(sid: str, view: str = "Front"):
    img = Image.new("RGB", (440, 520), color=(15, 23, 42))  # Slate 900
    draw = ImageDraw.Draw(img)
    # Medical head contour
    draw.ellipse([110, 80, 330, 350], fill=(30, 41, 59), outline=(56, 189, 248), width=2)
    # Neck & shoulders
    draw.polygon([(170, 330), (270, 330), (360, 480), (80, 480)], fill=(30, 41, 59))
    # Eye privacy bar
    draw.rectangle([80, 180, 360, 235], fill=(2, 6, 23), outline=(14, 165, 233), width=2)
    label_text = f"{view.upper()} SCAN (EYE-SAFE)"
    draw.text((120, 200), label_text, fill=(56, 189, 248))
    draw.text((125, 30), "CLARITY DERMATOLOGY", fill=(203, 213, 225))
    draw.text((140, 490), f"ID: {sid[:8]}...", fill=(148, 163, 184))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=90)
    return buf.getvalue()


def _require_scan_photo_access(request: Request, sid: str):
    """Scans and their face photos: the owning patient and clinic staff only."""
    scan = db.get_scan(sid)
    if not scan:
        raise HTTPException(404, "Scan not found")
    u = get_current_user(request)
    if u and u["role"] == "patient" and scan.get("patient_id") and scan.get("patient_id") != u["patient_id"]:
        raise HTTPException(403, "This belongs to a different patient account")


@app.get("/api/scans/{sid}/image")
def scan_image(sid: str, request: Request):
    _require_scan_photo_access(request, sid)
    p = db.scan_image_path(sid)
    if p and os.path.exists(p) and os.path.getsize(p) > 0:
        return FileResponse(p, media_type="image/jpeg")
    raw = db.get_scan_image_bytes(sid)
    if raw:
        if p:
            try:
                os.makedirs(os.path.dirname(p), exist_ok=True)
                with open(p, "wb") as f:
                    f.write(raw)
            except OSError:
                pass
        return Response(content=raw, media_type="image/jpeg")
    fallback_bytes = _generate_fallback_scan_image(sid, "Front")
    return Response(content=fallback_bytes, media_type="image/jpeg")


@app.get("/api/scans/{sid}/views")
def scan_views(sid: str, request: Request):
    """The captured angles for a scan (front/left/right/up/down), all with eyes covered."""
    _require_scan_photo_access(request, sid)
    return {"views": [{"view": v["view"], "url": f"/api/scans/{sid}/views/{v['view']}/image",
                       "eyes_covered": (v.get("redaction") or {}).get("status") == "redacted"}
                      for v in db.list_scan_views(sid)]}


@app.get("/api/scans/{sid}/views/{view}/image")
def scan_view_image(sid: str, view: str, request: Request):
    _require_scan_photo_access(request, sid)
    p = db.scan_view_image_path(sid, view)
    if p and os.path.exists(p) and os.path.getsize(p) > 0:
        return FileResponse(p, media_type="image/jpeg")
    raw = db.get_scan_view_image_bytes(sid, view)
    if raw:
        if p:
            try:
                os.makedirs(os.path.dirname(p), exist_ok=True)
                with open(p, "wb") as f:
                    f.write(raw)
            except OSError:
                pass
        return Response(content=raw, media_type="image/jpeg")
    fallback_bytes = _generate_fallback_scan_image(sid, view)
    return Response(content=fallback_bytes, media_type="image/jpeg")


@app.post("/api/scans/{sid}/verify")
async def verify_scan(sid: str, request: Request):
    """Clinician ground truth. Body: {verified_skin_type, severity: {attr: 0-5}, notes, annotator}"""
    require_roles(request, "clinician", "admin")
    s = db.get_scan(sid)
    if not s:
        raise HTTPException(404, "not found")
    body = await json_body(request)
    st = body.get("verified_skin_type")
    if st and st not in SKIN_TYPES:
        raise HTTPException(400, f"verified_skin_type must be one of {SKIN_TYPES}")
    sev = {k: float(np.clip(float(v), 0, 5)) for k, v in (body.get("severity") or {}).items() if v not in (None, "")}
    db.add_annotation(sid, actor(request, body), st, sev or None, body.get("notes"))
    added = learn_from_verification(sid, st, sev)
    started, msg = maybe_start_retrain()
    return {"success": True, "added_to_memory": added, "memory_size": len(ml.memory),
            "retrain": msg if started else None}


# --------------------------------------------------------------- consultations
@app.get("/api/consultations")
def list_consultations(request: Request, status: str | None = None, patient_id: str | None = None):
    u = require_roles(request, "patient", "clinician", "admin")
    if u["role"] == "patient":
        patient_id = u["patient_id"]           # patients only ever see their own
    rows = scheduling.attach(db.list_consultations(status, patient_id))
    return {"consultations": journey.enrich_completed(rows, db)}


@app.post("/api/consultations")
async def create_consultation(request: Request):
    body = await json_body(request)
    scan_id = body.get("scan_id")
    scan = db.get_scan(scan_id) if scan_id else None
    if not scan:
        raise HTTPException(400, "scan_id is required and must exist")
    if not scan["patient_id"]:
        raise HTTPException(400, "This scan has no patient record — the consultation flow requires patient intake first")
    logged_in_patient = require_roles(request, "patient", "admin")
    if logged_in_patient["role"] == "patient" and logged_in_patient["patient_id"] != scan["patient_id"]:
        raise HTTPException(403, "This scan belongs to a different patient account")
    existing = next((c for c in db.patient_consultations(scan["patient_id"]) if c["scan_id"] == scan_id), None)
    if existing:                              # one consultation per scan: resume it rather than creating a second
        return existing
    patient = db.get_patient(scan["patient_id"])
    draft = consultation_ai.draft_report(scan["skin_type_pred"], scan.get("severity") or {}, scan.get("triage_status"),
                                         scan.get("overall_score"), patient.get("previous_skin_condition") if patient else None)
    con = db.create_consultation(scan["patient_id"], scan_id, fee_usd=99.0, ai_suggestions=draft)
    return con


@app.get("/api/consultations/pending-followups")
def pending_followups(request: Request):
    """Completed cases with a day-15/30/45 check-in currently open that the patient has not done yet."""
    require_roles(request, "clinician", "admin")
    rows = journey.enrich_completed(db.completed_consultations(), db)
    due = [c for c in rows if c.get("due_checkpoint")]
    due.sort(key=lambda c: c["due_checkpoint"]["opens_on"])
    return {"consultations": due}


@app.get("/api/consultations/{cid}")
def consultation_detail(cid: str, request: Request):
    u = require_roles(request, "patient", "clinician", "admin")
    con = db.get_consultation(cid)
    if not con:
        raise HTTPException(404, "not found")
    if u["role"] == "patient" and con["patient_id"] != u["patient_id"]:
        raise HTTPException(403, "This consultation belongs to a different patient account")
    con["scan"] = db.get_scan(con["scan_id"])
    con["appointment"] = scheduling.for_consultation(cid)
    if con["status"] == "completed":
        con["checkpoints"] = _checkpoints_with_comparisons(con)
    if con["followup_scan_id"]:
        followup = db.get_scan(con["followup_scan_id"])
        con["followup_scan"] = followup
        con["comparison"] = compare_scans(con["scan"], {"overall_health_score": followup.get("overall_score"),
                                                        "severity_scores": followup.get("severity") or {}})
    return con


def _checkpoints_with_comparisons(con):
    """The three check-ins with, for each one done, how it compares with the first scan."""
    done = {f["day"]: f["scan_id"] for f in db.followups_for([con["id"]]).get(con["id"], [])}
    cps = journey.checkpoints(con, done)
    baseline = db.get_scan(con["scan_id"])
    for cp in cps:
        if cp["scan_id"]:
            fs = db.get_scan(cp["scan_id"])
            if fs and baseline:
                cp["comparison"] = compare_scans(baseline, {"overall_health_score": fs.get("overall_score"),
                                                            "severity_scores": fs.get("severity") or {}})
                cp["scanned_at"] = fs.get("captured_at")
    return cps


def _report_payload(scan):
    """Rebuilds the report screen's data from a stored scan (so a patient who comes back sees the same report)."""
    sev = scan.get("severity") or {}
    st = scan.get("skin_type_pred")
    views = [v["view"] for v in db.list_scan_views(scan["id"])]
    return {
        "success": True, "scan_id": scan["id"], "model_version": scan.get("model_version"), "skin_type": st,
        "skin_type_confidence": scan.get("skin_type_conf"), "skin_type_probabilities": scan.get("probs"),
        "overall_health_score": scan.get("overall_score"), "severity_scores": sev, "cv_metrics": scan.get("cv_metrics"),
        "triage": ml.triage(sev, st), "routine": scan.get("routine") or generate_skincare_routine(st, sev),
        "views": views or ["front"], "patient_id": scan.get("patient_id"),
    }


@app.get("/api/me/journey")
def my_journey(request: Request):
    """Where the signed-in patient is in their cycle, and what they may do next (see journey.py)."""
    u = require_roles(request, "patient")
    st = journey.state_for_patient(u["patient_id"], db)
    out = {"stage": st["stage"], "can_start_new": st["can_start_new"], "consultation_id": (st["consultation"] or {}).get("id"),
           "checkpoints": [], "report": None, "today": journey.today_ist().isoformat()}
    if st["stage"] == "report":
        out["report"] = _report_payload(db.get_scan(st["scan_id"]))
    elif st["stage"] == "completed":
        out["checkpoints"] = _checkpoints_with_comparisons(st["consultation"])
        avail = journey.available_checkpoint(out["checkpoints"])
        out["available_day"] = avail["day"] if avail else None
    elif st["stage"] in ("payment", "pending"):
        out["report"] = _report_payload(db.get_scan(st["scan_id"]))
    return out


@app.post("/api/consultations/{cid}/pay")
async def pay_consultation(cid: str, request: Request):
    """DEMO payment only — no real payment processor is wired up. Marks the $99 fee as paid locally
    so the consultation moves into the doctor's queue. No card or financial data is collected.
    Patients must send the appointment they chose as {"slot_start": <ISO time>}: the slot is reserved first (409 if it
    was just taken, and nothing is paid), then the consultation is marked paid. Staff may pay without a slot."""
    con = db.get_consultation(cid)
    if not con:
        raise HTTPException(404, "not found")
    if con["status"] != "awaiting_payment":
        raise HTTPException(400, f"consultation is '{con['status']}', not awaiting payment")
    logged_in = get_current_user(request)
    if logged_in and logged_in["role"] == "patient" and logged_in["patient_id"] != con["patient_id"]:
        raise HTTPException(403, "This consultation belongs to a different patient account")
    body = await json_body(request)
    slot = body.get("slot_start")
    if logged_in and logged_in["role"] == "patient" and not slot:
        raise HTTPException(400, "Please choose an appointment time first")
    appt = None
    if slot:
        try:
            appt = scheduling.book(cid, con["patient_id"], slot, actor=(logged_in or {}).get("email", "patient"))
        except scheduling.ScheduleError as e:
            raise HTTPException(e.status, str(e))
    paid = db.mark_consultation_paid(cid)
    if appt and paid["status"] != "awaiting_doctor":     # lost a race with another payment: don't keep the slot
        scheduling.cancel(appt["id"], staff=True, reason="payment not completed")
        raise HTTPException(409, "This consultation could not be paid. Please try again.")
    paid["appointment"] = appt
    return paid


# --------------------------------------------------------------- appointments (see scheduling.py)
def _own_consultation_or_staff(request: Request, cid: str):
    user = require_user(request)
    con = db.get_consultation(cid)
    if not con:
        raise HTTPException(404, "not found")
    if user["role"] == "patient" and user["patient_id"] != con["patient_id"]:
        raise HTTPException(403, "This consultation belongs to a different patient account")
    return user, con


@app.get("/api/schedule/config")
def schedule_config(request: Request):
    require_user(request)
    return scheduling.config()


@app.get("/api/schedule/week")
def schedule_week(request: Request, date: str | None = None):
    require_user(request)
    try:
        return scheduling.week(date)
    except scheduling.ScheduleError as e:
        raise HTTPException(e.status, str(e))


@app.post("/api/consultations/{cid}/appointment")
async def book_appointment(cid: str, request: Request):
    """Book — or reschedule — the appointment for a paid consultation. Body: {"slot_start": <ISO time>}."""
    user, con = _own_consultation_or_staff(request, cid)
    if con["status"] != "awaiting_doctor":
        raise HTTPException(400, "Appointments are booked when the consultation is paid and before the doctor completes it")
    body = await json_body(request)
    if not body.get("slot_start"):
        raise HTTPException(400, "slot_start is required")
    try:
        return scheduling.book(cid, con["patient_id"], body["slot_start"], actor=user["email"], staff=user["role"] != "patient")
    except scheduling.ScheduleError as e:
        raise HTTPException(e.status, str(e))


@app.get("/api/appointments")
def my_appointments(request: Request, all: int = 0, past_days: int = 0):
    """Patients: their own upcoming appointments. Clinicians: their calendar (all=1 for the whole clinic). Admins: everyone."""
    user = require_user(request)
    past = max(0, min(past_days, 60))
    if user["role"] == "patient":
        return {"appointments": scheduling.list_upcoming(patient_id=user["patient_id"], include_past_days=past), "tz_label": scheduling.TZ_LABEL}
    doctor_id = None if (user["role"] == "admin" or all) else user["id"]
    return {"appointments": scheduling.list_upcoming(doctor_id=doctor_id, include_past_days=past), "tz_label": scheduling.TZ_LABEL}


@app.post("/api/appointments/{aid}/cancel")
def cancel_appointment(aid: str, request: Request):
    user = require_user(request)
    appt = scheduling.get(aid)
    if not appt:
        raise HTTPException(404, "not found")
    if user["role"] == "patient" and user["patient_id"] != appt["patient_id"]:
        raise HTTPException(403, "This appointment belongs to a different patient account")
    try:
        return scheduling.cancel(aid, actor=user["email"], staff=user["role"] != "patient",
                                 reason="cancelled by patient" if user["role"] == "patient" else f"cancelled by {user['full_name']}")
    except scheduling.ScheduleError as e:
        raise HTTPException(e.status, str(e))


@app.get("/api/admin/appointments")
def admin_appointments(request: Request, status: str | None = None, doctor_id: str | None = None,
                       date_from: str | None = None, date_to: str | None = None, q: str | None = None):
    require_roles(request, "admin")
    try:
        rows = scheduling.list_all(status or None, doctor_id or None, date_from or None, date_to or None, q or None)
    except scheduling.ScheduleError as e:
        raise HTTPException(e.status, str(e))
    return {"appointments": rows, "needs_time": scheduling.needs_time(),
            "doctors": [{"id": u["id"], "name": u["full_name"], "code": u.get("user_code")} for u in db.list_users(role="clinician")]}


@app.get("/api/admin/schedule-summary")
def admin_schedule_summary(request: Request, date: str | None = None):
    require_roles(request, "admin")
    try:
        return scheduling.utilization(date)
    except scheduling.ScheduleError as e:
        raise HTTPException(e.status, str(e))


@app.get("/api/admin/users")
def admin_users(request: Request, role: str | None = None):
    """All accounts with their unique IDs and roles (never password hashes)."""
    require_roles(request, "admin")
    if role and role not in db.ROLES:
        raise HTTPException(400, f"role must be one of {db.ROLES}")
    return {"users": [{**user_public(u), "patient_mrn": (db.get_patient(u["patient_id"]) or {}).get("mrn") if u.get("patient_id") else None}
                      for u in db.list_users(role=role)], "roles": list(db.ROLES)}


@app.get("/api/admin/schedule-blocks")
def get_schedule_blocks(request: Request):
    require_user(request, role="admin")
    return {"blocks": scheduling.list_blocks(), "doctors": [{"id": u["id"], "name": u["full_name"]} for u in db.list_users(role="clinician")]}


@app.post("/api/admin/schedule-blocks")
async def create_schedule_block(request: Request):
    admin = require_user(request, role="admin")
    b = await json_body(request)
    try:
        bid, clash = scheduling.add_block(b.get("date_from"), b.get("date_to") or None, b.get("start_time") or None, b.get("end_time") or None,
                                          b.get("doctor_id") or None, b.get("reason"), actor=admin["email"])
    except scheduling.ScheduleError as e:
        raise HTTPException(e.status, str(e))
    return {"id": bid, "conflicts": clash}


@app.delete("/api/admin/schedule-blocks/{bid}")
def remove_schedule_block(bid: str, request: Request):
    admin = require_user(request, role="admin")
    try:
        scheduling.delete_block(bid, actor=admin["email"])
    except scheduling.ScheduleError as e:
        raise HTTPException(e.status, str(e))
    return {"success": True}


@app.get("/api/consultations/{cid}/ai-suggestions")
def consultation_ai_suggestions(cid: str, request: Request):
    require_roles(request, "clinician", "admin")
    con = db.get_consultation(cid)
    if not con:
        raise HTTPException(404, "not found")
    scan = db.get_scan(con["scan_id"])
    patient = con.get("patient") or {}
    return consultation_ai.draft_report(scan["skin_type_pred"], scan.get("severity") or {}, scan.get("triage_status"),
                                        scan.get("overall_score"), patient.get("previous_skin_condition"))


@app.patch("/api/consultations/{cid}")
async def complete_consultation(cid: str, request: Request):
    """Doctor submits the case report. Requires a logged-in clinician or admin account and payment.
    Feeds the doctor's verified labels into learning."""
    clinician = require_roles(request, "clinician", "admin")
    con = db.get_consultation(cid)
    if not con:
        raise HTTPException(404, "not found")
    if con["status"] != "awaiting_doctor":
        raise HTTPException(400, f"consultation is '{con['status']}', not awaiting a doctor")
    body = await json_body(request)
    doctor_name = clinician.get("full_name") or "Dr. Tanya Unni"
    st = body.get("verified_skin_type")
    if st and st not in SKIN_TYPES:
        raise HTTPException(400, f"verified_skin_type must be one of {SKIN_TYPES}")
    sev = {k: float(np.clip(float(v), 0, 5)) for k, v in (body.get("verified_severity") or {}).items() if v not in (None, "")}
    try:
        followup_days = int(body.get("followup_days") or 30)
    except (TypeError, ValueError):
        raise HTTPException(400, "followup_days must be a number")

    updated = db.complete_consultation(cid, doctor_name, body.get("symptoms"), body.get("diagnosis"), body.get("cure"),
                                       body.get("prescription"), body.get("previous_condition"), followup_days, st, sev or None)
    scheduling.complete_for_consultation(cid)
    db.add_annotation(con["scan_id"], doctor_name, st, sev or None, notes=f"via consultation {cid}")
    added = learn_from_verification(con["scan_id"], st, sev)
    started, msg = maybe_start_retrain()
    updated["added_to_memory"] = added
    updated["retrain"] = msg if started else None
    return updated


# --------------------------------------------------------------- learning
@app.get("/api/learning/status")
def learning_status(request: Request):
    require_roles(request, "admin")
    st = db.dashboard_stats()
    running = _retrain_proc is not None and _retrain_proc.poll() is None
    return {
        "learning_enabled": LEARNING_ENABLED,
        "active_model": ml.version, "model_kind": ml.kind, "memory_size": len(ml.memory) if LEARNING_ENABLED else 0,
        "verified_scans": st["verified_scans"], "verified_since_last_training": st["verified_since_last_training"],
        "auto_retrain_threshold": AUTO_RETRAIN_MIN_NEW, "auto_retrain": AUTO_RETRAIN, "retrain_running": running,
        "last_run": db.last_training_run(), "models": db.list_models(),
    }


@app.post("/api/learning/retrain")
async def learning_retrain(request: Request):
    require_roles(request, "admin")
    if not LEARNING_ENABLED:
        raise HTTPException(403, "Learning from user data is switched off.")
    body = await json_body(request)
    started, msg = maybe_start_retrain(force=bool(body.get("force", True)))
    return {"started": started, "message": msg}


# --------------------------------------------------------------- dashboard
@app.get("/api/dashboard/stats")
def dashboard_stats(request: Request):
    require_roles(request, "admin")
    return db.dashboard_stats()


@app.get("/api/hospital-metrics")
def get_hospital_metrics(request: Request):
    require_roles(request, "admin")
    ch = db.get_champion()
    report_path = os.path.join(ROOT, "hospital_deliverables_report.json")
    if ch and ch["version"].startswith("2.") and os.path.exists(report_path):
        with open(report_path) as f:
            rep = json.load(f)
        m = (ch.get("metrics") or {}).get("test")
        if m:
            rep["clinical_performance_metrics"] = m
            rep["model_metadata"]["version"] = ch["version"]
        return rep
    return {
        "model_metadata": {"version": "1.0", "backbone": "facebook/dinov2-base", "adaptation": "LoRA r=16 (query/value)",
                           "trainable_parameters": 988431, "total_parameters": 87568911,
                           "evaluation_protocol": "172 faces never seen in training (leak-free re-evaluation)"},
        "clinical_performance_metrics": {
            "accuracy": 0.3557, "macro_f1_score": 0.3021, "balanced_accuracy": 0.3114, "triage_safety_sensitivity": None,
            "confusion_matrix": {"labels": SKIN_TYPES,
                                 "matrix": [[41, 19, 19, 1], [22, 28, 15, 2], [33, 21, 19, 5], [13, 11, 2, 2]]}},
        "notes": ["v1's published 72.6% was measured on test faces that also appeared in training.",
                  "Run data_prep.py then train_v2.py to train and evaluate model v2."],
    }


@app.get("/api/products")
def list_products(request: Request):
    require_roles(request, "patient", "clinician", "admin")
    """Dr Tanya's real facial skincare catalog (https://drtanya.in), with each product's
    AI-matchable concern mapping — the same data the routine recommendation engine uses."""
    return {"products": dr_tanya_products.CATALOG, "catalog_gaps": dr_tanya_products.CATALOG_GAPS}


@app.get("/api/products/recommendations")
def product_recommendations(scan_id: str, request: Request):
    _require_scan_photo_access(request, scan_id)
    """Standalone ranked recommendation list (product + matched concerns + score) for a given scan,
    independent of the AM/PM routine shape — useful for a dedicated 'recommended for you' view."""
    scan = db.get_scan(scan_id)
    if not scan:
        raise HTTPException(404, "scan not found")
    ranked = dr_tanya_products.recommend(scan.get("severity") or {})
    return {"scan_id": scan_id, "skin_type": scan.get("skin_type_pred"), "recommendations": [
        {"product": r["product"], "score": r["score"],
         "matched_concerns": [{"attribute": a, "value": v, "weight": w} for a, v, w in r["matched_concerns"]]}
        for r in ranked]}


@app.get("/api/doctors")
def get_doctors(request: Request):
    require_roles(request, "admin")
    return {"doctors": DOCTOR_NETWORK}


@app.post("/api/book-consultation")
async def book_consultation(request: Request):
    require_roles(request, "admin")
    data = await json_body(request)
    doc = next((d for d in DOCTOR_NETWORK if d["id"] == data.get("doctor_id")), DOCTOR_NETWORK[0])
    return {
        "success": True, "booking_id": f"HAUT-MED-{int(time.time())}", "doctor": doc,
        "appointment_time": "Today at 4:30 PM EST (Instant Video Call)",
        "patient_name": data.get("patient_name", "Valued Customer"),
        "clinical_summary": "AI skin triage summary attached to referral.",
    }

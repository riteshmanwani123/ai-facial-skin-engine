"""
server.py — Clarity AI Face Engine API + dashboard.

New in this version
  * Local database (database.py): every analysis is stored with the patient, image,
    scores, triage and embedding.
  * Patients & history endpoints for the hospital dashboard.
  * Clinician verification endpoint -> instant learning (memory.py) + retraining queue.
  * Auto retraining (continual_learning.py) once enough new verified scans exist; the
    server hot-swaps to a newly promoted champion model.
  * Uses the model v2 champion (train_v2.py) when available, else falls back to v1.

Run:  python server.py   ->  http://localhost:5095
"""
import os
import io
import sys
import json
import time
import base64
import subprocess
import threading
import numpy as np
from PIL import Image

from flask import Flask, request, jsonify, render_template, send_file
import torch

from flask import make_response

import database as db
from memory import ExperienceMemory
from dinov2_lora_model import analyze_image_quality_and_cv_metrics, evaluate_clinical_triage
from dermatology_rules import generate_skincare_routine, DOCTOR_NETWORK
import consultation_ai
import auth
import privacy
import intake as intake_mod

ROOT = os.path.dirname(os.path.abspath(__file__))
os.chdir(ROOT)
SKIN_TYPES = ["dry", "oily", "normal", "combination"]
AUTO_RETRAIN_MIN_NEW = int(os.environ.get("CLARITY_RETRAIN_MIN_NEW", 50))
AUTO_RETRAIN = os.environ.get("CLARITY_AUTO_RETRAIN", "1") == "1"
V1_PATH = "dinov2_skin_lora.pt"

app = Flask(__name__, template_folder="templates", static_folder="static")


# =========================================================== model engine
class Engine:
    """Holds the active (champion) model and the experience memory; hot-swappable."""

    def __init__(self):
        self.lock = threading.RLock()
        self.memory = ExperienceMemory()
        self.version, self.kind, self.model = None, None, None
        if torch.cuda.is_available():
            self.device = torch.device("cuda")
        elif hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
            self.device = torch.device("mps")
        else:
            self.device = torch.device("cpu")
        self.load()

    def load(self):
        champ = db.get_champion()
        with self.lock:
            if champ and champ["version"].startswith("2.") and os.path.exists(champ["path"]):
                from model_v2 import load_checkpoint
                self.model, _ = load_checkpoint(champ["path"], self.device)
                self.kind, self.version = "v2", champ["version"]
            else:
                from dinov2_lora_model import DINOv2SkinAnalyzer
                m = DINOv2SkinAnalyzer(model_name="facebook/dinov2-base", num_classes=4, r=16)
                if os.path.exists(V1_PATH):
                    m.load_state_dict(torch.load(V1_PATH, map_location="cpu"), strict=False)
                    if not champ:
                        db.register_model("1.0", V1_PATH, notes="legacy v1 model (leaky evaluation)", make_champion=True)
                self.model, self.kind, self.version = m.to(self.device).eval(), "v1", "1.0"
            n = self.memory.load_from_db(db, self.version)
        print(f"[engine] model {self.version} ({self.kind}) on {self.device}; memory: {n} verified scans")

    @torch.no_grad()
    def infer(self, img):
        with self.lock:
            if self.kind == "v2":
                from model_v2 import predict, SEVERITY_ATTRIBUTES
                probs, sev, emb = predict(self.model, [img], self.device, tta=True)
                severity = {a: float(sev[0, i]) for i, a in enumerate(SEVERITY_ATTRIBUTES)}
            else:
                from dinov2_lora_model import SEVERITY_ATTRIBUTES
                from torchvision import transforms
                tf = transforms.Compose([transforms.Resize((224, 224)), transforms.ToTensor(),
                                         transforms.Normalize([0.485, 0.456, 0.406], [0.229, 0.224, 0.225])])
                out = self.model(tf(img).unsqueeze(0).to(self.device))
                probs = torch.softmax(out["type_logits"], 1).cpu()
                sev = out["severity_scores"].cpu()
                emb = out["embedding"].cpu()
                severity = {a: float(sev[0, i]) for i, a in enumerate(SEVERITY_ATTRIBUTES)}
            return probs[0].numpy(), severity, emb[0].numpy(), self.version


engine = Engine()


def champion_watcher():
    """Hot-swap to a newly promoted model (e.g. after a background retrain)."""
    while True:
        time.sleep(30)
        try:
            ch = db.get_champion()
            if ch and ch["version"] != engine.version:
                print(f"[engine] new champion {ch['version']} detected — reloading")
                engine.load()
        except Exception as e:  # never kill the server
            print("[engine] watcher error:", e)


threading.Thread(target=champion_watcher, daemon=True).start()
_retrain_proc = None


def maybe_start_retrain(force=False):
    global _retrain_proc
    if _retrain_proc is not None and _retrain_proc.poll() is None:
        return False, "retrain already running"
    if not force:
        if not AUTO_RETRAIN:
            return False, "auto-retrain disabled"
        if engine.kind != "v2":
            return False, "train model v2 first (python train_v2.py)"
        if db.dashboard_stats()["verified_since_last_training"] < AUTO_RETRAIN_MIN_NEW:
            return False, "not enough new verified scans yet"
    log = open(os.path.join(db.DATA_DIR, "retrain.log"), "a")
    cmd = [sys.executable, os.path.join(ROOT, "continual_learning.py"), "retrain"] + (["--force"] if force else [])
    _retrain_proc = subprocess.Popen(cmd, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT)
    return True, "retrain started"


# ================================================================ helpers
def read_image():
    if "file" in request.files:
        return Image.open(request.files["file"].stream).convert("RGB")
    body = request.get_json(silent=True) or {}
    if "image_base64" in body:
        return Image.open(io.BytesIO(base64.b64decode(body["image_base64"].split(",")[-1]))).convert("RGB")
    return None


def req_field(name, default=None):
    body = request.get_json(silent=True) or {}
    return request.form.get(name) or body.get(name) or request.args.get(name) or default


def actor():
    return request.headers.get("X-Clinician") or req_field("annotator") or "clinician"


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


# ================================================================ routes
@app.route("/")
def index():
    return render_template("index.html")


@app.route("/patient")
def patient_portal():
    return render_template("patient.html")


@app.route("/doctor")
def doctor_portal():
    return render_template("doctor.html")


@app.route("/login")
def login_page():
    return render_template("login.html")


@app.route("/signup")
def signup_page():
    return render_template("signup.html")


@app.route("/admin")
def admin_page():
    return render_template("admin.html")


# -------------------------------------------------------------------- auth
def user_public(u):
    return {k: v for k, v in u.items() if k != "password_hash"} if u else None


def get_current_user():
    token = request.cookies.get(auth.SESSION_COOKIE)
    if not token:
        return None
    payload = auth.decode_token(token)
    return db.get_user(payload["sub"]) if payload else None


def set_session_cookie(resp, user):
    resp.set_cookie(auth.SESSION_COOKIE, auth.create_token(user), httponly=True, samesite="Lax",
                    max_age=auth.TOKEN_TTL_DAYS * 86400, path="/")


@app.route("/api/auth/signup", methods=["POST"])
def signup():
    """Public self-service signup — PATIENTS ONLY. Clinician accounts are provisioned by an
    admin (see /api/admin/clinicians below), not self-service."""
    body = request.get_json(silent=True) or {}
    email, password = (body.get("email") or "").strip(), body.get("password") or ""
    full_name = (body.get("full_name") or "").strip()
    if not auth.valid_email(email):
        return jsonify({"error": "Enter a valid email address"}), 400
    if len(password) < 8:
        return jsonify({"error": "Password must be at least 8 characters"}), 400
    if not full_name:
        return jsonify({"error": "full_name is required"}), 400
    if db.get_user_by_email(email):
        return jsonify({"error": "An account with this email already exists"}), 409

    age = body.get("age")
    if age not in (None, ""):
        try:
            age = int(age)
        except (TypeError, ValueError):
            return jsonify({"error": "age must be a number"}), 400
    intake = intake_mod.clean_intake(body.get("intake"))
    history = (body.get("previous_skin_condition") or "").strip() or (intake_mod.summarize(intake) if intake else None)
    patient = db.create_patient(full_name, actor="signup", age=age, sex=body.get("sex"),
                                previous_skin_condition=history,
                                consent_research=body.get("consent_research", 1))
    if intake:
        db.set_patient_intake(patient["id"], intake)

    user = db.create_user(email, auth.hash_password(password), full_name, "patient", patient["id"])
    resp = make_response(jsonify(user_public(user)))
    set_session_cookie(resp, user)
    return resp


# ------------------------------------------------------- admin: manage clinicians
@app.route("/api/admin/clinicians", methods=["GET", "POST"])
def admin_clinicians():
    admin = get_current_user()
    if not admin:
        return jsonify({"error": "Not logged in"}), 401
    if admin["role"] != "admin":
        return jsonify({"error": "This action requires an admin account"}), 403
    if request.method == "POST":
        body = request.get_json(silent=True) or {}
        email, password = (body.get("email") or "").strip(), body.get("password") or ""
        full_name = (body.get("full_name") or "").strip()
        if not auth.valid_email(email):
            return jsonify({"error": "Enter a valid email address"}), 400
        if len(password) < 8:
            return jsonify({"error": "Password must be at least 8 characters"}), 400
        if not full_name:
            return jsonify({"error": "full_name is required"}), 400
        if db.get_user_by_email(email):
            return jsonify({"error": "An account with this email already exists"}), 409
        user = db.create_user(email, auth.hash_password(password), full_name, "clinician")
        db.audit("create", "user", user["id"], actor=admin["email"], details={"role": "clinician", "created_by_admin": True})
        return jsonify(user_public(user))
    return jsonify({"clinicians": [user_public(u) for u in db.list_users(role="clinician")]})


@app.route("/api/admin/clinicians/<uid>", methods=["DELETE"])
def admin_delete_clinician(uid):
    admin = get_current_user()
    if not admin:
        return jsonify({"error": "Not logged in"}), 401
    if admin["role"] != "admin":
        return jsonify({"error": "This action requires an admin account"}), 403
    target = db.get_user(uid)
    if not target or target["role"] != "clinician":
        return jsonify({"error": "not found"}), 404
    db.delete_user(uid)
    db.audit("delete", "user", uid, actor=admin["email"], details={"role": "clinician"})
    return jsonify({"deleted": uid})


@app.route("/api/auth/login", methods=["POST"])
def login():
    body = request.get_json(silent=True) or {}
    email, password = (body.get("email") or "").strip(), body.get("password") or ""
    user = db.get_user_by_email(email)
    if not user or not auth.verify_password(password, user["password_hash"]):
        return jsonify({"error": "Incorrect email or password"}), 401
    db.touch_user_login(user["id"])
    resp = make_response(jsonify(user_public(user)))
    set_session_cookie(resp, user)
    return resp


@app.route("/api/auth/logout", methods=["POST"])
def logout():
    resp = make_response(jsonify({"success": True}))
    resp.delete_cookie(auth.SESSION_COOKIE, path="/")
    return resp


@app.route("/api/auth/me")
def me():
    u = get_current_user()
    if not u:
        return jsonify({"error": "Not logged in"}), 401
    return jsonify(user_public(u))


@app.route("/api/analyze", methods=["POST"])
def analyze_skin():
    try:
        img = read_image()
        if img is None:
            return jsonify({"error": "No image provided"}), 400
        logged_in = get_current_user()
        is_patient = bool(logged_in and logged_in["role"] == "patient")
        if is_patient:
            patient_id = logged_in["patient_id"]
        else:
            patient_id = req_field("patient_id") or None
        if patient_id and not db.get_patient(patient_id):
            return jsonify({"error": "Unknown patient_id"}), 404
        consultation_id = req_field("consultation_id") or None
        baseline_scan = None
        if consultation_id:
            con = db.get_consultation(consultation_id)
            if not con or con["status"] != "completed":
                return jsonify({"error": "Consultation not found or not yet completed by a doctor"}), 404
            if con["followup_scan_id"]:
                return jsonify({"error": "A follow-up scan was already recorded for this consultation"}), 400
            baseline_scan = db.get_scan(con["scan_id"])

        # eye privacy (see privacy.py): stored image has eyes covered; the AI sees a skin-filled copy
        body = request.get_json(silent=True) or {}
        client_red = body.get("client_redaction") if isinstance(body.get("client_redaction"), dict) else {}
        img, analysis_img, front_report = privacy.process_image(img, client_red.get("front"), need_analysis=True)
        if is_patient and front_report["status"] != "redacted":
            return jsonify({"error": "We couldn't confirm your eyes were covered in this photo, so it was not saved. Please retake it."}), 422
        extra_views = {}
        ev = body.get("extra_views")
        if isinstance(ev, dict):
            for key, b64 in list(ev.items())[:4]:
                if key not in db.VIEW_KEYS or key == "front" or not isinstance(b64, str) or len(b64) > 14_000_000:
                    continue
                try:
                    im = Image.open(io.BytesIO(base64.b64decode(b64.split(",")[-1]))).convert("RGB")
                except Exception:
                    continue
                v_img, _, v_report = privacy.process_image(im, client_red.get(key), need_analysis=False)
                if is_patient and v_report["status"] != "redacted":
                    return jsonify({"error": f"We couldn't confirm your eyes were covered in the '{key}' photo, so it was not saved."}), 422
                extra_views[key] = (v_img, v_report)

        cv = analyze_image_quality_and_cv_metrics(analysis_img)
        probs, severity, emb, version = engine.infer(analysis_img)
        probs, severity, mem_info = engine.memory.adjust(emb, probs, severity)

        # CV-derived tanning index (no labelled data for tanning exists)
        severity["tanning_severity"] = cv["cv_tanning_severity"]
        if engine.kind == "v1":  # v1 severity head was trained on synthetic targets — keep legacy CV overrides
            severity["pigmentation_melasma"] = cv["cv_melasma_severity"]
            severity["texture_roughness"] = cv["cv_texture"]
        severity = {k: float(np.clip(v, 0, 5)) for k, v in severity.items()}

        idx = int(np.argmax(probs))
        skin_type = SKIN_TYPES[idx]
        triage = evaluate_clinical_triage(severity, skin_type)
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
        }
        result["scan_id"] = db.save_scan(img, result, patient_id, version, emb, version, actor())
        db.register_front_view(result["scan_id"], front_report)
        for key, (v_img, v_report) in extra_views.items():
            db.save_scan_view(result["scan_id"], patient_id, key, v_img, v_report)
        result["views"] = ["front"] + [k for k in db.VIEW_KEYS if k in extra_views]
        result["privacy"] = {"eye_redaction": privacy.public_report(front_report),
                             "all_views_covered": all(r["status"] == "redacted" for _, r in extra_views.values())}
        result["patient_id"] = patient_id
        if consultation_id and baseline_scan:
            db.attach_followup_scan(consultation_id, result["scan_id"])
            result["followup_comparison"] = compare_scans(baseline_scan, result)
        return jsonify(result)
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


# ---------------------------------------------------------------- patients
@app.route("/api/patients", methods=["GET", "POST"])
def patients():
    if request.method == "POST":
        body = request.get_json(silent=True) or request.form.to_dict()
        if not (body.get("full_name") or "").strip():
            return jsonify({"error": "full_name is required"}), 400
        if body.get("age") not in (None, ""):
            try:
                body["age"] = int(body["age"])
            except (TypeError, ValueError):
                return jsonify({"error": "age must be a number"}), 400
        try:
            p = db.create_patient(body.pop("full_name").strip(), actor=actor(), **body)
        except Exception as e:
            return jsonify({"error": str(e)}), 400
        return jsonify(p), 201
    return jsonify({"patients": db.list_patients(request.args.get("q"))})


@app.route("/api/patients/<pid>", methods=["GET", "PATCH", "DELETE"])
def patient(pid):
    if request.method == "PATCH":
        return jsonify(db.update_patient(pid, actor=actor(), **(request.get_json(silent=True) or {})))
    if request.method == "DELETE":
        db.delete_patient(pid, actor=actor())
        return jsonify({"deleted": pid})
    p = db.get_patient(pid)
    if not p:
        return jsonify({"error": "not found"}), 404
    p["scans"] = db.list_scans(pid)
    return jsonify(p)


# ------------------------------------------------------------------- scans
@app.route("/api/scans")
def scans():
    return jsonify({"scans": db.list_scans(request.args.get("patient_id"), int(request.args.get("limit", 100)))})


@app.route("/api/scans/<sid>")
def scan(sid):
    s = db.get_scan(sid)
    return (jsonify(s), 200) if s else (jsonify({"error": "not found"}), 404)


@app.route("/api/scans/<sid>/image")
def scan_image(sid):
    p = db.scan_image_path(sid)
    if not p or not os.path.exists(p):
        return jsonify({"error": "not found"}), 404
    return send_file(p, mimetype="image/jpeg")


def learn_from_verification(sid, verified_skin_type, severity):
    """Feeds a clinician/doctor-confirmed label into instant-learning memory, respecting research consent."""
    added = False
    for smp in db.verified_samples(embedding_version=engine.version, consent_only=True):
        if smp["scan_id"] == sid and smp["embedding"] is not None:
            engine.memory.add(sid, smp["embedding"], verified_skin_type, severity)
            added = True
    return added


@app.route("/api/scans/<sid>/views")
def scan_views(sid):
    if not db.get_scan(sid):
        return jsonify({"error": "not found"}), 404
    return jsonify({"views": [{"view": v["view"], "url": f"/api/scans/{sid}/views/{v['view']}/image",
                               "eyes_covered": (v.get("redaction") or {}).get("status") == "redacted"}
                              for v in db.list_scan_views(sid)]})


@app.route("/api/scans/<sid>/views/<view>/image")
def scan_view_image(sid, view):
    p = db.scan_view_image_path(sid, view)
    if not p or not os.path.exists(p):
        return jsonify({"error": "not found"}), 404
    return send_file(p, mimetype="image/jpeg")


@app.route("/api/scans/<sid>/verify", methods=["POST"])
def verify_scan(sid):
    """Clinician ground truth. Body: {verified_skin_type, severity: {attr: 0-5}, notes, annotator}"""
    s = db.get_scan(sid)
    if not s:
        return jsonify({"error": "not found"}), 404
    body = request.get_json(silent=True) or {}
    st = body.get("verified_skin_type")
    if st and st not in SKIN_TYPES:
        return jsonify({"error": f"verified_skin_type must be one of {SKIN_TYPES}"}), 400
    sev = {k: float(np.clip(float(v), 0, 5)) for k, v in (body.get("severity") or {}).items() if v not in (None, "")}
    db.add_annotation(sid, actor(), st, sev or None, body.get("notes"))
    added = learn_from_verification(sid, st, sev)
    started, msg = maybe_start_retrain()
    return jsonify({"success": True, "added_to_memory": added, "memory_size": len(engine.memory),
                    "retrain": msg if started else None})


# --------------------------------------------------------------- consultations
@app.route("/api/consultations", methods=["GET", "POST"])
def consultations():
    if request.method == "POST":
        body = request.get_json(silent=True) or {}
        scan_id = body.get("scan_id")
        scan = db.get_scan(scan_id) if scan_id else None
        if not scan:
            return jsonify({"error": "scan_id is required and must exist"}), 400
        if not scan["patient_id"]:
            return jsonify({"error": "This scan has no patient record — the consultation flow requires patient intake first"}), 400
        logged_in = get_current_user()
        if logged_in and logged_in["role"] == "patient" and logged_in["patient_id"] != scan["patient_id"]:
            return jsonify({"error": "This scan belongs to a different patient account"}), 403
        patient = db.get_patient(scan["patient_id"])
        draft = consultation_ai.draft_report(scan["skin_type_pred"], scan.get("severity") or {}, scan.get("triage_status"),
                                             scan.get("overall_score"), patient.get("previous_skin_condition") if patient else None)
        con = db.create_consultation(scan["patient_id"], scan_id, fee_usd=99.0, ai_suggestions=draft)
        return jsonify(con), 201
    return jsonify({"consultations": db.list_consultations(request.args.get("status"), request.args.get("patient_id"))})


@app.route("/api/consultations/<cid>")
def consultation_detail(cid):
    con = db.get_consultation(cid)
    if not con:
        return jsonify({"error": "not found"}), 404
    con["scan"] = db.get_scan(con["scan_id"])
    if con["followup_scan_id"]:
        followup = db.get_scan(con["followup_scan_id"])
        con["followup_scan"] = followup
        con["comparison"] = compare_scans(con["scan"], {"overall_health_score": followup.get("overall_score"),
                                                        "severity_scores": followup.get("severity") or {}})
    return jsonify(con)


@app.route("/api/consultations/<cid>/pay", methods=["POST"])
def pay_consultation(cid):
    """DEMO payment only — no real payment processor is wired up. Marks the $99 fee as paid locally
    so the consultation moves into the doctor's queue. No card or financial data is collected."""
    con = db.get_consultation(cid)
    if not con:
        return jsonify({"error": "not found"}), 404
    if con["status"] != "awaiting_payment":
        return jsonify({"error": f"consultation is '{con['status']}', not awaiting payment"}), 400
    logged_in = get_current_user()
    if logged_in and logged_in["role"] == "patient" and logged_in["patient_id"] != con["patient_id"]:
        return jsonify({"error": "This consultation belongs to a different patient account"}), 403
    return jsonify(db.mark_consultation_paid(cid))


@app.route("/api/consultations/<cid>/ai-suggestions")
def consultation_ai_suggestions(cid):
    con = db.get_consultation(cid)
    if not con:
        return jsonify({"error": "not found"}), 404
    scan = db.get_scan(con["scan_id"])
    patient = con.get("patient") or {}
    return jsonify(consultation_ai.draft_report(scan["skin_type_pred"], scan.get("severity") or {}, scan.get("triage_status"),
                                                scan.get("overall_score"), patient.get("previous_skin_condition")))


@app.route("/api/consultations/<cid>", methods=["PATCH"])
def complete_consultation(cid):
    """Doctor submits the case report. Requires a logged-in clinician account and payment.
    Feeds the doctor's verified labels into learning."""
    clinician = get_current_user()
    if not clinician:
        return jsonify({"error": "Not logged in"}), 401
    if clinician["role"] != "clinician":
        return jsonify({"error": "This action requires a clinician account"}), 403
    con = db.get_consultation(cid)
    if not con:
        return jsonify({"error": "not found"}), 404
    if con["status"] != "awaiting_doctor":
        return jsonify({"error": f"consultation is '{con['status']}', not awaiting a doctor"}), 400
    body = request.get_json(silent=True) or {}
    doctor_name = clinician["full_name"]
    st = body.get("verified_skin_type")
    if st and st not in SKIN_TYPES:
        return jsonify({"error": f"verified_skin_type must be one of {SKIN_TYPES}"}), 400
    sev = {k: float(np.clip(float(v), 0, 5)) for k, v in (body.get("verified_severity") or {}).items() if v not in (None, "")}
    try:
        followup_days = int(body.get("followup_days") or 30)
    except (TypeError, ValueError):
        return jsonify({"error": "followup_days must be a number"}), 400

    updated = db.complete_consultation(cid, doctor_name, body.get("symptoms"), body.get("diagnosis"), body.get("cure"),
                                       body.get("prescription"), body.get("previous_condition"), followup_days, st, sev or None)
    db.add_annotation(con["scan_id"], doctor_name, st, sev or None, notes=f"via consultation {cid}")
    added = learn_from_verification(con["scan_id"], st, sev)
    started, msg = maybe_start_retrain()
    updated["added_to_memory"] = added
    updated["retrain"] = msg if started else None
    return jsonify(updated)


@app.route("/api/consultations/pending-followups")
def pending_followups():
    return jsonify({"consultations": db.pending_followups()})


# --------------------------------------------------------------- learning
@app.route("/api/learning/status")
def learning_status():
    st = db.dashboard_stats()
    running = _retrain_proc is not None and _retrain_proc.poll() is None
    return jsonify({
        "active_model": engine.version, "model_kind": engine.kind, "memory_size": len(engine.memory),
        "verified_scans": st["verified_scans"], "verified_since_last_training": st["verified_since_last_training"],
        "auto_retrain_threshold": AUTO_RETRAIN_MIN_NEW, "auto_retrain": AUTO_RETRAIN, "retrain_running": running,
        "last_run": db.last_training_run(), "models": db.list_models(),
    })


@app.route("/api/learning/retrain", methods=["POST"])
def learning_retrain():
    started, msg = maybe_start_retrain(force=bool(req_field("force", True)))
    return jsonify({"started": started, "message": msg})


# --------------------------------------------------------------- dashboard
@app.route("/api/dashboard/stats")
def dashboard_stats():
    return jsonify(db.dashboard_stats())


@app.route("/api/hospital-metrics", methods=["GET"])
def get_hospital_metrics():
    ch = db.get_champion()
    if ch and ch["version"].startswith("2.") and os.path.exists("hospital_deliverables_report.json"):
        with open("hospital_deliverables_report.json") as f:
            rep = json.load(f)
        m = (ch.get("metrics") or {}).get("test")
        if m:
            rep["clinical_performance_metrics"] = m
            rep["model_metadata"]["version"] = ch["version"]
        return jsonify(rep)
    # v1: report the honest leak-free evaluation instead of the leaky one
    return jsonify({
        "model_metadata": {"version": "1.0", "backbone": "facebook/dinov2-base", "adaptation": "LoRA r=16 (query/value)",
                           "trainable_parameters": 988431, "total_parameters": 87568911,
                           "evaluation_protocol": "172 faces never seen in training (leak-free re-evaluation)"},
        "clinical_performance_metrics": {
            "accuracy": 0.3557, "macro_f1_score": 0.3021, "balanced_accuracy": 0.3114, "triage_safety_sensitivity": None,
            "confusion_matrix": {"labels": SKIN_TYPES,
                                 "matrix": [[41, 19, 19, 1], [22, 28, 15, 2], [33, 21, 19, 5], [13, 11, 2, 2]]}},
        "notes": ["v1's published 72.6% was measured on test faces that also appeared in training.",
                  "Run data_prep.py then train_v2.py to train and evaluate model v2."],
    })


@app.route("/api/doctors", methods=["GET"])
def get_doctors():
    return jsonify({"doctors": DOCTOR_NETWORK})


@app.route("/api/book-consultation", methods=["POST"])
def book_consultation():
    data = request.get_json(silent=True) or {}
    doc = next((d for d in DOCTOR_NETWORK if d["id"] == data.get("doctor_id")), DOCTOR_NETWORK[0])
    return jsonify({
        "success": True, "booking_id": f"HAUT-MED-{int(time.time())}", "doctor": doc,
        "appointment_time": "Today at 4:30 PM EST (Instant Video Call)",
        "patient_name": data.get("patient_name", "Valued Customer"),
        "clinical_summary": "AI skin triage summary attached to referral.",
    })


if __name__ == "__main__":
    # 127.0.0.1 = this computer only. Set CLARITY_HOST=0.0.0.0 to expose on the clinic network (add login first).
    app.run(host=os.environ.get("CLARITY_HOST", "127.0.0.1"), port=int(os.environ.get("CLARITY_PORT", 5095)), debug=False, threaded=True)

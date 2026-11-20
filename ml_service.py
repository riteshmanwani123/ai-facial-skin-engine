"""
ml_service.py — ML Service layer for Clarity AI Face Engine.

Owns everything statistical/model-related: loading the current champion
checkpoint (v2 DINOv2+LoRA, or the legacy v1 model as fallback), running
inference, image-quality/CV metrics, clinical triage rules, and blending
predictions with the instant-learning memory (memory.py). Deliberately kept
as one importable class — not a separate network service — so a scan never
fails because "the ML service is down"; it lives in the same process as the
FastAPI app and is called directly. See README_V2.md for why this boundary
was drawn this way.

Everything here (the load/hot-swap logic, the v1/v2 branching in infer())
mirrors the Engine class previously inlined in server.py.
"""
import os
import threading

import torch

import database as db
from memory import ExperienceMemory
from dinov2_lora_model import (
    DINOv2SkinAnalyzer, SEVERITY_ATTRIBUTES as V1_SEVERITY_ATTRIBUTES,
    analyze_image_quality_and_cv_metrics, evaluate_clinical_triage,
)

SKIN_TYPES = ["dry", "oily", "normal", "combination"]
ROOT = os.path.dirname(os.path.abspath(__file__))
V1_PATH = os.path.join(ROOT, "dinov2_skin_lora.pt")
ACNE_MODEL_PATH = os.path.join(ROOT, "models", "acne_efficientnet_b0.pt")
SUN_DAMAGE_MODEL_PATH = os.path.join(ROOT, "models", "sun_damage_flag.pt")
ACNE_GRADE_NAMES = {0: "clear/mild", 1: "moderate", 2: "severe", 3: "very severe"}


class MLService:
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
        self.acne_model = None
        self.sun_damage_backbone = None
        self.sun_damage_coef = self.sun_damage_intercept = self.sun_damage_threshold = None
        self.load()
        self._load_specialist_models()

    def _load_specialist_models(self):
        """Dedicated specialist models trained on real labeled dermatology data (ACNE04, SCIN) —
        see train_acne_model.py / train_sun_damage_model.py for methodology and real metrics.
        Loaded best-effort: if a checkpoint is missing, the corresponding specialist output is
        simply omitted (callers fall back to the v2 multi-task model's own acne_severity, and
        skip the sun-damage flag) rather than failing the whole service."""
        if os.path.exists(ACNE_MODEL_PATH):
            from train_acne_model import AcneSeverityModel
            ck = torch.load(ACNE_MODEL_PATH, map_location="cpu", weights_only=False)
            m = AcneSeverityModel()
            m.load_state_dict(ck["state_dict"])
            self.acne_model = m.to(self.device).eval()
            print(f"[ml_service] acne specialist model loaded (test macro-F1 "
                  f"{ck['meta']['test_metrics']['macro_f1_score']})")
        if os.path.exists(SUN_DAMAGE_MODEL_PATH):
            from torchvision.models import efficientnet_b0, EfficientNet_B0_Weights
            ck = torch.load(SUN_DAMAGE_MODEL_PATH, map_location="cpu", weights_only=False)
            self.sun_damage_backbone = efficientnet_b0(weights=EfficientNet_B0_Weights.IMAGENET1K_V1).to(self.device).eval()
            self.sun_damage_coef = torch.as_tensor(ck["sklearn_coef"], dtype=torch.float32, device=self.device)
            self.sun_damage_intercept = torch.as_tensor(ck["sklearn_intercept"], dtype=torch.float32, device=self.device)
            self.sun_damage_threshold = float(ck["decision_threshold"])
            print(f"[ml_service] sun-damage flag model loaded (threshold {self.sun_damage_threshold:.3f}, "
                  f"{ck['meta']['limitation']})")

    def load(self):
        champ = None
        try:
            champ = db.get_champion()
        except Exception as e:
            print(f"[ml_service] Note: database not yet reachable for champion check ({e})")
        with self.lock:
            if champ and champ["version"].startswith("2.") and os.path.exists(champ["path"]):
                from model_v2 import load_checkpoint
                self.model, _ = load_checkpoint(champ["path"], self.device)
                self.kind, self.version = "v2", champ["version"]
            else:
                m = DINOv2SkinAnalyzer(model_name="facebook/dinov2-base", num_classes=4, r=16)
                if os.path.exists(V1_PATH):
                    m.load_state_dict(torch.load(V1_PATH, map_location="cpu"), strict=False)
                    if not champ:
                        try:
                            db.register_model("1.0", V1_PATH, notes="legacy v1 model (leaky evaluation)", make_champion=True)
                        except Exception:
                            pass
                self.model, self.kind, self.version = m.to(self.device).eval(), "v1", "1.0"
            n = 0
            try:
                n = self.memory.load_from_db(db, self.version)
            except Exception:
                pass
        print(f"[ml_service] model {self.version} ({self.kind}) on {self.device}; memory: {n} verified scans")

    @torch.no_grad()
    def infer(self, img):
        """Returns (type_probs [4], severity dict, embedding [D], model_version)."""
        with self.lock:
            if self.kind == "v2":
                from model_v2 import predict, SEVERITY_ATTRIBUTES
                probs, sev, emb = predict(self.model, [img], self.device, tta=True)
                severity = {a: float(sev[0, i]) for i, a in enumerate(SEVERITY_ATTRIBUTES)}
            else:
                from torchvision import transforms
                tf = transforms.Compose([transforms.Resize((224, 224)), transforms.ToTensor(),
                                         transforms.Normalize([0.485, 0.456, 0.406], [0.229, 0.224, 0.225])])
                out = self.model(tf(img).unsqueeze(0).to(self.device))
                probs = torch.softmax(out["type_logits"], 1).cpu()
                sev = out["severity_scores"].cpu()
                emb = out["embedding"].cpu()
                severity = {a: float(sev[0, i]) for i, a in enumerate(V1_SEVERITY_ATTRIBUTES)}
            return probs[0].numpy(), severity, emb[0].numpy(), self.version

    @torch.no_grad()
    def infer_acne(self, img):
        """Dedicated acne-severity model (EfficientNet-B0 fine-tuned on ACNE04). Returns None if
        the specialist checkpoint isn't loaded. severity_0_5 rescales the model's native 0-3
        Hayashi grade to this app's 0-5 severity scale so it drops straight into severity dicts."""
        if self.acne_model is None:
            return None
        from train_acne_model import transform_eval
        x = transform_eval(img.convert("RGB")).unsqueeze(0).to(self.device)
        out = self.acne_model(x)
        probs = torch.softmax(out["severity_logits"], 1)[0].cpu().numpy()
        grade = int(probs.argmax())
        return {
            "grade": grade, "grade_name": ACNE_GRADE_NAMES[grade], "confidence": round(float(probs[grade]), 4),
            "severity_0_5": round(grade * 5.0 / 3.0, 2), "grade_probabilities": {ACNE_GRADE_NAMES[g]: round(float(probs[g]), 4) for g in range(4)},
        }

    @torch.no_grad()
    def infer_sun_damage(self, img):
        """Dedicated sun-damage (actinic-damage) PRESENCE flag: frozen EfficientNet-B0 embedding
        + logistic regression, trained on only 22 confirmed-positive SCIN cases. Returns None if
        the specialist checkpoint isn't loaded. This is a coarse screening signal, not a severity
        score or diagnosis — see train_sun_damage_model.py's docstring for why."""
        if self.sun_damage_backbone is None:
            return None
        from torchvision import transforms
        tf = transforms.Compose([transforms.Resize((224, 224)), transforms.ToTensor(),
                                 transforms.Normalize([0.485, 0.456, 0.406], [0.229, 0.224, 0.225])])
        x = tf(img.convert("RGB")).unsqueeze(0).to(self.device)
        h = torch.flatten(self.sun_damage_backbone.avgpool(self.sun_damage_backbone.features(x)), 1)
        logit = (h @ self.sun_damage_coef.T + self.sun_damage_intercept).squeeze()
        prob = torch.sigmoid(logit).item()
        return {
            "probability": round(float(prob), 4), "flag": bool(prob >= self.sun_damage_threshold),
            "note": "Coarse screening flag trained on 22 confirmed cases — not a calibrated severity score.",
        }

    @staticmethod
    def analyze_cv(img):
        return analyze_image_quality_and_cv_metrics(img)

    @staticmethod
    def triage(severity, skin_type):
        return evaluate_clinical_triage(severity, skin_type)

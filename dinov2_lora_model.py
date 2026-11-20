import os
import cv2
import numpy as np
import torch
import torch.nn as nn
from PIL import Image
from transformers import AutoModel, AutoConfig
from peft import LoraConfig, get_peft_model, TaskType

SKIN_TYPES = ["dry", "oily", "normal", "combination"]
FITZPATRICK_TYPES = ["Type I (Very Fair)", "Type II (Fair)", "Type III (Medium)", "Type IV (Olive)", "Type V (Brown)", "Type VI (Dark Brown)"]

SEVERITY_ATTRIBUTES = [
    "acne_severity",
    "redness_severity",
    "pigmentation_melasma",
    "tanning_severity",
    "texture_roughness",
    "dehydration",
    "excessive_oil",
    "open_pores",
    "wrinkles",
    "skin_elasticity",
    "skin_sensitivity"
]

class DINOv2SkinAnalyzer(nn.Module):
    """
    DINOv2 backbone fine-tuned using LoRA for 11 multi-task facial skin analysis parameters:
    - Primary Skin Type Classification (dry, oily, normal, combination)
    - 11 Multi-Attribute Severity Estimations (0 to 5 scale)
    """
    def __init__(self, model_name="facebook/dinov2-base", num_classes=4, r=16, lora_alpha=16, dropout=0.1):
        super().__init__()
        self.model_name = model_name
        self.num_classes = num_classes
        
        self.dinov2 = AutoModel.from_pretrained(model_name)
        
        lora_config = LoraConfig(
            r=r,
            lora_alpha=lora_alpha,
            target_modules=["query", "value"],
            lora_dropout=dropout,
            bias="none",
        )
        self.dinov2 = get_peft_model(self.dinov2, lora_config)
        
        hidden_size = self.dinov2.config.hidden_size
        
        self.type_head = nn.Sequential(
            nn.Linear(hidden_size, 256),
            nn.BatchNorm1d(256),
            nn.GELU(),
            nn.Dropout(dropout),
            nn.Linear(256, num_classes)
        )
        
        self.severity_head = nn.Sequential(
            nn.Linear(hidden_size, 256),
            nn.BatchNorm1d(256),
            nn.GELU(),
            nn.Dropout(dropout),
            nn.Linear(256, len(SEVERITY_ATTRIBUTES)),
            nn.Sigmoid()
        )

    def forward(self, pixel_values):
        outputs = self.dinov2(pixel_values=pixel_values)
        cls_embedding = outputs.last_hidden_state[:, 0, :]
        
        logits_type = self.type_head(cls_embedding)
        severity_scores = self.severity_head(cls_embedding) * 5.0
        
        return {
            "type_logits": logits_type,
            "severity_scores": severity_scores,
            "embedding": cls_embedding
        }

def analyze_image_quality_and_cv_metrics(pil_img):
    """
    Precision Computer Vision Algorithms:
    1. Illumination-Normalized Facial Skin ROI Analysis (Cheeks & Forehead crop)
    2. Melasma Brown Pigmentation Density (b* > 6.0, a* > 3.0, L_diff > 6.5)
    3. Tanning Severity Index (ITA° & UV Melanin Offset)
    4. Fitzpatrick Skin Tone Classification (Types I - VI)
    5. Image Quality (Sharpness, Resolution, Illumination)
    """
    img_np = np.array(pil_img)
    if img_np.ndim == 2:
        img_np = cv2.cvtColor(img_np, cv2.COLOR_GRAY2RGB)
    elif img_np.shape[2] == 4:
        img_np = cv2.cvtColor(img_np, cv2.COLOR_RGBA2RGB)
        
    bgr = cv2.cvtColor(img_np, cv2.COLOR_RGB2BGR)
    h, w = bgr.shape[:2]
    
    # 1. Image Quality Inspector
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    resolution_score = "High Resolution" if (w >= 500 and h >= 500) else "Standard Resolution"
    blur_var = float(cv2.Laplacian(gray, cv2.CV_64F).var())
    blur_rating = "Sharp / Clear Focus" if blur_var > 100 else ("Moderate Focus" if blur_var > 30 else "Blurry Image")
    mean_light = float(np.mean(gray))
    lighting_status = "Optimal Illumination" if (70 <= mean_light <= 210) else ("Overexposed" if mean_light > 210 else "Underexposed")
    
    image_quality = {
        "resolution": f"{w}x{h} ({resolution_score})",
        "sharpness_score": round(blur_var, 1),
        "focus_rating": blur_rating,
        "lighting": lighting_status,
        "overall_quality": "PASSED" if blur_var >= 30 and (50 <= mean_light <= 220) else "LOW_QUALITY"
    }

    # 2. Extract Facial Skin ROI (Cheeks & Forehead: y: 15%-55%, x: 25%-75%) to ignore hair, beards, shirt collars, and outer background
    roi = bgr[int(h*0.15):int(h*0.55), int(w*0.25):int(w*0.75)]
    if roi.size == 0:
        roi = bgr

    # Convert ROI to LAB color space
    lab = cv2.cvtColor(roi, cv2.COLOR_BGR2LAB)
    L, a, b = cv2.split(lab)
    
    L_f = L.astype(np.float32)
    a_f = a.astype(np.float32) - 128.0
    b_f = b.astype(np.float32) - 128.0
    
    # Skin Tone & Fitzpatrick Scale (ITA°)
    L_star = L_f * 100.0 / 255.0
    ita = np.arctan2((L_star - 50.0), b_f) * (180.0 / np.pi)
    mean_ita = float(np.mean(ita))
    mean_L_star = float(np.mean(L_star))
    mean_b_f = float(np.mean(b_f))
    
    if mean_ita > 55:
        fitzpatrick = FITZPATRICK_TYPES[0]
    elif mean_ita > 41:
        fitzpatrick = FITZPATRICK_TYPES[1]
    elif mean_ita > 28:
        fitzpatrick = FITZPATRICK_TYPES[2]
    elif mean_ita > 10:
        fitzpatrick = FITZPATRICK_TYPES[3]
    elif mean_ita > -30:
        fitzpatrick = FITZPATRICK_TYPES[4]
    else:
        fitzpatrick = FITZPATRICK_TYPES[5]

    # 3. Illumination Gradient Normalization (Gaussian blur subtraction)
    L_blur = cv2.GaussianBlur(L_f, (31, 31), 0)
    L_local_diff = L_blur - L_f  # positive where facial skin is locally darker than surrounding skin

    # 4. Melasma Brown Pigmentation Density Mask:
    # - Local lightness dip > 6.5 units darker than local facial skin
    # - Melanin brown chroma (b* > 6.0 and a* > 3.0)
    melasma_pixels = (L_local_diff > 6.5) & (b_f > 6.0) & (a_f > 3.0)
    melasma_density = float(np.mean(melasma_pixels) * 100.0)

    # Calibrated Melasma Score (0.0 to 5.0)
    if melasma_density < 2.0:
        cv_melasma_severity = min(1.2, float(melasma_density * 0.5 + 0.2))
    elif melasma_density < 6.0:
        cv_melasma_severity = min(3.5, float(1.5 + (melasma_density - 2.0) * 0.5))
    else:
        cv_melasma_severity = min(5.0, float(3.8 + (melasma_density - 6.0) * 0.1))

    # 5. Tanning Index (Melanin UV Offset)
    cv_tanning_severity = float(np.clip((50.0 - mean_L_star) / 10.0 + (mean_b_f / 8.0), 0.0, 5.0))

    # 6. Texture Roughness Variance
    cv_texture = float(np.clip(np.log10(max(1.0, blur_var)) * 0.9, 0.5, 3.5))

    return {
        "image_quality": image_quality,
        "fitzpatrick_skin_tone": fitzpatrick,
        "ita_degrees": round(mean_ita, 1),
        "melasma_brown_density": round(melasma_density, 2),
        "cv_melasma_severity": round(cv_melasma_severity, 2),
        "cv_tanning_severity": round(cv_tanning_severity, 2),
        "cv_texture": round(cv_texture, 2)
    }

def evaluate_clinical_triage(severity_dict, skin_type):
    """
    Calibrated Dual-Tier Triage Engine:
    - RED ALERT: Severe Dermal Melasma (>= 3.8/5.0), Severe Cystic Acne, Acute Erythema, Barrier Failure.
    - GREEN BANNER: Normal skin or Mild/Moderate Melasma & Tanning treatable with topical skincare routines.
    """
    high_risk_reasons = []
    
    melasma_val = severity_dict.get("pigmentation_melasma", 0)
    acne_val = severity_dict.get("acne_severity", 0)
    redness_val = severity_dict.get("redness_severity", 0)
    sensitivity_val = severity_dict.get("skin_sensitivity", 0)
    
    if melasma_val >= 3.8:
        high_risk_reasons.append(f"Severe Dermal Melasma / Hyperpigmentation ({melasma_val:.1f}/5.0)")
    if acne_val >= 3.7:
        high_risk_reasons.append(f"Severe / Cystic Acne Grade 4-5 ({acne_val:.1f}/5.0)")
    if redness_val >= 3.8:
        high_risk_reasons.append(f"Acute Erythema / Rosacea ({redness_val:.1f}/5.0)")
    if sensitivity_val >= 4.0:
        high_risk_reasons.append(f"Severe Barrier Compromise ({sensitivity_val:.1f}/5.0)")
        
    is_severe = len(high_risk_reasons) > 0
    
    if is_severe:
        triage_status = "HIGH_RISK_CLINICAL"
        action_text = "DOCTOR_CONSULTATION"
        banner_title = "⚠️ Severe Clinical Condition — Doctor Consultation Required"
        banner_desc = f"Issues identified: {', '.join(high_risk_reasons)}. Deep dermal involvement requires clinical dermatologist evaluation."
        banner_color = "red"
    elif melasma_val > 1.8 or severity_dict.get("tanning_severity", 0) > 2.5:
        triage_status = "TOPICAL_MELASMA_ROUTINE"
        action_text = "PERSONALIZED_SKINCARE_ROUTINE"
        banner_title = "🟢 Melasma & Tanning Manageable with Topical Dermatological Routine"
        banner_desc = "Epidermal hyperpigmentation / sun tanning detected. Suitable for targeted active topical treatments (Hydroquinone, Tranexamic Acid, Azelaic Acid, Tinted Sunscreen)."
        banner_color = "green"
    else:
        triage_status = "SAFE_COSMETIC"
        action_text = "PERSONALIZED_SKINCARE_ROUTINE"
        banner_title = "🟢 Skin Condition Suitable for Cosmetic Care"
        banner_desc = "No high-risk clinical lesions detected. Personalized routine generated below."
        banner_color = "green"
        
    return {
        "status": triage_status,
        "is_severe": is_severe,
        "high_risk_reasons": high_risk_reasons,
        "recommended_action": action_text,
        "banner_title": banner_title,
        "banner_desc": banner_desc,
        "banner_color": banner_color
    }

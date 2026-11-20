"""
model_v2.py — Clarity AI Face Engine, model generation 2.

Changes vs dinov2_lora_model.py (v1):
  * LoRA on query, key, value AND attention-output projections (v1: query/value only)
  * Richer image embedding: [CLS token ; mean of patch tokens] (1536-d) -> LayerNorm
  * LayerNorm heads instead of BatchNorm (stable for batch-size-1 inference and small
    continual-learning batches)
  * Severity head predicts the 18 attributes that dermatologists actually labelled
    in the .xlsx sheets (v1 was trained on hard-coded per-class numbers)
  * Checkpoints store only the trained weights (LoRA + heads, ~5 MB) plus metadata;
    the frozen DINOv2 backbone is loaded from the HuggingFace cache.
"""
import os
import json
import torch
import torch.nn as nn
import torch.nn.functional as F
from torchvision import transforms
from transformers import AutoModel
from peft import LoraConfig, get_peft_model

SKIN_TYPES = ["dry", "oily", "normal", "combination"]

# Order matters: matches data_prep.SEVERITY_ATTRIBUTES
SEVERITY_ATTRIBUTES = [
    "acne_severity", "blackheads", "whiteheads", "open_pores", "excessive_oil",
    "skin_irritation", "skin_sensitivity", "redness_severity", "fine_lines_eyes",
    "eye_puffiness", "dark_circles", "wrinkles", "skin_elasticity", "dehydration",
    "pigmentation_melasma", "post_acne_marks", "texture_roughness", "freckles",
]

IMAGENET_MEAN = [0.485, 0.456, 0.406]
IMAGENET_STD = [0.229, 0.224, 0.225]
IMG_SIZE = 224

transform_eval = transforms.Compose([
    transforms.Resize((IMG_SIZE, IMG_SIZE)),
    transforms.ToTensor(),
    transforms.Normalize(IMAGENET_MEAN, IMAGENET_STD),
])

# Colour is diagnostic (redness, pigmentation) -> keep hue/saturation jitter small.
transform_train = transforms.Compose([
    transforms.RandomResizedCrop(IMG_SIZE, scale=(0.70, 1.0), ratio=(0.85, 1.18)),
    transforms.RandomHorizontalFlip(),
    transforms.RandomApply([transforms.RandomRotation(12)], p=0.5),
    transforms.RandomApply([transforms.ColorJitter(brightness=0.25, contrast=0.2, saturation=0.08, hue=0.01)], p=0.7),
    transforms.RandomApply([transforms.GaussianBlur(3, sigma=(0.1, 1.2))], p=0.2),
    transforms.ToTensor(),
    transforms.Normalize(IMAGENET_MEAN, IMAGENET_STD),
])


def pick_device():
    if torch.cuda.is_available():
        return torch.device("cuda")
    if hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
        return torch.device("mps")
    return torch.device("cpu")


class SkinAnalyzerV2(nn.Module):
    def __init__(self, backbone="facebook/dinov2-base", lora_r=16, lora_alpha=32, lora_dropout=0.1,
                 lora_targets=("query", "key", "value", "dense"), head_dropout=0.2,
                 n_types=len(SKIN_TYPES), n_sev=len(SEVERITY_ATTRIBUTES)):
        super().__init__()
        self.config = dict(backbone=backbone, lora_r=lora_r, lora_alpha=lora_alpha, lora_dropout=lora_dropout,
                           lora_targets=list(lora_targets), head_dropout=head_dropout, n_types=n_types, n_sev=n_sev)
        base = AutoModel.from_pretrained(backbone)
        try:
            self.backbone = get_peft_model(base, LoraConfig(
                r=lora_r, lora_alpha=lora_alpha, lora_dropout=lora_dropout,
                target_modules=r".*(query|key|value|dense)", bias="none"))
        except Exception:
            self.backbone = get_peft_model(base, LoraConfig(
                r=lora_r, lora_alpha=lora_alpha, lora_dropout=lora_dropout,
                target_modules=list(lora_targets), bias="none"))
        hid = base.config.hidden_size
        self.embed_dim = hid * 2
        self.norm = nn.LayerNorm(self.embed_dim)

        def head(n_out):
            return nn.Sequential(nn.Linear(self.embed_dim, 512), nn.LayerNorm(512), nn.GELU(),
                                 nn.Dropout(head_dropout), nn.Linear(512, n_out))
        self.type_head = head(n_types)
        self.severity_head = head(n_sev)

    def embed(self, pixel_values):
        h = self.backbone(pixel_values=pixel_values).last_hidden_state
        return self.norm(torch.cat([h[:, 0], h[:, 1:].mean(1)], dim=1))

    def forward(self, pixel_values):
        e = self.embed(pixel_values)
        return {
            "type_logits": self.type_head(e),
            "severity_scores": torch.sigmoid(self.severity_head(e)) * 5.0,
            "embedding": e,
        }

    # ---- only the trainable part is saved (LoRA adapters + norm + heads)
    def trainable_state_dict(self):
        names = {n for n, p in self.named_parameters() if p.requires_grad}
        return {k: v.detach().cpu() for k, v in self.state_dict().items() if k in names}


def save_checkpoint(model, path, meta=None):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    torch.save({"config": model.config, "state_dict": model.trainable_state_dict(),
                "skin_types": SKIN_TYPES, "severity_attributes": SEVERITY_ATTRIBUTES,
                "meta": meta or {}}, path)


def load_checkpoint(path, device=None):
    device = device or pick_device()
    ck = torch.load(path, map_location="cpu", weights_only=False)
    model = SkinAnalyzerV2(**ck["config"])
    missing, unexpected = model.load_state_dict(ck["state_dict"], strict=False)
    trainable = {n for n, p in model.named_parameters() if p.requires_grad}
    not_loaded = [m for m in missing if m in trainable]
    if not_loaded:
        raise RuntimeError(f"Checkpoint {path} is missing trained weights: {not_loaded[:5]}")
    model.to(device).eval()
    return model, ck.get("meta", {})


@torch.no_grad()
def predict(model, pil_images, device=None, tta=True):
    """Returns (type_probs [B,4], severity [B,18], embedding [B,1536]) as CPU tensors."""
    device = device or next(model.parameters()).device
    x = torch.stack([transform_eval(im.convert("RGB")) for im in pil_images]).to(device)
    out = model(x)
    probs = F.softmax(out["type_logits"], 1)
    sev, emb = out["severity_scores"], out["embedding"]
    if tta:  # horizontal-flip test-time augmentation
        o2 = model(torch.flip(x, dims=[3]))
        probs = (probs + F.softmax(o2["type_logits"], 1)) / 2
        sev = (sev + o2["severity_scores"]) / 2
        emb = (emb + o2["embedding"]) / 2
    return probs.cpu(), sev.cpu(), emb.cpu()

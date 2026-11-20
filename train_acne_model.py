"""
train_acne_model.py — trains a dedicated acne-severity model on the real
ACNE04 benchmark (Wu et al., ICCV 2019: 1,457 dermatologist-graded face
images, 4-level Hayashi severity scale), via transfer learning:

    EfficientNet-B0 (ImageNet-pretrained)  ->  fine-tune  ->  ACNE04  ->  this model

Two heads share the EfficientNet-B0 trunk: 4-class severity classification
(the primary task) and lesion-count regression (log1p-transformed) as an
auxiliary signal — the joint-task setup the original ACNE04 paper found
improves grading accuracy over classification alone.

Run:
    python prepare_acne_data.py          # once, materializes images (already done)
    python train_acne_model.py           # full training
    python train_acne_model.py --quick   # smoke test

Evaluation:
  - Primary: the official held-out ACNE04 test split (292 images), never
    used for training or early-stopping.
  - Cross-dataset generalization + Fitzpatrick-skin-tone stratification:
    against SCIN's 207 dermatologist-confirmed "Acne" cases (no severity
    grade, but real-world photos with known Fitzpatrick skin type) — see
    evaluate_on_scin() and prepare_scin_data.py.
"""
import os
import sys
import json
import time
import argparse
import numpy as np
import pandas as pd
from PIL import Image

import torch
import torch.nn as nn
import torch.nn.functional as F
from torch.utils.data import Dataset, DataLoader, WeightedRandomSampler
from torchvision import transforms
from torchvision.models import efficientnet_b0, EfficientNet_B0_Weights
from sklearn.metrics import (accuracy_score, balanced_accuracy_score, precision_recall_fscore_support,
                             confusion_matrix)

ROOT = os.path.dirname(os.path.abspath(__file__))
MANIFEST = os.path.join(ROOT, "data", "acne_datasets", "acne04_manifest.csv")
MODELS_DIR = os.path.join(ROOT, "models")
LEVELS = [0, 1, 2, 3]
LEVEL_NAMES = {0: "clear/mild", 1: "moderate", 2: "severe", 3: "very severe"}

IMAGENET_MEAN = [0.485, 0.456, 0.406]
IMAGENET_STD = [0.229, 0.224, 0.225]
IMG_SIZE = 224

transform_eval = transforms.Compose([
    transforms.Resize((IMG_SIZE, IMG_SIZE)),
    transforms.ToTensor(),
    transforms.Normalize(IMAGENET_MEAN, IMAGENET_STD),
])

transform_train = transforms.Compose([
    transforms.RandomResizedCrop(IMG_SIZE, scale=(0.75, 1.0), ratio=(0.9, 1.11)),
    transforms.RandomHorizontalFlip(),
    transforms.RandomApply([transforms.ColorJitter(brightness=0.2, contrast=0.2, saturation=0.1)], p=0.7),
    transforms.ToTensor(),
    transforms.Normalize(IMAGENET_MEAN, IMAGENET_STD),
])


def pick_device():
    if torch.cuda.is_available():
        return torch.device("cuda")
    if hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
        return torch.device("mps")
    return torch.device("cpu")


class AcneDataset(Dataset):
    def __init__(self, df, transform):
        self.paths = [os.path.join(ROOT, p) for p in df["path"]]
        self.levels = df["level"].astype(int).tolist()
        self.log_counts = np.log1p(df["num_lesions"].fillna(0).astype(float).values).astype(np.float32)
        self.transform = transform

    def __len__(self):
        return len(self.paths)

    def __getitem__(self, i):
        img = Image.open(self.paths[i]).convert("RGB")
        return self.transform(img), self.levels[i], self.log_counts[i]


class AcneSeverityModel(nn.Module):
    def __init__(self, n_levels=4, dropout=0.3):
        super().__init__()
        backbone = efficientnet_b0(weights=EfficientNet_B0_Weights.IMAGENET1K_V1)
        self.features = backbone.features
        self.pool = backbone.avgpool
        feat_dim = backbone.classifier[1].in_features  # 1280
        self.severity_head = nn.Sequential(nn.Dropout(dropout), nn.Linear(feat_dim, n_levels))
        self.count_head = nn.Sequential(nn.Dropout(dropout), nn.Linear(feat_dim, 1))

    def forward(self, x):
        h = torch.flatten(self.pool(self.features(x)), 1)
        return {"severity_logits": self.severity_head(h), "log_count": self.count_head(h).squeeze(-1), "embedding": h}


def balanced_sampler(levels):
    levels = np.asarray(levels)
    counts = np.bincount(levels, minlength=len(LEVELS)).astype(np.float64)
    w = 1.0 / np.maximum(counts[levels], 1)
    return WeightedRandomSampler(torch.as_tensor(w), num_samples=len(levels), replacement=True)


@torch.no_grad()
def evaluate(model, loader, device):
    model.eval()
    P, Y = [], []
    for x, y, _ in loader:
        out = model(x.to(device))
        P.append(out["severity_logits"].argmax(1).cpu())
        Y.append(y)
    P, Y = torch.cat(P).numpy(), torch.cat(Y).numpy()
    prec, rec, f1, _ = precision_recall_fscore_support(Y, P, labels=LEVELS, average=None, zero_division=0)
    return {
        "n": len(Y), "accuracy": round(float(accuracy_score(Y, P)), 4),
        "balanced_accuracy": round(float(balanced_accuracy_score(Y, P)), 4),
        "macro_f1_score": round(float(f1.mean()), 4),
        "per_class_f1": {LEVEL_NAMES[l]: round(float(f1[l]), 4) for l in LEVELS},
        "per_class_recall": {LEVEL_NAMES[l]: round(float(rec[l]), 4) for l in LEVELS},
        "confusion_matrix": {"labels": [LEVEL_NAMES[l] for l in LEVELS],
                             "matrix": confusion_matrix(Y, P, labels=LEVELS).tolist()},
    }


def build_optimizer(model, lr_backbone, lr_head, wd):
    backbone_p = list(model.features.parameters())
    head_p = list(model.severity_head.parameters()) + list(model.count_head.parameters())
    return torch.optim.AdamW([{"params": backbone_p, "lr": lr_backbone}, {"params": head_p, "lr": lr_head}],
                             weight_decay=wd)


def fit(model, train_df, val_df, device, epochs, batch_size, lr_backbone, lr_head, wd, count_loss_weight,
       label_smoothing, patience, warmup_frac, num_workers, log=print, max_steps_per_epoch=None):
    tr = AcneDataset(train_df, transform_train)
    va = AcneDataset(val_df, transform_eval)
    pin = device.type == "cuda"
    tl = DataLoader(tr, batch_size=batch_size, sampler=balanced_sampler(tr.levels), num_workers=num_workers,
                    pin_memory=pin, drop_last=True, persistent_workers=num_workers > 0)
    vl = DataLoader(va, batch_size=32, shuffle=False, num_workers=num_workers, pin_memory=pin)

    opt = build_optimizer(model, lr_backbone, lr_head, wd)
    steps_per_epoch = len(tl) if not max_steps_per_epoch else min(len(tl), max_steps_per_epoch)
    total, warm = epochs * steps_per_epoch, max(1, int(epochs * steps_per_epoch * warmup_frac))
    import math
    sched = torch.optim.lr_scheduler.LambdaLR(
        opt, lambda s: (s + 1) / warm if s < warm else 0.5 * (1 + math.cos(math.pi * (s - warm) / max(1, total - warm))))
    ce = nn.CrossEntropyLoss(label_smoothing=label_smoothing)

    best, best_state, bad, hist = -1.0, None, 0, []
    for ep in range(epochs):
        model.train()
        t0, run, n = time.time(), 0.0, 0
        for step, (x, y, lc) in enumerate(tl):
            if max_steps_per_epoch and step >= max_steps_per_epoch:
                break
            x, y, lc = x.to(device), y.to(device), lc.to(device)
            out = model(x)
            loss = ce(out["severity_logits"], y) + count_loss_weight * F.smooth_l1_loss(out["log_count"], lc)
            opt.zero_grad(set_to_none=True)
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            opt.step(); sched.step()
            run += loss.item() * x.size(0); n += x.size(0)
        vm = evaluate(model, vl, device)
        hist.append({"epoch": ep + 1, "train_loss": round(run / max(n, 1), 4), "val_acc": vm["accuracy"],
                    "val_f1": vm["macro_f1_score"], "sec": round(time.time() - t0, 1)})
        log(f"epoch {ep + 1:02d}/{epochs}  loss {run / max(n, 1):.4f}  val_acc {vm['accuracy']}  "
            f"val_f1 {vm['macro_f1_score']}  ({time.time() - t0:.0f}s)")
        if vm["macro_f1_score"] > best + 1e-4:
            best, bad = vm["macro_f1_score"], 0
            best_state = {k: v.detach().cpu().clone() for k, v in model.state_dict().items()}
            best_vm = vm
        else:
            bad += 1
            if bad >= patience:
                log(f"early stop (no val improvement for {patience} epochs)")
                break
    model.load_state_dict(best_state)
    return best_state, best_vm, hist


@torch.no_grad()
def evaluate_on_scin(model, device, scin_manifest_path):
    """Cross-dataset generalization + Fitzpatrick-stratified check: ACNE04 has no severity
    grade for these, but every image is a dermatologist-CONFIRMED acne case, so a model that
    generalizes should rarely predict grade 0 ('clear') on them. Reported per Fitzpatrick type."""
    if not os.path.exists(scin_manifest_path):
        return None
    df = pd.read_csv(scin_manifest_path)
    acne = df[df["label"] == "acne"]
    if acne.empty:
        return None
    model.eval()
    preds, fsts = [], []
    for _, r in acne.iterrows():
        p = os.path.join(ROOT, r["path"])
        if not os.path.exists(p):
            continue
        img = transform_eval(Image.open(p).convert("RGB")).unsqueeze(0).to(device)
        pred = model(img)["severity_logits"].argmax(1).item()
        preds.append(pred)
        fsts.append(r.get("fitzpatrick_skin_type") or "UNKNOWN")
    preds, fsts = np.array(preds), np.array(fsts)
    overall = {"n": len(preds), "pct_predicted_clear_grade0": round(float((preds == 0).mean()), 4),
              "mean_predicted_grade": round(float(preds.mean()), 3)}
    by_fst = {}
    for fst in sorted(set(fsts)):
        mask = fsts == fst
        if mask.sum() >= 3:
            by_fst[fst] = {"n": int(mask.sum()), "pct_predicted_clear_grade0": round(float((preds[mask] == 0).mean()), 4),
                          "mean_predicted_grade": round(float(preds[mask].mean()), 3)}
    return {"overall": overall, "by_fitzpatrick_skin_type": by_fst}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--epochs", type=int, default=20)
    ap.add_argument("--batch-size", type=int, default=24)
    ap.add_argument("--lr-backbone", type=float, default=5e-5)
    ap.add_argument("--lr-head", type=float, default=5e-4)
    ap.add_argument("--workers", type=int, default=2)
    ap.add_argument("--quick", action="store_true")
    args = ap.parse_args()

    if not os.path.exists(MANIFEST):
        sys.exit("ACNE04 manifest not found — run `python prepare_acne_data.py` first.")
    torch.manual_seed(42); np.random.seed(42)
    device = pick_device()
    print(f"device: {device}")

    df = pd.read_csv(MANIFEST)
    train_all = df[df.split == "train"]
    te = df[df.split == "test"]
    # fold_0_split only subdivides the official test split in this release, not train, so carve
    # a validation set out of train with a stratified-by-level random split instead (seeded, fixed).
    rng = np.random.RandomState(42)
    val_idx = []
    for level in LEVELS:
        idx = train_all[train_all.level == level].index.to_numpy().copy()
        rng.shuffle(idx)
        val_idx.extend(idx[: max(1, int(round(len(idx) * 0.15)))])
    va = train_all.loc[val_idx]
    tr = train_all.drop(index=val_idx)
    print(f"train {len(tr)} / val {len(va)} / test {len(te)} images")

    model = AcneSeverityModel().to(device)
    n_tr = sum(p.numel() for p in model.parameters() if p.requires_grad)
    print(f"trainable params: {n_tr:,}")

    t0 = time.time()
    _, val_metrics, hist = fit(model, tr, va, device, epochs=args.epochs, batch_size=args.batch_size,
                               lr_backbone=args.lr_backbone, lr_head=args.lr_head, wd=0.05, count_loss_weight=0.3,
                               label_smoothing=0.1, patience=5, warmup_frac=0.08, num_workers=args.workers,
                               max_steps_per_epoch=5 if args.quick else None)
    dur = time.time() - t0

    tl = DataLoader(AcneDataset(te, transform_eval), batch_size=32, shuffle=False, num_workers=args.workers)
    test_metrics = evaluate(model, tl, device)
    print("\nTEST (official ACNE04 held-out split):")
    print(json.dumps({k: v for k, v in test_metrics.items() if k != "confusion_matrix"}, indent=2))

    scin_eval = evaluate_on_scin(model, device, os.path.join(ROOT, "data", "scin", "scin_manifest.csv"))
    if scin_eval:
        print("\nCross-dataset generalization check (SCIN real-world acne-confirmed photos):")
        print(json.dumps(scin_eval, indent=2))

    os.makedirs(MODELS_DIR, exist_ok=True)
    path = os.path.join(MODELS_DIR, "acne_efficientnet_b0.pt")
    meta = {"trained_at": time.strftime("%Y-%m-%d %H:%M:%S"), "backbone": "efficientnet_b0 (ImageNet pretrained)",
            "dataset": "ACNE04 (Wu et al. ICCV 2019), official train/test split", "train_images": len(tr),
            "val_metrics": val_metrics, "test_metrics": test_metrics, "scin_generalization_check": scin_eval,
            "history": hist, "duration_sec": round(dur, 1), "args": vars(args)}
    torch.save({"state_dict": model.state_dict(), "meta": meta}, path)
    print(f"\nsaved {path}")


if __name__ == "__main__":
    main()

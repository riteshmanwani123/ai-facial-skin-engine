"""
train_v2.py — Accuracy-focused training for Clarity AI Face Engine (model v2).

Run once after `python data_prep.py`:
    python train_v2.py                    # full training (~45-70 min on an M-series Mac)
    python train_v2.py --epochs 2 --quick # smoke test (few minutes)

What it does differently from train_and_evaluate.py (v1):
  1. Leak-free splits (data/manifest.csv, grouped by face) -> honest numbers
  2. Uses the REAL 0-5 severity labels from the xlsx sheets (masked loss: only labelled
     images/attributes contribute) instead of hard-coded per-class numbers
  3. Class-balanced sampling + label smoothing (fixes weak "combination" class)
  4. Stronger but colour-safe augmentation, 15 epochs, warm-up + cosine LR,
     separate LRs for LoRA vs heads, gradient clipping
  5. Early stopping / checkpoint selection on validation macro-F1
  6. Flip test-time augmentation at evaluation
  7. Registers the model in the local DB and writes an HONEST metrics report
"""
import os
import sys
import json
import math
import time
import argparse
import numpy as np
import pandas as pd
from PIL import Image

import torch
import torch.nn as nn
import torch.nn.functional as F
from torch.utils.data import Dataset, DataLoader, WeightedRandomSampler
from sklearn.metrics import (accuracy_score, balanced_accuracy_score, precision_recall_fscore_support,
                             confusion_matrix, mean_absolute_error)

from model_v2 import (SkinAnalyzerV2, SKIN_TYPES, SEVERITY_ATTRIBUTES, transform_train, transform_eval,
                      pick_device, save_checkpoint, load_checkpoint)

ROOT = os.path.dirname(os.path.abspath(__file__))
MANIFEST = os.path.join(ROOT, "data", "manifest.csv")
MODELS_DIR = os.path.join(ROOT, "models")


# ------------------------------------------------------------------ data
class SkinDatasetV2(Dataset):
    """Rows need: path (abs or relative to ROOT), label (skin type str or None), sev_<attr> columns (NaN = unknown)."""

    def __init__(self, df, transform):
        self.paths = [p if os.path.isabs(p) else os.path.join(ROOT, p) for p in df["path"]]
        self.labels = [SKIN_TYPES.index(l) if isinstance(l, str) and l in SKIN_TYPES else -100 for l in df["label"]]
        sev = np.stack([df.get("sev_" + a, pd.Series([np.nan] * len(df))).values.astype(np.float32)
                        for a in SEVERITY_ATTRIBUTES], 1)
        self.sev_mask = ~np.isnan(sev)
        self.sev = np.nan_to_num(sev, nan=0.0)
        self.transform = transform

    def __len__(self):
        return len(self.paths)

    def __getitem__(self, i):
        img = Image.open(self.paths[i]).convert("RGB")
        return (self.transform(img), self.labels[i], torch.from_numpy(self.sev[i]),
                torch.from_numpy(self.sev_mask[i].astype(np.float32)))


def balanced_sampler(labels, sev_mask, sev_boost=3.0):
    """Each skin type gets equal expected mass; images with real severity labels are drawn more often."""
    labels = np.asarray(labels)
    w = np.zeros(len(labels), np.float64)
    valid = labels >= 0
    counts = np.bincount(labels[valid], minlength=len(SKIN_TYPES)).astype(np.float64)
    w[valid] = 1.0 / np.maximum(counts[labels[valid]], 1)
    w[~valid] = w[valid].mean() if valid.any() else 1.0
    w *= np.where(sev_mask.any(1), sev_boost, 1.0)
    return WeightedRandomSampler(torch.as_tensor(w), num_samples=len(labels), replacement=True)


# ------------------------------------------------------------- training
def masked_severity_loss(pred, target, mask):
    if mask.sum() == 0:
        return pred.sum() * 0.0
    return (F.smooth_l1_loss(pred, target, reduction="none", beta=0.5) * mask).sum() / mask.sum()


@torch.no_grad()
def evaluate(model, loader, device, tta=True):
    model.eval()
    P, Y, S, T, M, E = [], [], [], [], [], []
    for x, y, s, m in loader:
        x = x.to(device)
        o = model(x)
        p, sv = F.softmax(o["type_logits"], 1), o["severity_scores"]
        if tta:
            o2 = model(torch.flip(x, dims=[3]))
            p, sv = (p + F.softmax(o2["type_logits"], 1)) / 2, (sv + o2["severity_scores"]) / 2
        P.append(p.cpu()); Y.append(y); S.append(sv.cpu()); T.append(s); M.append(m)
    P, Y, S, T, M = torch.cat(P).numpy(), torch.cat(Y).numpy(), torch.cat(S).numpy(), torch.cat(T).numpy(), torch.cat(M).numpy()
    return metrics_from_predictions(P, Y, S, T, M)


def metrics_from_predictions(P, Y, S, T, M):
    keep = Y >= 0
    pred = P.argmax(1)
    out = {"n": int(keep.sum())}
    if keep.any():
        yk, pk = Y[keep], pred[keep]
        prec, rec, f1, _ = precision_recall_fscore_support(yk, pk, labels=range(len(SKIN_TYPES)), average=None, zero_division=0)
        out.update({
            "accuracy": round(float(accuracy_score(yk, pk)), 4),
            "balanced_accuracy": round(float(balanced_accuracy_score(yk, pk)), 4),
            "macro_precision": round(float(prec.mean()), 4),
            "macro_recall": round(float(rec.mean()), 4),
            "macro_f1_score": round(float(f1.mean()), 4),
            "per_class_f1": {SKIN_TYPES[i]: round(float(f1[i]), 4) for i in range(len(SKIN_TYPES))},
            "per_class_recall": {SKIN_TYPES[i]: round(float(rec[i]), 4) for i in range(len(SKIN_TYPES))},
            "confusion_matrix": {"labels": SKIN_TYPES,
                                 "matrix": confusion_matrix(yk, pk, labels=range(len(SKIN_TYPES))).tolist()},
        })
    if M.sum() > 0:
        per_attr = {}
        for j, a in enumerate(SEVERITY_ATTRIBUTES):
            mj = M[:, j] > 0
            if mj.sum() >= 3:
                per_attr[a] = round(float(np.abs(S[mj, j] - T[mj, j]).mean()), 3)
        out["severity_mae"] = round(float((np.abs(S - T) * M).sum() / M.sum()), 4)
        out["severity_mae_per_attribute"] = per_attr
        out["severity_labelled_images"] = int((M.sum(1) > 0).sum())
    return out


def build_optimizer(model, lr_lora, lr_head, wd):
    lora, head = [], []
    for n, p in model.named_parameters():
        if p.requires_grad:
            (lora if "lora_" in n else head).append(p)
    return torch.optim.AdamW([{"params": lora, "lr": lr_lora}, {"params": head, "lr": lr_head}], weight_decay=wd)


def fit(model, train_df, val_df, device, epochs=15, batch_size=16, lr_lora=2e-4, lr_head=1e-3, wd=0.05,
        sev_weight=1.0, label_smoothing=0.1, patience=4, warmup_frac=0.06, num_workers=2, log=print,
        max_steps_per_epoch=None):
    """Generic trainer used by train_v2.py and continual_learning.py. Returns (best_state, best_val_metrics, history)."""
    tr = SkinDatasetV2(train_df, transform_train)
    va = SkinDatasetV2(val_df, transform_eval)
    pin = device.type == "cuda"
    tl = DataLoader(tr, batch_size=batch_size, sampler=balanced_sampler(tr.labels, tr.sev_mask),
                    num_workers=num_workers, pin_memory=pin, drop_last=True, persistent_workers=num_workers > 0)
    vl = DataLoader(va, batch_size=32, shuffle=False, num_workers=num_workers, pin_memory=pin)

    opt = build_optimizer(model, lr_lora, lr_head, wd)
    steps_per_epoch = len(tl) if not max_steps_per_epoch else min(len(tl), max_steps_per_epoch)
    total, warm = epochs * steps_per_epoch, max(1, int(epochs * steps_per_epoch * warmup_frac))
    sched = torch.optim.lr_scheduler.LambdaLR(
        opt, lambda s: (s + 1) / warm if s < warm else 0.5 * (1 + math.cos(math.pi * (s - warm) / max(1, total - warm))))
    ce = nn.CrossEntropyLoss(label_smoothing=label_smoothing, ignore_index=-100)

    best, best_state, bad, hist = -1.0, None, 0, []
    for ep in range(epochs):
        model.train()
        t0, run, n = time.time(), 0.0, 0
        for step, (x, y, s, m) in enumerate(tl):
            if max_steps_per_epoch and step >= max_steps_per_epoch:
                break
            x, y, s, m = x.to(device), y.to(device), s.to(device), m.to(device)
            o = model(x)
            loss_t = ce(o["type_logits"], y) if (y >= 0).any() else o["type_logits"].sum() * 0
            loss = loss_t + sev_weight * masked_severity_loss(o["severity_scores"], s, m)
            opt.zero_grad(set_to_none=True)
            loss.backward()
            torch.nn.utils.clip_grad_norm_([p for p in model.parameters() if p.requires_grad], 1.0)
            opt.step(); sched.step()
            run += loss.item() * x.size(0); n += x.size(0)
        vm = evaluate(model, vl, device, tta=False)
        score = vm.get("macro_f1_score", 0.0) - 0.02 * vm.get("severity_mae", 0.0)
        hist.append({"epoch": ep + 1, "train_loss": round(run / max(n, 1), 4), **{k: vm.get(k) for k in
                     ("accuracy", "macro_f1_score", "severity_mae")}, "sec": round(time.time() - t0, 1)})
        log(f"epoch {ep + 1:02d}/{epochs}  loss {run / max(n, 1):.4f}  val_acc {vm.get('accuracy')}  "
            f"val_f1 {vm.get('macro_f1_score')}  val_sevMAE {vm.get('severity_mae')}  ({time.time() - t0:.0f}s)")
        if score > best + 1e-4:
            best, bad = score, 0
            best_state = {k: v.detach().cpu().clone() for k, v in model.trainable_state_dict().items()}
            best_vm = vm
        else:
            bad += 1
            if bad >= patience:
                log(f"early stop (no val improvement for {patience} epochs)")
                break
    model.load_state_dict(best_state, strict=False)
    return best_state, best_vm, hist


def next_version():
    os.makedirs(MODELS_DIR, exist_ok=True)
    existing = [f for f in os.listdir(MODELS_DIR) if f.startswith("clarity_v2.") and f.endswith(".pt")]
    nums = [int(f.split(".")[1]) for f in existing if f.split(".")[1].isdigit()]
    return f"2.{(max(nums) + 1) if nums else 0}"


# ------------------------------------------------------------------ main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--epochs", type=int, default=15)
    ap.add_argument("--batch-size", type=int, default=16)
    ap.add_argument("--lr-lora", type=float, default=2e-4)
    ap.add_argument("--lr-head", type=float, default=1e-3)
    ap.add_argument("--lora-r", type=int, default=16)
    ap.add_argument("--workers", type=int, default=2)
    ap.add_argument("--quick", action="store_true", help="tiny run to check everything works")
    args = ap.parse_args()

    if not os.path.exists(MANIFEST):
        sys.exit("data/manifest.csv not found — run `python data_prep.py` first.")
    torch.manual_seed(42); np.random.seed(42)
    device = pick_device()
    print(f"device: {device}")

    df = pd.read_csv(MANIFEST)
    tr, va, te = df[df.split == "train"], df[df.split == "val"], df[df.split == "test"]
    print(f"train {len(tr)} imgs / {tr.group.nunique()} faces | val {len(va)} / {va.group.nunique()} | "
          f"test {len(te)} / {te.group.nunique()}  | severity-labelled train imgs: {int(tr.has_severity.sum())}")

    model = SkinAnalyzerV2(lora_r=args.lora_r, lora_alpha=2 * args.lora_r).to(device)
    n_tr = sum(p.numel() for p in model.parameters() if p.requires_grad)
    n_all = sum(p.numel() for p in model.parameters())
    print(f"trainable params {n_tr:,} / {n_all:,} ({100 * n_tr / n_all:.2f}%)")

    t0 = time.time()
    _, val_metrics, hist = fit(model, tr, va, device, epochs=args.epochs, batch_size=args.batch_size,
                               lr_lora=args.lr_lora, lr_head=args.lr_head, num_workers=args.workers,
                               max_steps_per_epoch=5 if args.quick else None)
    dur = time.time() - t0

    tl = DataLoader(SkinDatasetV2(te, transform_eval), batch_size=32, shuffle=False, num_workers=args.workers)
    test_metrics = evaluate(model, tl, device, tta=True)
    print("\nTEST (faces never seen in training):")
    print(json.dumps({k: v for k, v in test_metrics.items() if k != "confusion_matrix"}, indent=2))

    version = next_version()
    path = os.path.join(MODELS_DIR, f"clarity_v2.{version.split('.')[1]}.pt")
    meta = {"version": version, "trained_at": time.strftime("%Y-%m-%d %H:%M:%S"), "train_images": len(tr),
            "val_metrics": val_metrics, "test_metrics": test_metrics, "history": hist,
            "duration_sec": round(dur, 1), "args": vars(args)}
    save_checkpoint(model, path, meta)
    print(f"saved {path}")

    import database as db
    champ = db.get_champion()
    promote = champ is None or champ["version"].startswith("1.") or \
        test_metrics.get("macro_f1_score", 0) >= (champ.get("metrics") or {}).get("test", {}).get("macro_f1_score", 0)
    if args.quick:
        promote = champ is None
    db.register_model(version, os.path.relpath(path, ROOT), {"val": val_metrics, "test": test_metrics},
                      parent_version=champ["version"] if champ else None,
                      notes="quick smoke test" if args.quick else "train_v2 full training", make_champion=promote)
    print(f"registered model {version} (champion: {promote})")

    report = {
        "model_metadata": {
            "version": version, "backbone": "facebook/dinov2-base",
            "adaptation": f"LoRA r={args.lora_r} on query/key/value/attn-output + CLS+mean-patch pooling",
            "trainable_parameters": n_tr, "total_parameters": n_all,
            "training_duration_seconds": round(dur, 1), "test_set_size": test_metrics["n"],
            "test_set_faces": int(te.group.nunique()), "total_attribute_count": len(SEVERITY_ATTRIBUTES),
            "evaluation_protocol": "Group split by source face; test faces never seen during training. Flip TTA.",
        },
        "clinical_performance_metrics": test_metrics,
        "validation_metrics": val_metrics,
        "notes": [
            "All figures are measured on held-out faces; nothing in this report is hard-coded.",
            "Severity MAE is measured only on images with dermatologist 0-5 labels in the test split (small n).",
            "This is a research prototype, not a cleared medical device.",
        ],
    }
    if not args.quick:
        with open(os.path.join(ROOT, "hospital_deliverables_report.json"), "w") as f:
            json.dump(report, f, indent=2)
        print("wrote hospital_deliverables_report.json")


if __name__ == "__main__":
    main()

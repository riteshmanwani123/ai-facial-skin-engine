"""
train_sun_damage_model.py — a deliberately small, honest sun-damage
(actinic damage) PRESENCE classifier.

There is no public severity-graded sun-damage/photoaging dataset (checked:
the closest match, iToBoS, requires a data-use agreement). What exists is
22 dermatologist-confirmed "Actinic Keratosis" cases in Google's SCIN
dataset (https://github.com/google-research-datasets/scin, public, no
auth) — real but small. Fine-tuning a big CNN end-to-end on 22 positives
would overfit badly, so this instead:

    EfficientNet-B0 (ImageNet-pretrained, FROZEN)  ->  embeddings
                                                          ↓
                                   logistic regression (scikit-learn,
                                   class-balanced, stratified 5-fold CV)

This is a coarse presence/absence flag meant to feed the existing
CV-based tanning/pigmentation heuristic in dinov2_lora_model.py, not a
standalone severity score — reported with 5-fold cross-validated metrics
(not a single train/test split, which would be far too noisy at this N)
and stratified by Fitzpatrick skin type so its real, limited reliability
is visible rather than hidden behind one lucky split.

Run: python prepare_scin_data.py   (once)
     python train_sun_damage_model.py
"""
import os
import json
import numpy as np
import pandas as pd
from PIL import Image

import torch
from torchvision import transforms
from torchvision.models import efficientnet_b0, EfficientNet_B0_Weights
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import StratifiedKFold
from sklearn.metrics import roc_auc_score, f1_score, precision_score, recall_score, precision_recall_curve

ROOT = os.path.dirname(os.path.abspath(__file__))
MANIFEST = os.path.join(ROOT, "data", "scin", "scin_manifest.csv")
MODELS_DIR = os.path.join(ROOT, "models")
IMG_SIZE = 224
IMAGENET_MEAN = [0.485, 0.456, 0.406]
IMAGENET_STD = [0.229, 0.224, 0.225]

transform = transforms.Compose([
    transforms.Resize((IMG_SIZE, IMG_SIZE)),
    transforms.ToTensor(),
    transforms.Normalize(IMAGENET_MEAN, IMAGENET_STD),
])


def pick_device():
    if torch.cuda.is_available():
        return torch.device("cuda")
    if hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
        return torch.device("mps")
    return torch.device("cpu")


@torch.no_grad()
def embed_images(paths, device, batch_size=16):
    backbone = efficientnet_b0(weights=EfficientNet_B0_Weights.IMAGENET1K_V1).to(device).eval()
    feats = []
    for i in range(0, len(paths), batch_size):
        batch = paths[i:i + batch_size]
        imgs = torch.stack([transform(Image.open(os.path.join(ROOT, p)).convert("RGB")) for p in batch]).to(device)
        h = torch.flatten(backbone.avgpool(backbone.features(imgs)), 1)
        feats.append(h.cpu().numpy())
        print(f"  embedded {min(i + batch_size, len(paths))}/{len(paths)}")
    return np.concatenate(feats, axis=0)


def main():
    if not os.path.exists(MANIFEST):
        raise SystemExit("SCIN manifest not found — run `python prepare_scin_data.py` first.")
    df = pd.read_csv(MANIFEST)
    pos = df[df.label == "actinic"]
    neg = df[df.label == "other"]
    print(f"positives (Actinic Keratosis): {len(pos)}   negatives (other SCIN conditions): {len(neg)}")

    data = pd.concat([pos.assign(y=1), neg.assign(y=0)], ignore_index=True)
    data = data[data["path"].apply(lambda p: os.path.exists(os.path.join(ROOT, p)))]
    device = pick_device()
    print(f"device: {device}")
    print("Extracting frozen EfficientNet-B0 embeddings...")
    X = embed_images(data["path"].tolist(), device)
    y = data["y"].values
    fst = data["fitzpatrick_skin_type"].fillna("UNKNOWN").values

    print(f"\nTotal samples: {len(y)}  (positives: {y.sum()}, negatives: {(y == 0).sum()})")

    n_splits = min(5, int(y.sum()))  # can't have more folds than positive examples
    skf = StratifiedKFold(n_splits=n_splits, shuffle=True, random_state=42)
    fold_metrics = []
    for fold, (tr_idx, te_idx) in enumerate(skf.split(X, y)):
        clf = LogisticRegression(max_iter=2000, class_weight="balanced", C=0.5)
        clf.fit(X[tr_idx], y[tr_idx])
        prob = clf.predict_proba(X[te_idx])[:, 1]
        pred = (prob >= 0.5).astype(int)
        m = {"fold": fold, "n_test": len(te_idx), "n_pos_test": int(y[te_idx].sum())}
        if len(set(y[te_idx])) > 1:
            m["roc_auc"] = round(float(roc_auc_score(y[te_idx], prob)), 4)
        m["f1"] = round(float(f1_score(y[te_idx], pred, zero_division=0)), 4)
        m["precision"] = round(float(precision_score(y[te_idx], pred, zero_division=0)), 4)
        m["recall"] = round(float(recall_score(y[te_idx], pred, zero_division=0)), 4)
        fold_metrics.append(m)
        print(f"  fold {fold}: {m}")

    agg = {k: round(float(np.mean([m[k] for m in fold_metrics if k in m])), 4)
          for k in ("roc_auc", "f1", "precision", "recall")}
    agg_std = {k: round(float(np.std([m[k] for m in fold_metrics if k in m])), 4)
              for k in ("roc_auc", "f1", "precision", "recall")}
    print(f"\n{n_splits}-fold cross-validated metrics (mean ± std):")
    for k in agg:
        print(f"  {k}: {agg[k]} ± {agg_std[k]}")

    # final model fit on ALL data for deployment (no held-out set left — honesty is in the CV numbers above)
    final_clf = LogisticRegression(max_iter=2000, class_weight="balanced", C=0.5)
    final_clf.fit(X, y)

    # Fitzpatrick-stratified check using the CV-averaged out-of-fold predictions
    oof_prob = np.zeros(len(y))
    for tr_idx, te_idx in StratifiedKFold(n_splits=n_splits, shuffle=True, random_state=42).split(X, y):
        clf = LogisticRegression(max_iter=2000, class_weight="balanced", C=0.5).fit(X[tr_idx], y[tr_idx])
        oof_prob[te_idx] = clf.predict_proba(X[te_idx])[:, 1]
    # The default 0.5 threshold misses most positives at this sample size (see fold metrics above).
    # For a screening flag, missing a real case is worse than a false alarm, so pick the threshold
    # that maximizes recall while keeping precision >= 0.2 (not pure noise), from out-of-fold predictions only.
    prec, rec, thr = precision_recall_curve(y, oof_prob)
    candidates = [(t, p, r) for p, r, t in zip(prec[:-1], rec[:-1], thr) if p >= 0.2]
    if candidates:
        best_thr, best_p, best_r = max(candidates, key=lambda c: c[2])
    else:
        best_thr, best_p, best_r = 0.5, None, None
    oof_pred_tuned = (oof_prob >= best_thr).astype(int)
    tuned_metrics = {
        "threshold": round(float(best_thr), 3),
        "recall": round(float(recall_score(y, oof_pred_tuned, zero_division=0)), 4),
        "precision": round(float(precision_score(y, oof_pred_tuned, zero_division=0)), 4),
        "f1": round(float(f1_score(y, oof_pred_tuned, zero_division=0)), 4),
    }
    print(f"\nTuned decision threshold (out-of-fold, precision>=0.2, max recall): {tuned_metrics}")

    by_fst = {}
    for f in sorted(set(fst)):
        mask = fst == f
        if mask.sum() >= 5 and len(set(y[mask])) > 1:
            by_fst[f] = {"n": int(mask.sum()), "n_pos": int(y[mask].sum()),
                        "roc_auc": round(float(roc_auc_score(y[mask], oof_prob[mask])), 4)}
        elif mask.sum() >= 5:
            by_fst[f] = {"n": int(mask.sum()), "n_pos": int(y[mask].sum()), "roc_auc": None,
                        "note": "only one class present in this skin-type group, AUC undefined"}

    print("\nFitzpatrick-skin-type-stratified out-of-fold AUC (small-N groups omitted):")
    print(json.dumps(by_fst, indent=2))

    os.makedirs(MODELS_DIR, exist_ok=True)
    path = os.path.join(MODELS_DIR, "sun_damage_flag.pt")
    torch.save({
        "sklearn_coef": final_clf.coef_, "sklearn_intercept": final_clf.intercept_,
        "decision_threshold": best_thr,
        "backbone": "efficientnet_b0 (ImageNet pretrained, frozen)",
        "meta": {
            "dataset": "SCIN (Google), Actinic Keratosis vs other conditions",
            "n_positive": int(y.sum()), "n_negative": int((y == 0).sum()), "cv_folds": n_splits,
            "cv_metrics_mean": agg, "cv_metrics_std": agg_std, "by_fitzpatrick_skin_type": by_fst,
            "decision_threshold": tuned_metrics,
            "limitation": f"Trained on only {int(y.sum())} positive examples. This is a coarse presence flag, "
                          "not a calibrated severity score — treat predictions as a weak signal, not "
                          "a standalone diagnosis.",
        },
    }, path)
    print(f"\nsaved {path}")


if __name__ == "__main__":
    main()

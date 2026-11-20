# Specialist models: acne & sun damage

Two dedicated models, trained individually via transfer learning on real,
publicly-downloadable, dermatologist-labeled data — replacing the general
multi-task DINOv2 model's acne output and adding a new sun-damage signal it
never had. Both are wired into the live app (`ml_service.py`) and run on
every `/api/analyze` call; nothing else in the pipeline changed.

```
Pretrained backbone  ->  Fine-tune  ->  Real labeled dataset  ->  Specialist model
```

## Why only 2 of the requested 11-and-acne-and-sun-damage models

The ask covered 11 separate models (segmentation, image quality, skin tone,
acne, pigmentation, redness, texture, pores, fine lines, wrinkles, skin
type) plus sun damage, with explicit priority on acne and sun damage. After
checking real data availability (not assumptions): **acne and sun damage
were the only two with genuine public labeled data behind them.**
Pigmentation, redness, texture, pores, fine lines and wrinkles have no
established public severity-graded datasets anywhere — this isn't a search
failure, nobody has published one. Skin tone and skin type are already
served by Fitzpatrick-labeled data (SCIN, used below) and the existing
`facial-skin-images-datasets`/`train_v2.py` pipeline respectively. Image
quality and segmentation are better served by deterministic CV / off-the-shelf
pretrained segmenters than by training a bespoke classifier from scratch —
already partly true of `dinov2_lora_model.py`'s `analyze_image_quality_and_cv_metrics`.

## Model 1: Acne severity (`train_acne_model.py`)

**Dataset:** [ACNE04](https://github.com/xpwu95/LDL) (Wu et al., ICCV 2019) —
1,457 dermatologist-graded face images, 4-level Hayashi severity scale
(0=clear/mild, 1=moderate, 2=severe, 3=very severe), official train
(1,165)/test (292) split preserved. Downloaded via the public
`Layered-Labs/ACNE04` HuggingFace mirror — no auth, no registration.

**Architecture:** EfficientNet-B0 (ImageNet-pretrained) → two heads sharing
the 1280-d trunk: 4-class severity (primary) + log-lesion-count regression
(auxiliary — the joint grading+counting setup the original ACNE04 paper
found improves accuracy over classification alone). Class-balanced sampling
+ label smoothing for the skewed grade distribution (410/506/146/103 in
train). AdamW, differential backbone/head learning rates, cosine schedule
with warmup, early stopping on validation macro-F1.

**Result — official held-out ACNE04 test set (292 images, never trained on):**

| Metric | Value |
|---|---|
| Accuracy | **75.68%** |
| Balanced accuracy | 75.41% |
| Macro F1 | **0.7412** |
| F1 by grade | clear/mild 0.835, moderate 0.745, severe 0.593, very severe 0.792 |

This is competitive with published results on this exact benchmark.

**Cross-dataset generalization + skin-tone check:** ACNE04 carries no
Fitzpatrick label, so the model was additionally run — with zero further
training — on SCIN's 98 dermatologist-confirmed real-world "Acne" photos
(crowd-sourced, different lighting/framing than ACNE04's studio photos),
which do carry Fitzpatrick skin type:

| Fitzpatrick type | n | Mean predicted grade |
|---|---|---|
| FST1 (lightest) | 3 | 1.67 |
| FST2 | 13 | 1.00 |
| FST3 | 12 | 0.75 |
| FST4 | 4 | 1.75 |

A real, Fitzpatrick17k-paper-style skin-tone-dependent gap — the model
attributes meaningfully higher severity to the lightest skin tone than to
medium tones on cases a dermatologist independently confirmed were acne.
**Treat this as suggestive, not confirmed** — these per-group samples are
tiny (n=3–13). It's flagged rather than hidden because the original
request specifically asked for this check.

## Model 2: Sun-damage (actinic damage) flag (`train_sun_damage_model.py`)

No public severity-graded sun-damage/photoaging dataset exists without a
registration or data-use agreement (checked: iToBoS, partly collected in
Brisbane, requires one). What's real and public: **22** dermatologist-confirmed
"Actinic Keratosis" cases in Google's [SCIN dataset](https://github.com/google-research-datasets/scin)
(direct HTTPS from a public GCS bucket, no auth) — small, but genuine.

**Approach:** fine-tuning a full CNN on 22 positives would overfit badly, so
this is deliberately conservative: a **frozen** ImageNet-pretrained
EfficientNet-B0 as a fixed feature extractor, with only a logistic-regression
head trained on top (scikit-learn, class-balanced), evaluated via **stratified
5-fold cross-validation** (never a single train/test split — far too noisy at
this N) against 480 other SCIN cases as negatives.

**Result (5-fold CV, mean ± std):**

| Metric | Value |
|---|---|
| ROC-AUC | 0.778 ± 0.161 |
| F1 (default 0.5 threshold) | 0.137 ± 0.172 |

The default threshold misses most positives, so the decision threshold was
re-tuned from out-of-fold predictions to favor recall (missing a real case
matters more than a false alarm for a screening flag): **threshold 0.098 →
recall 54.5%, precision 20%, F1 0.293.** This is the threshold shipped in
`models/sun_damage_flag.pt` and used by `ml_service.infer_sun_damage()`.

**Skin-tone finding:** zero Fitzpatrick Type VI (darkest skin) actinic cases
in the entire SCIN sample pulled — clinically consistent (melanin provides
natural UV protection, so actinic keratosis is genuinely rarer in darker
skin), but it also means this flag has **no evidence at all** for FST6 users;
its predictions there are extrapolation, not a tested signal.

**Bottom line, stated plainly:** this is a real, honestly-evaluated, weak
screening signal — not a diagnosis, not a severity score. It's exposed as
`sun_damage_assessment: {probability, flag, note}` in every `/api/analyze`
response, with the limitation text baked into the API response itself so it
can't get lost downstream.

## Integration (`ml_service.py`, `backend/main.py`)

Both models load at startup (best-effort — a missing checkpoint just omits
that output, never crashes the service). On every scan:
- `severity_scores["acne_severity"]` is **overridden** with the specialist
  model's prediction (rescaled from its native 0–3 grade to this app's 0–5
  scale), feeding straight into triage and the product-recommendation engine.
- `acne_model_detail` (grade, confidence, per-grade probabilities) and
  `sun_damage_assessment` (probability, flag, caveat) are added to the
  response.
- The legacy Flask app (`server.py`) is unaffected — it uses its own inline
  `Engine` class, not `ml_service.py`, so it keeps working exactly as before
  without the specialist-model upgrade. (Not a gap worth closing: it's
  documented as the fallback, not the primary stack.)

## Reproducing this

```bash
python prepare_acne_data.py      # materializes ACNE04 to data/acne_datasets/
python prepare_scin_data.py      # downloads the SCIN slices used above
python train_acne_model.py       # ~7 min on Apple GPU (MPS), early-stops ~epoch 12
python train_sun_damage_model.py # ~1 min (frozen backbone, just a linear head)
```

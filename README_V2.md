# Clarity AI Face Engine: v2 upgrade

## What changed and why

| Area | Before (v1) | Now (v2) |
|---|---|---|
| Evaluation | Test faces also appeared in training (301 of 372 test faces). Reported 72.6% | Split by face. 172 test faces never seen in training. v1 scores **35.6%** on them |
| Severity scores | Trained on numbers hard-coded per skin type | Trained on the **200 real dermatologist 0–5 grades** in the xlsx sheets (18 attributes), with masked loss |
| Class balance | None ("combination" F1 0.58 even with leakage) | Balanced sampling and label smoothing |
| Training | 3 epochs, fixed LR, LoRA on query/value | 15 epochs with early stopping, warm-up and cosine LR, LoRA on q/k/v/out, CLS+patch pooling, flip TTA |
| Report | "96.8%", "0.00% FN", "97.2%", "HIPAA-ready" were typed-in constants | Every number is measured on held-out faces |
| Data storage | None | Local SQLite: patients, scans and images, clinician labels, model versions, audit log |
| Learning | None | Instant memory from each verified scan, plus automatic gated retraining |

## Run it (on your Mac, inside your `.venv`)

```bash
cd ~/Desktop/AI-Facial-Skin-Engine
source .venv/bin/activate

python data_prep.py            # 1. leak-free split → data/manifest.csv  (seconds)
python train_v2.py --quick     # 2. 3-minute smoke test (optional)
python train_v2.py             # 3. full training, ~45–70 min on Apple GPU (MPS)
python server.py               # 4. http://localhost:5095
```

`train_v2.py` prints validation scores every epoch and final test scores on never-seen faces. It saves `models/clarity_v2.N.pt` (about 5 MB), registers it in the database, and rewrites `hospital_deliverables_report.json` with the measured numbers. The server picks up the new champion automatically.

No new packages are needed. Everything uses what's already in your `.venv` (torch, transformers, peft, flask, pandas, openpyxl, scikit-learn).

## The database (`data/clarity.db`)

| Table | Contents |
|---|---|
| `patients` | name, MRN, DOB, sex, contact, notes, **research consent** |
| `scans` | every analysis: image (saved in `data/scans/<patient>/`), skin type, probabilities, 18 severities, score, triage, CV metrics, routine, embedding, model version |
| `annotations` | clinician-verified skin type and severity grades for a scan |
| `model_versions` | every trained model and which one is the champion |
| `training_runs` | each retrain: data used, champion vs challenger metrics, promoted or not, and why |
| `audit_log` | who did what and when |

You can open it with any SQLite viewer (e.g. DB Browser for SQLite). To back up, copy the whole `data/` folder.

## How the model learns from each scan

1. **Every scan** is stored, with its embedding.
2. **A clinician verifies it.** After an analysis, a *Clinician verification* panel appears under the results. Confirm or correct the skin type and optionally grade severities.
3. **Instant learning** (`memory.py`). That verified scan immediately influences predictions for similar-looking faces. The weight depends on similarity and is capped at 50%, so the network always has a say. Each result shows how much memory contributed.
4. **Automatic retraining** (`continual_learning.py`). After **50** new verified scans (set `CLARITY_RETRAIN_MIN_NEW`), the server starts a background fine-tune on verified scans plus a replay sample of the original data, so the model doesn't forget. The new model replaces the old one only if:
   - it is no worse on the fixed test faces (macro-F1 within 0.01),
   - no skin type's recall drops by more than 10 points,
   - severity error doesn't get worse, and
   - it improves on held-out patients' verified scans.

   Otherwise it's kept as a candidate and the reason is logged. You can also press **Retrain now** on the Hospital Deliverables tab, or run:
   ```bash
   python continual_learning.py status
   python continual_learning.py retrain --force
   ```

**Why only verified scans?** Training on the model's own unverified guesses reinforces its mistakes. `--include-unverified` exists for experiments (≥ 95% confidence only) but is off by default.

**Consent.** Only patients with *Research consent* ticked are used for learning. Anonymous scans (no patient record, e.g. staff test photos) are also used unless you set `CLARITY_LEARN_ANONYMOUS=0`. Deleting a patient removes their scans and images.

## New API endpoints

| Method | Path | Purpose |
|---|---|---|
| POST | `/api/analyze` | now accepts `patient_id`; returns `scan_id`, `model_version`, `learning` |
| GET/POST | `/api/patients` | list (`?q=` search) / create |
| GET/PATCH/DELETE | `/api/patients/<id>` | detail with scan history / update / erase |
| GET | `/api/scans`, `/api/scans/<id>`, `/api/scans/<id>/image` | scan records |
| POST | `/api/scans/<id>/verify` | clinician label, which feeds learning |
| GET | `/api/learning/status` | active model, memory size, retrain progress, model versions |
| POST | `/api/learning/retrain` | start a retrain now |
| GET | `/api/dashboard/stats` | counts for the dashboard |

## What to expect, and what will move accuracy most

- The skin-type labels in this public dataset are noisy. Dry, normal and oily look very similar in many photos, so the ceiling on unseen faces is moderate. Expect a clear gain over v1's 35.6% on unseen faces, not 95%.
- **The biggest lever is your own verified data.** A few hundred clinician-verified scans from your clinic's camera and lighting will help more than any tuning. The verification panel and retraining loop exist for exactly that.
- Severity MAE is measured on only 15 labelled test images, so treat it as indicative until more graded scans accumulate.
- This is a research prototype. Before hospital use it needs clinical validation, and in India, CDSCO medical-device software classification, DPDP Act 2023 compliance, access control/login, and encrypted backups.

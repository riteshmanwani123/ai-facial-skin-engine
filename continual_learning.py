"""
continual_learning.py — The model keeps learning from the scans it sees, safely.

Two layers of learning:
  1. INSTANT  (memory.py, inside server.py): every clinician-verified scan changes the
     predictions for similar faces immediately.
  2. PERIODIC (this file): once enough new verified scans exist (default 50), the champion
     model is fine-tuned on   verified user scans  +  a replay sample of the original
     dataset (prevents forgetting). The new "challenger" only replaces the champion if it
     is at least as good on the fixed, never-trained-on test faces AND on held-out user
     scans. Every run is logged in the DB (training_runs).

Why verified labels only (by default)?  Training on the model's own unverified guesses
reinforces its mistakes — unacceptable for clinical use. `--include-unverified` exists
for experimentation (uses only >= 0.95-confidence predictions) but is off by default.

Only patients with consent_research = 1 are used.

CLI
    python continual_learning.py status
    python continual_learning.py retrain            # respects --min-new threshold
    python continual_learning.py retrain --force
    python continual_learning.py reembed            # recompute stored embeddings with champion
"""
import os
import sys
import json
import time
import zlib
import argparse
import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)
import database as db  # noqa: E402

MANIFEST = os.path.join(ROOT, "data", "manifest.csv")
LOCK_FILE = os.path.join(db.DATA_DIR, "retrain.lock")


def analysis_image_path(scan_id, abs_path):
    """Scans taken since eye-covering was introduced are stored with the eyes blacked out, but the live
    pipeline analyses a copy with those areas filled in from the surrounding skin (privacy.neutralize).
    Rebuild that same copy for training and re-embedding, so the model learns from — and is compared
    against — exactly what it sees at inference time. Older scans (no recorded eye regions) are used as-is."""
    polys = ((db.get_view_redaction(scan_id, "front") or {}).get("polygons")) if scan_id else None
    if not polys:
        return abs_path
    from PIL import Image
    import privacy
    out_dir = os.path.join(db.DATA_DIR, "cache", "analysis")
    os.makedirs(out_dir, exist_ok=True)
    out = os.path.join(out_dir, f"{scan_id}.jpg")
    if not os.path.exists(out):
        privacy.neutralize(Image.open(abs_path).convert("RGB"), polys).save(out, format="JPEG", quality=95)
    return out


def samples_to_df(samples, split):
    from model_v2 import SEVERITY_ATTRIBUTES
    rows = []
    for s in samples:
        r = {"path": analysis_image_path(s["scan_id"], s["abs_image_path"]), "label": s.get("verified_skin_type"), "split": split,
             "group": s.get("patient_id") or s["scan_id"], "scan_id": s["scan_id"]}
        for a in SEVERITY_ATTRIBUTES:
            r["sev_" + a] = float(s["severity"][a]) if a in (s.get("severity") or {}) else np.nan
        r["has_severity"] = any(not np.isnan(r["sev_" + a]) for a in SEVERITY_ATTRIBUTES)
        rows.append(r)
    return pd.DataFrame(rows)


def user_split(group_id, val_pct=20):
    """Deterministic patient-level split so a patient's scans never straddle train/val."""
    return "user_val" if zlib.crc32(str(group_id).encode()) % 100 < val_pct else "user_train"


def status():
    champ = db.get_champion()
    st = db.dashboard_stats()
    last = db.last_training_run()
    return {"champion": champ and {k: champ[k] for k in ("version", "created_at", "path")},
            "champion_test_metrics": champ and (champ.get("metrics") or {}).get("test"),
            "verified_scans": st["verified_scans"],
            "verified_since_last_training": st["verified_since_last_training"],
            "last_run": last, "retrain_running": os.path.exists(LOCK_FILE)}


def reembed(model=None, version=None, log=print):
    """Recompute every stored scan embedding with the current champion (after promotion)."""
    from PIL import Image
    from model_v2 import load_checkpoint, predict
    if model is None:
        champ = db.get_champion()
        if not champ or not champ["version"].startswith("2."):
            log("no v2 champion; nothing to re-embed"); return 0
        model, _ = load_checkpoint(os.path.join(ROOT, champ["path"]))
        version = champ["version"]
    with db.connect() as c:
        rows = c.execute("SELECT id, image_path FROM scans").fetchall()
    n = 0
    for i in range(0, len(rows), 16):
        batch = rows[i:i + 16]
        imgs, ids = [], []
        for r in batch:
            p = os.path.join(db.DATA_DIR, r["image_path"])
            if os.path.exists(p):
                imgs.append(Image.open(analysis_image_path(r["id"], p)).convert("RGB")); ids.append(r["id"])
        if not imgs:
            continue
        _, _, emb = predict(model, imgs)
        for sid, e in zip(ids, emb.numpy()):
            db.update_scan_embedding(sid, e, version); n += 1
    log(f"re-embedded {n} scans with model {version}")
    return n


def retrain(min_new=50, force=False, epochs=4, replay=1500, user_oversample=3, include_unverified=False,
            log=print):
    import torch
    from torch.utils.data import DataLoader
    from model_v2 import load_checkpoint, save_checkpoint, pick_device, transform_eval
    from train_v2 import fit, evaluate, SkinDatasetV2, next_version, MODELS_DIR

    champ = db.get_champion()
    if not champ or not champ["version"].startswith("2."):
        return {"status": "skipped", "reason": "No v2 champion yet — run `python train_v2.py` first."}
    st = db.dashboard_stats()
    if not force and st["verified_since_last_training"] < min_new:
        return {"status": "skipped", "reason": f"{st['verified_since_last_training']} new verified scans "
                                               f"(< {min_new}). Use --force to train anyway."}
    if os.path.exists(LOCK_FILE) and time.time() - os.path.getmtime(LOCK_FILE) < 6 * 3600:
        return {"status": "skipped", "reason": "another retrain is running"}
    open(LOCK_FILE, "w").write(str(os.getpid()))
    run_id = None
    try:
        samples = [s for s in db.verified_samples(consent_only=True) if os.path.exists(s["abs_image_path"])]
        if include_unverified:
            samples += _confident_unverified(threshold=0.95)
        udf = samples_to_df(samples, "user")
        if len(udf):
            udf["split"] = udf.group.map(user_split)
        u_tr = udf[udf.split == "user_train"] if len(udf) else udf
        u_va = udf[udf.split == "user_val"] if len(udf) else udf

        base = pd.read_csv(MANIFEST)
        rng = np.random.RandomState(int(time.time()) % 2**31)
        b_tr = base[base.split == "train"]
        b_tr = pd.concat([b_tr[b_tr.has_severity],
                          b_tr[~b_tr.has_severity].sample(min(replay, int((~b_tr.has_severity).sum())), random_state=rng)])
        train_df = pd.concat([b_tr] + [u_tr] * user_oversample, ignore_index=True)
        val_df = pd.concat([base[base.split == "val"], u_va], ignore_index=True)
        test_df = base[base.split == "test"]
        run_id = db.start_training_run(champ["version"], len(train_df), len(u_tr))
        log(f"retrain: {len(u_tr)} user train scans (x{user_oversample}), {len(u_va)} user val, {len(b_tr)} replay")

        device = pick_device()
        champion, _ = load_checkpoint(os.path.join(ROOT, champ["path"]), device)
        challenger, _ = load_checkpoint(os.path.join(ROOT, champ["path"]), device)
        fit(challenger, train_df, val_df, device, epochs=epochs, lr_lora=5e-5, lr_head=3e-4, patience=2,
            num_workers=0, log=log)

        def ev(m, df):
            if len(df) == 0:
                return {}
            return evaluate(m, DataLoader(SkinDatasetV2(df, transform_eval), batch_size=32), device, tta=True)
        res = {"champion": {"test": ev(champion, test_df), "user_val": ev(champion, u_va)},
               "challenger": {"test": ev(challenger, test_df), "user_val": ev(challenger, u_va)}}
        promote, reason = decide(res)
        version = next_version()
        path = os.path.join(MODELS_DIR, f"clarity_v2.{version.split('.')[1]}.pt")
        save_checkpoint(challenger, path, {"version": version, "parent": champ["version"], "results": res})
        db.register_model(version, os.path.relpath(path, ROOT),
                          {"test": res["challenger"]["test"], "user_val": res["challenger"]["user_val"]},
                          parent_version=champ["version"], notes=f"continual retrain: {reason}",
                          make_champion=promote)
        db.finish_training_run(run_id, version, res, promote, reason)
        log(f"challenger {version}: {'PROMOTED' if promote else 'kept as candidate'} — {reason}")
        if promote:
            reembed(challenger, version, log)
        return {"status": "done", "new_version": version, "promoted": promote, "reason": reason, "results": res}
    except Exception as e:
        if run_id:
            db.finish_training_run(run_id, None, {"error": str(e)}, False, "failed", status="failed")
        raise
    finally:
        try:
            os.remove(LOCK_FILE)
        except OSError:
            pass


def decide(res, tol_f1=0.01, max_recall_drop=0.10, tol_mae=0.10):
    ch, cc = res["champion"], res["challenger"]
    t0, t1 = ch["test"], cc["test"]
    if t1.get("macro_f1_score", 0) < t0.get("macro_f1_score", 0) - tol_f1:
        return False, f"test macro-F1 dropped {t0['macro_f1_score']:.3f} -> {t1['macro_f1_score']:.3f}"
    for cls, r0 in t0.get("per_class_recall", {}).items():
        if t1["per_class_recall"][cls] < r0 - max_recall_drop:
            return False, f"'{cls}' recall dropped {r0:.2f} -> {t1['per_class_recall'][cls]:.2f}"
    if "severity_mae" in t0 and t1.get("severity_mae", 9) > t0["severity_mae"] + tol_mae:
        return False, f"severity MAE worsened {t0['severity_mae']:.2f} -> {t1['severity_mae']:.2f}"
    u0, u1 = ch["user_val"], cc["user_val"]
    if u0.get("n", 0) >= 10 and u1.get("macro_f1_score", 0) < u0.get("macro_f1_score", 0):
        return False, f"no gain on held-out user scans ({u0['macro_f1_score']:.3f} -> {u1['macro_f1_score']:.3f})"
    return True, (f"test F1 {t0.get('macro_f1_score', 0):.3f} -> {t1.get('macro_f1_score', 0):.3f}; "
                  f"user-val F1 {u0.get('macro_f1_score', '-')} -> {u1.get('macro_f1_score', '-')}")


def _confident_unverified(threshold=0.95):
    """Experimental: high-confidence predictions on unverified scans from consenting patients."""
    out = []
    with db.connect() as c:
        for r in c.execute("SELECT s.id, s.patient_id, s.image_path, s.skin_type_pred, s.skin_type_conf FROM scans s "
                           "JOIN patients p ON p.id = s.patient_id WHERE p.consent_research = 1 AND s.skin_type_conf >= ? "
                           "AND NOT EXISTS (SELECT 1 FROM annotations a WHERE a.scan_id = s.id)", (threshold,)):
            out.append({"scan_id": r["id"], "patient_id": r["patient_id"], "verified_skin_type": r["skin_type_pred"],
                        "severity": {}, "abs_image_path": os.path.join(db.DATA_DIR, r["image_path"])})
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["status", "retrain", "reembed"])
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--min-new", type=int, default=int(os.environ.get("CLARITY_RETRAIN_MIN_NEW", 50)))
    ap.add_argument("--epochs", type=int, default=4)
    ap.add_argument("--include-unverified", action="store_true")
    a = ap.parse_args()
    if a.cmd == "status":
        print(json.dumps(status(), indent=2, default=str))
    elif a.cmd == "reembed":
        reembed()
    else:
        print(json.dumps(retrain(a.min_new, a.force, a.epochs, include_unverified=a.include_unverified),
                         indent=2, default=str))

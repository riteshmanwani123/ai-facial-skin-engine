"""
prepare_scin_data.py — downloads the slices of Google's SCIN dataset this
project needs, directly from the public GCS bucket over HTTPS (no auth,
no registration: https://github.com/google-research-datasets/scin).

Produces:
  data/scin/scin_cases.csv, data/scin/scin_labels.csv   — full metadata (small)
  data/scin/images/acne/...        — dermatologist-confirmed Acne cases,
                                      used as an independent real-world test
                                      set for the ACNE04-trained model (SCIN
                                      has no severity grade, only presence)
  data/scin/images/actinic/...     — dermatologist-confirmed Actinic Keratosis
                                      cases (the sun-damage positive class)
  data/scin/images/other/...       — a stratified-by-Fitzpatrick-type sample
                                      of everything else, used as negatives
                                      for the sun-damage classifier and for
                                      Fitzpatrick-stratified evaluation
  data/scin/scin_manifest.csv      — path, case_id, label, fitzpatrick_skin_type
"""
import os
import ast
import random
import urllib.request
import pandas as pd

ROOT = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.join(ROOT, "data", "scin")
IMG_DIR = os.path.join(OUT_DIR, "images")
BUCKET = "https://storage.googleapis.com/dx-scin-public-data/"
SEED = 42


def download(url, path):
    if os.path.exists(path):
        return True
    os.makedirs(os.path.dirname(path), exist_ok=True)
    try:
        urllib.request.urlretrieve(url, path)
        return True
    except Exception as e:
        print(f"  FAILED {url}: {e}")
        return False


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    cases_path = os.path.join(OUT_DIR, "scin_cases.csv")
    labels_path = os.path.join(OUT_DIR, "scin_labels.csv")
    download(BUCKET + "dataset/scin_cases.csv", cases_path)
    download(BUCKET + "dataset/scin_labels.csv", labels_path)

    cases = pd.read_csv(cases_path, dtype=str)
    labels = pd.read_csv(labels_path, dtype=str)
    df = labels.merge(cases, on="case_id", how="inner")

    def has_condition(cell, name):
        if pd.isna(cell):
            return False
        try:
            return name in ast.literal_eval(cell)
        except (ValueError, SyntaxError):
            return False

    df["is_acne"] = df["dermatologist_skin_condition_on_label_name"].apply(lambda c: has_condition(c, "Acne"))
    df["is_actinic"] = df["dermatologist_skin_condition_on_label_name"].apply(lambda c: has_condition(c, "Actinic Keratosis"))
    df = df[df["image_1_path"].notna()]

    acne_rows = df[df["is_acne"]]
    actinic_rows = df[df["is_actinic"]]
    other_pool = df[~df["is_acne"] & ~df["is_actinic"]]

    rng = random.Random(SEED)
    # stratify the "other" sample roughly evenly across reported Fitzpatrick types for balanced negatives/eval
    other_pool = other_pool.copy()
    other_pool["fst_group"] = other_pool["fitzpatrick_skin_type"].fillna("UNKNOWN")
    other_sample = (other_pool.groupby("fst_group", group_keys=False)
                    .apply(lambda g: g.sample(min(len(g), 60), random_state=SEED)))

    manifest = []
    groups = [("acne", acne_rows), ("actinic", actinic_rows), ("other", other_sample)]
    for label, rows in groups:
        print(f"\nDownloading {len(rows)} '{label}' images...")
        for i, (_, r) in enumerate(rows.iterrows()):
            rel = f"images/{label}/{r['case_id']}.png"
            abs_path = os.path.join(OUT_DIR, rel)
            ok = download(BUCKET + r["image_1_path"], abs_path)
            if ok:
                manifest.append({"path": os.path.relpath(abs_path, ROOT), "case_id": r["case_id"],
                                 "label": label, "fitzpatrick_skin_type": r.get("fitzpatrick_skin_type")})
            if (i + 1) % 50 == 0:
                print(f"  {i + 1}/{len(rows)}")

    out = pd.DataFrame(manifest)
    out.to_csv(os.path.join(OUT_DIR, "scin_manifest.csv"), index=False)
    print(f"\nTotal downloaded: {len(out)}")
    print(out.groupby("label").size())
    print("\nFitzpatrick distribution by label:")
    print(out.groupby(["label", "fitzpatrick_skin_type"]).size().unstack(fill_value=0))


if __name__ == "__main__":
    main()

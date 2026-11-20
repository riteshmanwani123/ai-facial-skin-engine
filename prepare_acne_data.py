"""
prepare_acne_data.py — materializes the ACNE04 dataset (downloaded from the
Layered-Labs/ACNE04 HuggingFace mirror as parquet) to JPEG files on disk plus
a manifest CSV, for use by train_acne_model.py.

Source: https://huggingface.co/datasets/Layered-Labs/ACNE04 (public, no auth).
Original dataset: Wu et al., "Joint Acne Image Grading and Counting via Label
Distribution Learning" (ICCV 2019) — 1,457 face images, 4-level acne severity
(Hayashi grading), lesion counts and boxes. Official train/test split and a
5-fold CV split (fold_0..4) are preserved from the source parquet.
"""
import os
import io
import json
import pandas as pd
import pyarrow.parquet as pq
from PIL import Image

ROOT = os.path.dirname(os.path.abspath(__file__))
HF_DIR = os.path.join(ROOT, "data", "acne_datasets", "acne04_hf", "data")
OUT_DIR = os.path.join(ROOT, "data", "acne_datasets", "acne04_images")
MANIFEST = os.path.join(ROOT, "data", "acne_datasets", "acne04_manifest.csv")


def main():
    shards = [f for f in sorted(os.listdir(HF_DIR)) if f.endswith(".parquet")]
    dfs = [pq.read_table(os.path.join(HF_DIR, f)).to_pandas() for f in shards]
    df = pd.concat(dfs, ignore_index=True)
    os.makedirs(OUT_DIR, exist_ok=True)

    rows = []
    for i, r in df.iterrows():
        rel_path = os.path.join(r["split"], f"{r['level']}_{r['file_name']}")
        abs_path = os.path.join(OUT_DIR, rel_path)
        os.makedirs(os.path.dirname(abs_path), exist_ok=True)
        if not os.path.exists(abs_path):
            img = Image.open(io.BytesIO(r["image"]["bytes"])).convert("RGB")
            img.save(abs_path, format="JPEG", quality=92)
        rows.append({
            "path": os.path.relpath(abs_path, ROOT), "file_name": r["file_name"],
            "level": int(r["level"]), "severity": r["severity"],
            "num_lesions": int(r["num_lesions_annotated"]) if pd.notna(r["num_lesions_annotated"]) else None,
            "split": r["split"], "fold_0_split": r["fold_0_split"],
        })
        if (i + 1) % 300 == 0:
            print(f"  {i + 1}/{len(df)} images written")

    out = pd.DataFrame(rows)
    out.to_csv(MANIFEST, index=False)
    print(f"\nWrote {len(out)} images to {OUT_DIR}")
    print(f"Manifest: {MANIFEST}")
    print("\nSplit x level counts:")
    print(out.groupby(["split", "level"]).size().unstack(fill_value=0))


if __name__ == "__main__":
    main()

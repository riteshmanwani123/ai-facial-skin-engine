"""
data_prep.py  —  Build a LEAK-FREE dataset manifest for Clarity AI Face Engine.

Why: the Roboflow export stores 2-3 augmented copies of the same face
("<id>-Copy_jpg.rf.<hash>.jpg"). The original train/valid/test folders share
the same faces (301 of 372 test faces also appear in train), so accuracy
measured on that test set is inflated. This script:

  1. Collects every image from train/ valid/ test/.
  2. Groups copies by their SOURCE face id.
  3. Puts every face that the old model never trained on (i.e. not in the
     original train/) into a fixed TEST set -> fair comparison old vs new.
  4. Splits the remaining faces into TRAIN / VAL by group (no face in two splits).
  5. Attaches the real 0-5 severity labels from the two .xlsx sheets.

Output: data/manifest.csv  (one row per image)
Usage:  python data_prep.py
"""
import os, re, json, random
from collections import defaultdict
import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.abspath(__file__))
DS = os.path.join(ROOT, "facial-skin-images-datasets", "skin_type_classification_dataset")
OUT_DIR = os.path.join(ROOT, "data")
SKIN_TYPES = ["dry", "oily", "normal", "combination"]
SEED = 42
VAL_FRACTION = 0.15

# Real annotation columns (xlsx)  ->  model attribute names
SEVERITY_COLUMNS = {
    "Acne_Severity (0-5)": "acne_severity",
    "blackheads": "blackheads",
    "whiteheads": "whiteheads",
    "Open pores (0-5)": "open_pores",
    "Excessive oil (0-5)": "excessive_oil",
    "Skin irritation (0-5)": "skin_irritation",
    "Skin sensitivity (0-5)": "skin_sensitivity",
    "Redness Severity (0-5)": "redness_severity",
    "Fine line around eyes(0-5)": "fine_lines_eyes",
    "Eye puffiness(0-5)": "eye_puffiness",
    "Dark circles around eyes(0-5)": "dark_circles",
    "Wrinkes on forehead(0-5)": "wrinkles",
    "Skin elasticity(0-5)(5-not elastic at all)": "skin_elasticity",
    "Dehydration (0-5)(5 very dehydrated)": "dehydration",
    "dark spots(0-5)": "pigmentation_melasma",      # train sheet name
    "pigmentation(0-5)": "pigmentation_melasma",    # valid sheet name
    "post acne marks(0-5)": "post_acne_marks",
    "uneven skin(0-5)": "texture_roughness",
    "freckles(0-5)": "freckles",
}
SEVERITY_ATTRIBUTES = list(dict.fromkeys(SEVERITY_COLUMNS.values()))  # 18 unique, ordered


def source_id(fname: str) -> str:
    """oily_0033afe2a6242dbbf7b4-Copy_jpg.rf.<hash>.jpg -> oily_0033afe2a6242dbbf7b4"""
    return re.sub(r"(-Copy)?(_jpe?g|_png)?\.rf\..*$", "", os.path.basename(fname))


def load_severity_labels():
    labels = {}
    for x in ["skinalaysis_labeling_train1.xlsx", "skinanalysis_valid1.xlsx"]:
        p = os.path.join(DS, x)
        if not os.path.exists(p):
            continue
        for _, sheet in pd.read_excel(p, sheet_name=None).items():
            for _, r in sheet.iterrows():
                img = str(r["Image_ID"]).strip()
                if not img.lower().endswith(".jpg"):
                    img += ".jpg"
                vals = {}
                for col, attr in SEVERITY_COLUMNS.items():
                    if col in sheet.columns and pd.notna(r[col]):
                        vals[attr] = float(np.clip(float(r[col]), 0, 5))
                labels[img] = vals
    return labels


def main():
    rows = []
    for split in ["train", "valid", "test"]:
        for cls in SKIN_TYPES:
            d = os.path.join(DS, split, cls)
            if not os.path.isdir(d):
                continue
            for f in sorted(os.listdir(d)):
                if f.lower().endswith((".jpg", ".jpeg", ".png", ".webp")):
                    rows.append(dict(path=os.path.relpath(os.path.join(d, f), ROOT), file=f,
                                     orig_split=split, label=cls, group=source_id(f)))
    df = pd.DataFrame(rows)

    # sanity: one label per face
    conflicts = df.groupby("group")["label"].nunique()
    if (conflicts > 1).any():
        print(f"WARNING: {int((conflicts > 1).sum())} faces have conflicting labels; dropping them")
        df = df[~df.group.isin(conflicts[conflicts > 1].index)]

    seen_by_old_model = set(df[df.orig_split == "train"].group)
    df["split"] = np.where(df.group.isin(seen_by_old_model), "trainval", "test")

    # group-stratified train/val split
    rng = random.Random(SEED)
    tv = df[df.split == "trainval"].drop_duplicates("group")
    val_groups = set()
    for cls in SKIN_TYPES:
        g = sorted(tv[tv.label == cls].group)
        rng.shuffle(g)
        val_groups.update(g[: max(1, int(round(len(g) * VAL_FRACTION)))])
    df.loc[(df.split == "trainval") & df.group.isin(val_groups), "split"] = "val"
    df.loc[df.split == "trainval", "split"] = "train"

    # attach real severity labels
    sev = load_severity_labels()
    for a in SEVERITY_ATTRIBUTES:
        df["sev_" + a] = df.file.map(lambda f: sev.get(f, {}).get(a, np.nan))
    df["has_severity"] = df[["sev_" + a for a in SEVERITY_ATTRIBUTES]].notna().any(axis=1)

    os.makedirs(OUT_DIR, exist_ok=True)
    out = os.path.join(OUT_DIR, "manifest.csv")
    df.to_csv(out, index=False)

    # leak check
    gs = {s: set(df[df.split == s].group) for s in ["train", "val", "test"]}
    assert not (gs["train"] & gs["val"]) and not (gs["train"] & gs["test"]) and not (gs["val"] & gs["test"])

    summary = {s: {"images": int((df.split == s).sum()), "faces": len(gs[s]),
                   "per_class_faces": df[df.split == s].drop_duplicates("group").label.value_counts().to_dict(),
                   "with_severity_labels": int(df[(df.split == s)].has_severity.sum())}
               for s in ["train", "val", "test"]}
    with open(os.path.join(OUT_DIR, "split_summary.json"), "w") as f:
        json.dump(summary, f, indent=2)
    print(json.dumps(summary, indent=2))
    print(f"Manifest written: {out}  (no face appears in more than one split ✔)")


if __name__ == "__main__":
    main()

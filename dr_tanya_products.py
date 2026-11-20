"""
dr_tanya_products.py — Dr Tanya's real product catalog and the product
recommendation engine that matches it against the AI scan's severity scores.

Catalog sourced from https://drtanya.in/collections/all (fetched 2026-10-01).
The site sells 11 products; only the 6 facial skincare items are modeled
here (3 haircare items — Holy Basil Shampoo/Conditioner/Hair Serum — and the
NuBrush scalp massager are out of scope for a facial-skin analysis engine).
There is also a 7th item, the NuSonic facial exfoliator, a device rather
than a leave-on product.

Known gap, stated plainly rather than papered over: the current catalog has
no cleanser and no sunscreen. Recommendations therefore note this rather
than inventing a placeholder third-party product for those steps.
"""

# attribute -> plain-language label, for routine explanations
CONCERN_LABELS = {
    "acne_severity": "active acne", "blackheads": "blackheads", "whiteheads": "whiteheads",
    "open_pores": "visible pores", "excessive_oil": "excess oil", "skin_irritation": "skin irritation",
    "skin_sensitivity": "skin sensitivity", "redness_severity": "redness", "fine_lines_eyes": "fine lines around the eyes",
    "eye_puffiness": "eye puffiness", "dark_circles": "dark circles", "wrinkles": "wrinkles",
    "skin_elasticity": "reduced elasticity", "dehydration": "dehydration", "pigmentation_melasma": "pigmentation/melasma",
    "post_acne_marks": "post-acne marks", "texture_roughness": "uneven texture", "freckles": "freckling",
    "tanning_severity": "sun tanning",
}

CATALOG = [
    {
        "id": "advanced-retinol-serum", "name": "Advanced Retinol Serum",
        "url": "https://drtanya.in/products/advanced-retinol-serum",
        "category": "serum", "time": "pm", "step_label": "Treatment Serum (Retinol)",
        "price_inr": 3500, "list_price_inr": 5000, "in_stock": True,
        "key_ingredients": ["Encapsulated Retinol (Vitamin A)", "Niacinamide", "Panthenol", "Hyaluronic Acid"],
        "description": "Microencapsulated retinol serum for smoother, firmer, more radiant skin; supports "
                       "cellular turnover and barrier strength.",
        "usage": "Apply 1-2 pumps to clean, dry skin in the evening. Start 2-3 nights/week and build up "
                 "tolerance. Always follow with SPF the next morning.",
        "concerns": {"wrinkles": 1.0, "skin_elasticity": 0.8, "texture_roughness": 0.6,
                     "pigmentation_melasma": 0.4, "dehydration": 0.3, "post_acne_marks": 0.3},
        "contraindications": ["skin_irritation", "skin_sensitivity"],
    },
    {
        "id": "eye-lift-tightening-serum", "name": "Eye Lift Tightening Serum",
        "url": "https://drtanya.in/products/eye-lift-tightening-serum",
        "category": "serum", "time": "pm", "step_label": "Eye Serum",
        "price_inr": 2000, "list_price_inr": 2000, "in_stock": False,
        "key_ingredients": ["Squalane", "Retinyl Palmitate (Vitamin A)", "Acetyl Octapeptide-3 (Snap-8)"],
        "description": "Peptide and gentle vitamin-A eye serum to firm, tighten and brighten the eye area.",
        "usage": "Pat a small amount around the orbital bone, avoiding direct contact with the eye.",
        "concerns": {"eye_puffiness": 1.0, "dark_circles": 0.9, "fine_lines_eyes": 1.0},
        "contraindications": [],
    },
    {
        "id": "holy-basil-day-night-cream", "name": "Holy Basil Day & Night Cream",
        "url": "https://drtanya.in/products/holy-basil-day-night-cream",
        "category": "moisturizer", "time": "both", "step_label": "Moisturizer", "base_step": True,
        "price_inr": 2500, "list_price_inr": 2500, "in_stock": True,
        "key_ingredients": ["Squalane", "Jojoba Oil", "Holy Basil (Tulsi)"],
        "description": "Lightweight, non-comedogenic Ayurvedic moisturiser that hydrates without clogging "
                       "pores, with antioxidant protection from Holy Basil.",
        "usage": "Apply to clean skin morning and night (under sunscreen in the AM).",
        "concerns": {"dehydration": 0.6, "texture_roughness": 0.3, "skin_irritation": 0.3},
        "contraindications": [],
    },
    {
        "id": "holy-basil-face-serum", "name": "Holy Basil Face Serum",
        "url": "https://drtanya.in/products/holy-basil-face-serum",
        "category": "serum", "time": "both", "step_label": "Calming Serum",
        "price_inr": 2225, "list_price_inr": 2225, "in_stock": False,
        "key_ingredients": ["Jojoba Oil", "Holy Basil (Tulsi)", "Cacay Oil"],
        "description": "Calming Ayurvedic serum formulated for sensitive, reactive skin; soothes while hydrating.",
        "usage": "Apply 3-4 drops to clean skin morning and/or night, before moisturiser.",
        "concerns": {"skin_irritation": 1.0, "skin_sensitivity": 1.0, "texture_roughness": 0.3, "dehydration": 0.3},
        "contraindications": [],
    },
    {
        "id": "hyaluronic-acid-serum", "name": "Hyaluronic Acid Serum",
        "url": "https://drtanya.in/products/hyaluronic-acid-serum",
        "category": "serum", "time": "both", "step_label": "Hydrating Serum",
        "price_inr": 2800, "list_price_inr": 4000, "in_stock": True,
        "key_ingredients": ["Hydrolyzed Sodium Hyaluronate", "Sodium Hyaluronate", "Sodium Acetylated Hyaluronate"],
        "description": "Triple-weight hyaluronic acid complex for deep, multi-depth hydration and plumpness.",
        "usage": "Apply 2-3 drops to damp skin morning and/or night, before moisturiser.",
        "concerns": {"dehydration": 1.0, "skin_elasticity": 0.5, "wrinkles": 0.3},
        "contraindications": [],
    },
    {
        "id": "super-c-serum", "name": "Super C Serum",
        "url": "https://drtanya.in/products/super-c-serum",
        "category": "serum", "time": "am", "step_label": "Vitamin C Serum",
        "price_inr": 3300, "list_price_inr": 4700, "in_stock": True,
        "key_ingredients": ["Kakadu Plum Extract (Vitamin C)", "Bearberry Leaf Extract", "Vitamin C Isopalmitate"],
        "description": "Antioxidant Vitamin C day serum that brightens tone, evens pigmentation and protects "
                       "against environmental stress.",
        "usage": "Apply 3-4 drops to clean skin every morning, before moisturiser and SPF.",
        "concerns": {"pigmentation_melasma": 1.0, "tanning_severity": 0.8, "post_acne_marks": 0.7,
                     "texture_roughness": 0.3, "freckles": 0.3, "redness_severity": 0.2},
        "contraindications": [],
    },
    {
        "id": "nusonic-facial-exfoliator", "name": "NuSonic Facial Exfoliator",
        "url": "https://drtanya.in/products/nusonic-facial-exfoliator",
        "category": "device", "time": "weekly", "step_label": "Exfoliation (1-3x/week)",
        "price_inr": 2700, "list_price_inr": 2700, "in_stock": True,
        "key_ingredients": [],
        "description": "Sonic facial-cleansing device that lifts dead skin and pore buildup for a smoother, "
                       "more even complexion.",
        "usage": "1-3 times weekly with a cream cleanser, gentle outward strokes at a 90-degree angle.",
        "concerns": {"texture_roughness": 0.8, "open_pores": 0.8, "blackheads": 0.7, "whiteheads": 0.7,
                     "excessive_oil": 0.4, "acne_severity": 0.3},
        "contraindications": ["skin_irritation", "skin_sensitivity"],
    },
]

BY_ID = {p["id"]: p for p in CATALOG}

# Products the catalog does NOT cover — surfaced honestly instead of inventing a placeholder brand.
CATALOG_GAPS = {
    "cleanser": "Dr Tanya's current range has no cleanser — use a gentle, pH-balanced cleanser suited to "
               "your skin type before these steps.",
    "sunscreen": "No sunscreen in the current catalog — apply broad-spectrum SPF 30+ every morning, "
                "especially important alongside Vitamin C and Retinol.",
    "acne_treatment": "Dr Tanya's range has no dedicated anti-acne treatment (e.g. salicylic acid or "
                      "benzoyl peroxide) — for active breakouts, this is worth raising with a dermatologist.",
}


def recommend(severity_scores, concern_threshold=1.5, top_n=None):
    """Ranks every catalog product by how well it matches the AI-detected severity scores.
    Returns a list of {product, score, matched_concerns: [(attr, value, weight)]}, highest score first."""
    severity_scores = severity_scores or {}
    ranked = []
    for p in CATALOG:
        matched = []
        score = 0.0
        for attr, weight in p["concerns"].items():
            val = severity_scores.get(attr, 0)
            if val >= concern_threshold:
                matched.append((attr, val, weight))
                score += weight * (val / 5.0)
        # de-prioritise (not hard-exclude) products that fight an active irritation/sensitivity flag
        for c in p.get("contraindications", []):
            if severity_scores.get(c, 0) >= 2.5:
                score *= 0.3
        if matched or p.get("base_step"):
            ranked.append({"product": p, "score": round(score, 3), "matched_concerns": matched})
    ranked.sort(key=lambda r: -r["score"])
    return ranked[:top_n] if top_n else ranked


def _step(product, matched_concerns):
    why = ""
    if matched_concerns:
        parts = ", ".join(f"{CONCERN_LABELS.get(a, a)} ({v:.1f}/5.0)" for a, v, _ in matched_concerns)
        why = f" Recommended for {parts} detected in your AI scan."
    return {
        "step": f"Step: {product['step_label']}", "product": product["name"],
        "details": product["description"] + why, "usage": product["usage"],
        "url": product["url"], "price_inr": product["price_inr"], "list_price_inr": product["list_price_inr"],
        "in_stock": product["in_stock"], "key_ingredients": product["key_ingredients"],
    }


def build_routine(skin_type, severity_scores):
    """Returns {am_routine, pm_routine, weekly_routine, key_ingredients, notes} — same am/pm/key_ingredients
    shape the existing frontend already renders, now built entirely from Dr Tanya's real catalog."""
    severity_scores = severity_scores or {}
    sensitive = severity_scores.get("skin_irritation", 0) >= 2.5 or severity_scores.get("skin_sensitivity", 0) >= 2.5
    eye_flag = any(severity_scores.get(a, 0) >= 2.0 for a in ("eye_puffiness", "dark_circles", "fine_lines_eyes"))
    texture_flag = any(severity_scores.get(a, 0) >= 2.0 for a in ("texture_roughness", "open_pores", "blackheads", "whiteheads"))

    ranked = recommend(severity_scores, top_n=None)
    matched_by_id = {r["product"]["id"]: r["matched_concerns"] for r in ranked}

    am_ids = ["super-c-serum", "holy-basil-day-night-cream"]
    pm_ids = ["hyaluronic-acid-serum"]
    if eye_flag and BY_ID["eye-lift-tightening-serum"]:
        pm_ids.append("eye-lift-tightening-serum")
    pm_ids.append("holy-basil-face-serum" if sensitive else "advanced-retinol-serum")
    pm_ids.append("holy-basil-day-night-cream")

    am_routine = [_step(BY_ID[i], matched_by_id.get(i, [])) for i in dict.fromkeys(am_ids)]
    pm_routine = [_step(BY_ID[i], matched_by_id.get(i, [])) for i in dict.fromkeys(pm_ids)]

    weekly_routine = []
    if texture_flag and not sensitive:
        weekly_routine = [_step(BY_ID["nusonic-facial-exfoliator"], matched_by_id.get("nusonic-facial-exfoliator", []))]

    notes = [CATALOG_GAPS["cleanser"], CATALOG_GAPS["sunscreen"]]
    if severity_scores.get("acne_severity", 0) >= 3.0:
        notes.append(CATALOG_GAPS["acne_treatment"])
    if sensitive and texture_flag:
        notes.append("Exfoliation was skipped in this routine because irritation/sensitivity was also flagged — "
                     "mechanical exfoliation can aggravate a compromised skin barrier.")

    key_ingredients = sorted({ing for i in (am_ids + pm_ids) for ing in BY_ID[i]["key_ingredients"]})
    return {"am_routine": am_routine, "pm_routine": pm_routine, "weekly_routine": weekly_routine,
            "key_ingredients": key_ingredients, "notes": notes}

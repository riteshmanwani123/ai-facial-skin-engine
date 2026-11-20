"""
consultation_ai.py — drafts a starting point for the doctor's consultation form.

This never prescribes medication and never finalizes a diagnosis: it turns the
AI scan's severity scores into plain-language draft text (symptoms, a
suggested cure direction, a follow-up timeframe) that a licensed dermatologist
reviews, edits and confirms before it is saved. The "prescription" field is
intentionally left for the doctor — the draft only lists non-prescription
skincare adjuncts already used elsewhere in the app's own routine engine.
"""
from dermatology_rules import generate_skincare_routine

# attribute -> (plain-language symptom phrase, suggested clinical direction)
CONCERN_NOTES = {
    "acne_severity": ("active acne lesions", "anti-acne regimen (topical retinoid / benzoyl peroxide class, dose per clinician)"),
    "blackheads": ("comedonal blackheads", "pore-clearing exfoliation (salicylic acid / BHA)"),
    "whiteheads": ("whiteheads", "pore-clearing exfoliation (salicylic acid / BHA)"),
    "open_pores": ("enlarged/visible pores", "niacinamide-based pore-refining regimen"),
    "excessive_oil": ("excess sebum / oily texture", "oil-control cleanser and lightweight gel moisturizer"),
    "skin_irritation": ("skin irritation", "barrier-repair, fragrance-free regimen; identify irritant/allergen"),
    "skin_sensitivity": ("heightened skin sensitivity", "barrier-repair regimen; avoid active acids until stable"),
    "redness_severity": ("facial redness / erythema", "anti-inflammatory, soothing regimen (consider rosacea work-up)"),
    "fine_lines_eyes": ("fine lines around the eyes", "peptide/retinoid eye-area regimen"),
    "eye_puffiness": ("periorbital puffiness", "caffeine/peptide eye cream, sleep and salt-intake review"),
    "dark_circles": ("dark circles", "vitamin C / caffeine eye regimen; rule out allergy or anemia"),
    "wrinkles": ("wrinkling / loss of firmness", "retinoid + antioxidant anti-aging regimen"),
    "skin_elasticity": ("reduced skin elasticity", "collagen-support regimen (retinoid, peptides, sun protection)"),
    "dehydration": ("skin dehydration", "humectant + occlusive hydration regimen"),
    "pigmentation_melasma": ("hyperpigmentation / melasma", "tranexamic/azelaic acid depigmenting regimen + strict SPF"),
    "post_acne_marks": ("post-acne marks", "azelaic acid / niacinamide regimen for post-inflammatory marks"),
    "texture_roughness": ("uneven skin texture", "gentle chemical exfoliation regimen"),
    "freckles": ("freckling", "cosmetic concern only unless recently changing — monitor"),
    "tanning_severity": ("sun tanning / UV pigmentation", "brightening regimen + daily broad-spectrum SPF"),
}

# triage_status -> default days until the doctor should see the patient again
DEFAULT_FOLLOWUP_DAYS = {
    "HIGH_RISK_CLINICAL": 14,
    "TOPICAL_MELASMA_ROUTINE": 30,
    "SAFE_COSMETIC": 45,
}


def draft_report(skin_type, severity, triage_status=None, overall_score=None, previous_skin_condition=None, concern_threshold=2.0, top_n=5):
    """Returns {symptoms, diagnosis, cure, prescription, previous_condition, followup_days} — all editable drafts."""
    ranked = sorted(((a, v) for a, v in (severity or {}).items() if a in CONCERN_NOTES and v >= concern_threshold),
                    key=lambda x: -x[1])[:top_n]

    if ranked:
        symptoms = "AI-detected concerns (patient to confirm): " + "; ".join(
            f"{CONCERN_NOTES[a][0]} ({v:.1f}/5.0)" for a, v in ranked) + "."
    else:
        symptoms = "No clinically significant concerns flagged by AI screening (all tracked attributes below 2.0/5.0)."

    diagnosis = (f"AI-assisted impression: {skin_type} skin type"
                + (f", overall skin health index {overall_score}/100" if overall_score is not None else "")
                + (". " + ", ".join(CONCERN_NOTES[a][0].capitalize() for a, _ in ranked) + " noted." if ranked else ". No significant abnormality flagged.")
                + " Pending clinician confirmation.")

    cure = ("Suggested treatment directions: " + "; ".join(CONCERN_NOTES[a][1] for a, _ in ranked) + "."
           if ranked else "Maintenance skincare only; no active treatment indicated by AI screening.")

    try:
        routine = generate_skincare_routine(skin_type, severity or {})
        adjuncts = ", ".join(sorted({p["product"] for p in routine.get("am_routine", [])[:3]}))
    except Exception:
        adjuncts = ""
    prescription = ("No medication is AI-prescribed. Suggested non-prescription adjuncts: " + adjuncts +
                    ". Doctor to add any prescription medication, strength and duration.") if adjuncts else \
                   "Doctor to prescribe if clinically indicated. AI does not suggest medication."

    previous_condition = (f"Patient-reported at intake: {previous_skin_condition}"
                          if previous_skin_condition else "None reported at intake.")

    followup_days = DEFAULT_FOLLOWUP_DAYS.get(triage_status, 30)

    return {"symptoms": symptoms, "diagnosis": diagnosis, "cure": cure, "prescription": prescription,
            "previous_condition": previous_condition, "followup_days": followup_days}

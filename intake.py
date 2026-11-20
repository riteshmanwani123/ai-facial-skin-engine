"""
intake.py — the short skin questionnaire shown at sign-up (see frontend/app/signup + static/js/signup-quiz.js).

Only questions that change what a dermatologist does next are asked. Each one maps to something the doctor
already uses: concerns and duration (what to examine), breakout style (acne grading), skin type and sensitivity
(what a routine can tolerate), sun exposure (sun-damage / pigmentation risk), prior treatments and isotretinoin
(prescribing safety), known sensitivities and pregnancy / breastfeeding (ingredient safety).

The browser is not trusted: clean_intake() keeps only known keys and known values, so nothing else can be stored
through this door. The questionnaire is stored on the patient record (patients.intake_json), shown to the doctor
in the case view, and summarised into patients.previous_skin_condition so the existing consultation draft and
doctor form pick it up unchanged.
"""

OPTIONS = {
    "goals": {
        "acne": "Clear acne & breakouts",
        "dark_spots": "Fade dark spots & sun damage",
        "redness": "Calm redness & irritation",
        "texture": "Smooth texture & pores",
        "aging": "Fine lines & early ageing",
        "other": "Something else",
    },
    "skin_type": {"dry": "Dry", "normal": "Normal", "oily": "Oily", "combination": "Combination", "unsure": "Not sure"},
    "duration": {"lt3m": "Less than 3 months", "3to12m": "3 months to a year", "gt1y": "More than a year"},
    "breakout": {
        "blackheads": "Mostly blackheads and clogged pores",
        "few_pimples": "A few pimples",
        "regular": "Regular breakouts",
        "frequent_painful": "Frequent or painful, deep breakouts",
    },
    "sun": {
        "rarely": "Rarely in the sun",
        "some": "Some — under an hour a day",
        "often": "Often — 1 to 3 hours a day",
        "most_day": "Outdoors most of the day",
    },
    "tried": {
        "otc_creams": "Over-the-counter creams",
        "prescription": "Prescription creams or tablets",
        "isotretinoin": "Isotretinoin (now or in the last 6 months)",
        "procedures": "Peels, facials or laser",
        "nothing": "Nothing yet",
    },
    "sensitivities": {
        "sensitive_skin": "My skin is easily irritated",
        "retinoids": "Retinol / retinoids",
        "benzoyl_peroxide": "Benzoyl peroxide",
        "acids": "Salicylic acid / AHAs",
        "fragrance": "Fragrance",
        "none": "None that I know of",
    },
    "pregnancy": {"yes": "Pregnant or breastfeeding", "planning": "Planning a pregnancy", "no": "No", "skip": "Prefer not to say"},
}
MULTI = {"goals", "tried", "sensitivities"}
EXCLUSIVE = {"tried": "nothing", "sensitivities": "none"}   # picking this clears the others
LABELS = {
    "goals": "Goals", "skin_type": "Skin type", "duration": "Concerns for", "breakout": "Breakouts",
    "sun": "Sun exposure", "tried": "Tried before", "sensitivities": "Sensitivities", "pregnancy": "Pregnancy / breastfeeding",
}
ORDER = ["goals", "skin_type", "duration", "breakout", "sun", "tried", "sensitivities", "pregnancy"]
NOTE_MAX = 500


def clean_intake(raw):
    """Returns a sanitised intake dict (possibly empty). Unknown keys/values are dropped, never stored."""
    if not isinstance(raw, dict):
        return {}
    out = {}
    for key, allowed in OPTIONS.items():
        v = raw.get(key)
        if key in MULTI:
            if isinstance(v, (list, tuple)):
                picked = [x for x in dict.fromkeys(v) if isinstance(x, str) and x in allowed]
                excl = EXCLUSIVE.get(key)
                if excl and excl in picked:
                    picked = [excl]
                if picked:
                    out[key] = picked
        elif isinstance(v, str) and v in allowed:
            out[key] = v
    note = raw.get("note")
    if isinstance(note, str) and note.strip():
        out["note"] = note.strip()[:NOTE_MAX]
    if out:
        out["version"] = 1
    return out


def describe(intake):
    """Ordered [(label, human text)] pairs for display (doctor case view, summaries)."""
    rows = []
    for key in ORDER:
        v = (intake or {}).get(key)
        if not v:
            continue
        text = ", ".join(OPTIONS[key][x] for x in v) if isinstance(v, list) else OPTIONS[key][v]
        rows.append((LABELS[key], text))
    if (intake or {}).get("note"):
        rows.append(("Note", intake["note"]))
    return rows


def summarize(intake):
    """One readable paragraph for patients.previous_skin_condition (what the doctor form is pre-filled from)."""
    rows = describe(intake)
    return "; ".join(f"{k}: {v}" for k, v in rows)

"""
Dermatological Product Recommendation Rules Engine & Doctor Network Portal

generate_skincare_routine() is now backed by dr_tanya_products.py — Dr
Tanya's real product catalog (https://drtanya.in) — instead of the
placeholder third-party brands this file used to hard-code below.
"""
import dr_tanya_products

DOCTOR_NETWORK = [
    {
        "id": "doc-101",
        "name": "Dr. Elena Vance, MD",
        "title": "Board-Certified Dermatologist & Clinical Researcher",
        "hospital": "Stanford Health Dermatology Center",
        "experience": "16 Years",
        "specialties": ["Melasma & Dermal Pigmentation", "Acne Vulgaris & Cystic Lesions", "Laser Skin Surgery"],
        "rating": 4.9,
        "avatar": "https://images.unsplash.com/photo-1559839734-2b71ea197ec2?auto=format&fit=crop&q=80&w=300",
        "available_today": True,
        "contact_phone": "+1 (800) 555-DERM"
    },
    {
        "id": "doc-102",
        "name": "Dr. Marcus Thorne, MD, FAAD",
        "title": "Senior Clinical Dermatologist",
        "hospital": "Johns Hopkins Dermatology Clinic",
        "experience": "21 Years",
        "specialties": ["Atopic Dermatitis", "Post-Inflammatory Hyperpigmentation", "Skin Oncology"],
        "rating": 4.95,
        "avatar": "https://images.unsplash.com/photo-1622253692010-333f2da6031d?auto=format&fit=crop&q=80&w=300",
        "available_today": True,
        "contact_phone": "+1 (800) 444-SKIN"
    },
    {
        "id": "doc-103",
        "name": "Dr. Sophia Lin, MD",
        "title": "Cosmetic & Medical Dermatologist",
        "hospital": "Mayo Clinic Skin & Allergy Institute",
        "experience": "12 Years",
        "specialties": ["Refractory Melasma", "Photoaging & Sun Tanning", "Sensitive Skin Barrier"],
        "rating": 4.88,
        "avatar": "https://images.unsplash.com/photo-1594824813566-788534863836?auto=format&fit=crop&q=80&w=300",
        "available_today": True,
        "contact_phone": "+1 (800) 333-CLINIC"
    }
]

def generate_skincare_routine(skin_type, severity_scores):
    """
    Generates tailored AM & PM skincare routines from Dr Tanya's real product
    catalog (dr_tanya_products.py), ranked against the AI scan's severity scores.
    """
    return dr_tanya_products.build_routine(skin_type, severity_scores)

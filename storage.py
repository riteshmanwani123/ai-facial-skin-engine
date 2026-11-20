"""
storage.py — Image Storage layer for Clarity AI Face Engine.

Owns every scan image on disk. PostgreSQL (database.py) never holds image
bytes — only the relative path and sha256 this module returns. Kept as its
own module (not folded into database.py) so it is a clean, swappable layer:
today it writes to local disk under CLARITY_DATA_DIR; swapping to an object
store (S3-compatible) later only means changing this file.
"""
import os
import io
import hashlib

ROOT = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.environ.get("CLARITY_DATA_DIR", os.path.join(ROOT, "data"))
SCAN_DIR = os.path.join(DATA_DIR, "scans")


def ensure_dirs():
    os.makedirs(SCAN_DIR, exist_ok=True)


def save_image(pil_image, owner_id, image_id):
    """Writes a scan image as JPEG under SCAN_DIR/<owner_id>/<image_id>.jpg.
    Returns (path_relative_to_DATA_DIR, sha256_hex)."""
    ensure_dirs()
    buf = io.BytesIO()
    pil_image.convert("RGB").save(buf, format="JPEG", quality=95)
    raw = buf.getvalue()
    sha = hashlib.sha256(raw).hexdigest()
    folder = os.path.join(SCAN_DIR, owner_id or "anonymous")
    os.makedirs(folder, exist_ok=True)
    abs_path = os.path.join(folder, f"{image_id}.jpg")
    with open(abs_path, "wb") as f:
        f.write(raw)
    return os.path.relpath(abs_path, DATA_DIR), sha


def abs_path(rel_path):
    return os.path.join(DATA_DIR, rel_path)


def delete_image(rel_path):
    try:
        os.remove(abs_path(rel_path))
    except OSError:
        pass


def delete_owner_folder(owner_id):
    try:
        os.rmdir(os.path.join(SCAN_DIR, owner_id))
    except OSError:
        pass

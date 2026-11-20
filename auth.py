"""
auth.py — password hashing and session tokens for Clarity AI Face Engine.

Accounts (database.py's users table) let multiple patients and clinicians
use the app at once, each seeing only their own data, instead of the
anonymous intake-form-per-visit flow this app started with.

Sessions are a JWT (HS256) carried in an httpOnly cookie, set by
POST /api/auth/login and read by the get_current_user dependency in
backend/main.py. The signing secret is generated once and persisted to
data/.jwt_secret (outside the DB, so rotating the DB doesn't log everyone
out, and outside source control) unless CLARITY_JWT_SECRET is set explicitly.
"""
import os
import re
import jwt
import bcrypt
from datetime import datetime, timedelta, timezone

import storage

SESSION_COOKIE = "clarity_session"
TOKEN_TTL_DAYS = 30
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

_SECRET_PATH = os.path.join(storage.DATA_DIR, ".jwt_secret")


def _load_or_create_secret():
    env = os.environ.get("CLARITY_JWT_SECRET")
    if env:
        return env
    if os.path.exists(_SECRET_PATH):
        with open(_SECRET_PATH) as f:
            return f.read().strip()
    storage.ensure_dirs()
    secret = os.urandom(32).hex()
    with open(_SECRET_PATH, "w") as f:
        f.write(secret)
    return secret


SECRET = _load_or_create_secret()


def valid_email(email):
    return bool(email) and bool(EMAIL_RE.match(email.strip()))


def hash_password(password):
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password, password_hash):
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except ValueError:
        return False


def create_token(user):
    payload = {
        "sub": user["id"], "role": user["role"], "email": user["email"],
        "exp": datetime.now(timezone.utc) + timedelta(days=TOKEN_TTL_DAYS),
        "iat": datetime.now(timezone.utc),
    }
    return jwt.encode(payload, SECRET, algorithm="HS256")


def decode_token(token):
    try:
        return jwt.decode(token, SECRET, algorithms=["HS256"])
    except jwt.PyJWTError:
        return None

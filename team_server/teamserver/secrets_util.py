"""Random credentials and their stored forms. Only hashes are stored."""
from __future__ import annotations

import base64
import hashlib
import hmac
import secrets

# 20 consonants: no vowels (no words), no look-alikes for 0/1/O/I.
_CODE_ALPHABET = "BCDFGHJKLMNPQRSTVWXZ"


def new_secret() -> str:
    """256 bits, URL-safe."""
    return secrets.token_urlsafe(32)


def digest(secret: str) -> str:
    return hashlib.sha256(secret.encode()).hexdigest()


def pkce_challenge(verifier: str) -> str:
    raw = hashlib.sha256(verifier.encode()).digest()
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def same(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode(), b.encode())


def csrf_token(session_token: str) -> str:
    """Derived from the session credential: only its holder can compute it,
    and nothing extra is stored or signed."""
    return hashlib.sha256(b"csrf:" + session_token.encode()).hexdigest()


def new_user_code() -> str:
    raw = "".join(secrets.choice(_CODE_ALPHABET) for _ in range(8))
    return f"{raw[:4]}-{raw[4:]}"


def normalize_user_code(value: str) -> str:
    v = "".join(ch for ch in value.upper() if ch.isalnum())
    if len(v) != 8 or any(ch not in _CODE_ALPHABET for ch in v):
        return ""
    return f"{v[:4]}-{v[4:]}"

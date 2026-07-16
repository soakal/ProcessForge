"""Password hashing. Salted PBKDF2-HMAC-SHA256, self-describing stored format.

Never log or print a password or a derived hash anywhere in this module.
"""
from __future__ import annotations

import hashlib
import hmac
import secrets

_ALGO = "pbkdf2_sha256"
_ITERATIONS = 600_000
_SALT_BYTES = 16


def hash_password(password: str) -> str:
    """Hash `password` and return a single self-describing storable string:

    "pbkdf2_sha256$<iterations>$<salt_hex>$<hash_hex>"

    Raises ValueError if `password` is empty or whitespace-only. This is a
    library-level guard only; any minimum-length policy is enforced by the
    CLI layer.
    """
    if not password or not password.strip():
        raise ValueError("password must not be empty or whitespace-only")

    salt = secrets.token_bytes(_SALT_BYTES)
    derived = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, _ITERATIONS)
    return f"{_ALGO}${_ITERATIONS}${salt.hex()}${derived.hex()}"


def verify_password(password: str, stored: str) -> bool:
    """Verify `password` against a stored string produced by hash_password().

    Returns False (never raises) if `stored` is malformed or uses an
    unrecognized algorithm tag, so a corrupted/tampered value fails closed.
    A wrong password also returns False, never raises.
    """
    parts = stored.split("$")
    if len(parts) != 4:
        return False

    algo, iterations_str, salt_hex, hash_hex = parts
    if algo != _ALGO:
        return False

    try:
        iterations = int(iterations_str)
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(hash_hex)
    except ValueError:
        return False

    if iterations < 1:
        return False

    derived = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return hmac.compare_digest(derived, expected)

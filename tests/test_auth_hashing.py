"""Tests for auth.hashing: salted PBKDF2 password hashing/verification."""
from __future__ import annotations

import pytest

from auth.hashing import hash_password, verify_password


def test_round_trip_verifies():
    pw = "correct horse battery staple"
    stored = hash_password(pw)
    assert verify_password(pw, stored) is True


def test_wrong_password_fails():
    stored = hash_password("right")
    assert verify_password("wrong", stored) is False


def test_same_password_hashed_twice_differs_but_both_verify():
    pw = "same password twice"
    stored_a = hash_password(pw)
    stored_b = hash_password(pw)
    assert stored_a != stored_b
    assert verify_password(pw, stored_a) is True
    assert verify_password(pw, stored_b) is True


def test_hash_password_rejects_empty_string():
    with pytest.raises(ValueError):
        hash_password("")


def test_hash_password_rejects_whitespace_only():
    with pytest.raises(ValueError):
        hash_password("   ")


def test_verify_password_rejects_malformed_stored_format():
    assert verify_password("whatever", "not-a-valid-stored-format") is False


def test_verify_password_rejects_malformed_iteration_count():
    assert verify_password("whatever", "pbkdf2_sha256$notanumber$abc$def") is False


def test_verify_password_rejects_non_positive_iteration_count():
    assert verify_password("pw", "pbkdf2_sha256$0$aabb$ccdd") is False
    assert verify_password("pw", "pbkdf2_sha256$-5$aabb$ccdd") is False


def test_stored_format_locks_in_iteration_count():
    stored = hash_password("lock in the iteration count")
    _, iterations_str, _, _ = stored.split("$")
    assert int(iterations_str) == 600_000

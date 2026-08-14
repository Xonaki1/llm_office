from __future__ import annotations

import base64

import pytest
from cryptography.exceptions import InvalidTag

from core.config import get_settings
from core.crypto import (
    CryptoError,
    decrypt_secret,
    encrypt_secret,
    fingerprint,
    mask_secret,
    needs_rewrap,
    rewrap,
)

SECRET = "sk-ant-api03-abcdef1234567890"  # noqa: S105 - test fixture value


def test_roundtrip():
    blob = encrypt_secret(SECRET, aad="org-1")
    assert SECRET not in blob
    assert decrypt_secret(blob, aad="org-1") == SECRET


def test_ciphertext_is_never_reused():
    a = encrypt_secret(SECRET, aad="org-1")
    b = encrypt_secret(SECRET, aad="org-1")
    assert a != b, "a fresh DEK and nonce per call must produce different ciphertexts"


def test_another_org_cannot_decrypt():
    blob = encrypt_secret(SECRET, aad="org-1")
    with pytest.raises(InvalidTag):
        decrypt_secret(blob, aad="org-2")


def test_tampering_is_detected():
    blob = encrypt_secret(SECRET, aad="org-1")
    version, kek_version, wrapped, dek_nonce, ciphertext, kek_nonce = blob.split(".")
    flipped = bytearray(base64.b64decode(ciphertext))
    flipped[0] ^= 0x01
    tampered = ".".join(
        [
            version,
            kek_version,
            wrapped,
            dek_nonce,
            base64.b64encode(bytes(flipped)).decode(),
            kek_nonce,
        ]
    )
    with pytest.raises(InvalidTag):
        decrypt_secret(tampered, aad="org-1")


def test_empty_secret_is_rejected():
    with pytest.raises(CryptoError):
        encrypt_secret("", aad="org-1")


def test_malformed_blob_is_rejected():
    with pytest.raises(CryptoError):
        decrypt_secret("not-a-real-envelope", aad="org-1")


def test_mask_hides_the_middle():
    masked = mask_secret(SECRET)
    assert masked.startswith("sk-ant")
    assert masked.endswith("7890")
    assert "abcdef" not in masked


def test_fingerprint_is_stable_and_not_reversible():
    assert fingerprint(SECRET) == fingerprint(SECRET)
    assert fingerprint(SECRET) != fingerprint(SECRET + "x")
    assert SECRET not in fingerprint(SECRET)


class TestRotation:
    def test_rewrap_moves_a_secret_to_the_new_key(self, monkeypatch):
        blob = encrypt_secret(SECRET, aad="org-1")
        assert not needs_rewrap(blob)

        settings = get_settings()
        old_keys = dict(settings.master_keys)
        new_keys = {**old_keys, "2": base64.b64encode(b"n" * 32).decode()}
        monkeypatch.setattr(settings, "master_keys", new_keys)
        monkeypatch.setattr(settings, "master_key_version", "2")

        # The old ciphertext still decrypts, because its KEK is still configured.
        assert needs_rewrap(blob)
        assert decrypt_secret(blob, aad="org-1") == SECRET

        rotated = rewrap(blob, aad="org-1")
        assert not needs_rewrap(rotated)
        assert decrypt_secret(rotated, aad="org-1") == SECRET
        assert rotated.split(".")[1] == "2"

    def test_missing_old_key_is_an_explicit_error(self, monkeypatch):
        blob = encrypt_secret(SECRET, aad="org-1")
        settings = get_settings()
        monkeypatch.setattr(
            settings, "master_keys", {"2": base64.b64encode(b"n" * 32).decode()}
        )
        monkeypatch.setattr(settings, "master_key_version", "2")
        with pytest.raises(CryptoError, match="version"):
            decrypt_secret(blob, aad="org-1")

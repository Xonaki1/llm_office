"""Envelope encryption for user-supplied provider API keys (BYOK).

Each secret gets a fresh 256-bit data encryption key (DEK). The DEK encrypts the
secret with AES-256-GCM; the DEK itself is wrapped with a key-encryption key (KEK)
held only in the process environment (or a KMS).

Ciphertext layout — dot-separated, every field base64:

    v2.<kek_version>.<wrapped_dek>.<dek_nonce>.<ciphertext>.<kek_nonce>

The KEK version is embedded so keys can be rotated: new writes use the newest
KEK while old rows stay readable, and `rewrap` migrates them in the background.

Plaintext secrets are never written to the database, logs, or API responses.
"""

from __future__ import annotations

import base64
import hmac
import os
from dataclasses import dataclass

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from core.config import get_settings

_VERSION = "v2"
_LEGACY_VERSION = "v1"  # pre-rotation format, implicitly KEK version "1"


class CryptoError(RuntimeError):
    pass


def _b64e(raw: bytes) -> str:
    return base64.b64encode(raw).decode()


def _b64d(text: str) -> bytes:
    return base64.b64decode(text.encode())


def _kek(version: str) -> bytes:
    keys = get_settings().master_keys
    if not keys:
        raise CryptoError("MASTER_KEYS is not configured")
    if version not in keys:
        raise CryptoError(
            f"ciphertext was wrapped with KEK version {version!r}, which is not "
            f"configured; restore it before decrypting"
        )
    key = _b64d(keys[version])
    if len(key) != 32:
        raise CryptoError(f"MASTER_KEYS[{version}] must decode to exactly 32 bytes")
    return key


@dataclass(frozen=True)
class Envelope:
    version: str
    kek_version: str
    wrapped_dek: str
    dek_nonce: str
    ciphertext: str
    kek_nonce: str

    @classmethod
    def parse(cls, blob: str) -> Envelope:
        parts = blob.split(".")
        if parts and parts[0] == _LEGACY_VERSION and len(parts) == 5:
            _, wrapped_dek, dek_nonce, ciphertext, kek_nonce = parts
            return cls(_LEGACY_VERSION, "1", wrapped_dek, dek_nonce, ciphertext, kek_nonce)
        if parts and parts[0] == _VERSION and len(parts) == 6:
            _, kek_version, wrapped_dek, dek_nonce, ciphertext, kek_nonce = parts
            return cls(_VERSION, kek_version, wrapped_dek, dek_nonce, ciphertext, kek_nonce)
        raise CryptoError("malformed ciphertext")

    def render(self) -> str:
        return ".".join(
            [
                self.version,
                self.kek_version,
                self.wrapped_dek,
                self.dek_nonce,
                self.ciphertext,
                self.kek_nonce,
            ]
        )


def encrypt_secret(plaintext: str, *, aad: str) -> str:
    """Encrypt a provider API key.

    `aad` binds the ciphertext to a context — always the owning organisation id —
    so a row copied into another tenant's record fails to decrypt.
    """
    if not plaintext:
        raise CryptoError("refusing to encrypt an empty secret")
    version, key_b64 = get_settings().active_master_key
    kek = _b64d(key_b64)
    if len(kek) != 32:
        raise CryptoError(f"MASTER_KEYS[{version}] must decode to exactly 32 bytes")

    dek = os.urandom(32)
    dek_nonce = os.urandom(12)
    kek_nonce = os.urandom(12)
    aad_bytes = aad.encode()

    ciphertext = AESGCM(dek).encrypt(dek_nonce, plaintext.encode(), aad_bytes)
    wrapped_dek = AESGCM(kek).encrypt(kek_nonce, dek, aad_bytes)

    return Envelope(
        version=_VERSION,
        kek_version=version,
        wrapped_dek=_b64e(wrapped_dek),
        dek_nonce=_b64e(dek_nonce),
        ciphertext=_b64e(ciphertext),
        kek_nonce=_b64e(kek_nonce),
    ).render()


def decrypt_secret(blob: str, *, aad: str) -> str:
    env = Envelope.parse(blob)
    kek = _kek(env.kek_version)
    aad_bytes = aad.encode()
    dek = AESGCM(kek).decrypt(_b64d(env.kek_nonce), _b64d(env.wrapped_dek), aad_bytes)
    return AESGCM(dek).decrypt(_b64d(env.dek_nonce), _b64d(env.ciphertext), aad_bytes).decode()


def needs_rewrap(blob: str) -> bool:
    """True when a stored secret is under an older KEK than the active one."""
    env = Envelope.parse(blob)
    active_version, _ = get_settings().active_master_key
    return env.version != _VERSION or env.kek_version != active_version


def rewrap(blob: str, *, aad: str) -> str:
    """Re-encrypt an existing secret under the active KEK. Used by the rotation
    script; the plaintext exists only inside this call."""
    return encrypt_secret(decrypt_secret(blob, aad=aad), aad=aad)


def mask_secret(plaintext: str) -> str:
    """Display form for the UI. The real value is never returned to the client."""
    if len(plaintext) <= 12:
        return "*" * len(plaintext)
    return f"{plaintext[:6]}...{plaintext[-4:]}"


def fingerprint(plaintext: str) -> str:
    """Stable, non-reversible id for a secret, so the UI can tell two keys apart
    and the audit log can reference one without storing it."""
    import hashlib

    return hashlib.sha256(plaintext.encode()).hexdigest()[:16]


def constant_time_equals(a: str, b: str) -> bool:
    return hmac.compare_digest(a, b)


def generate_master_key() -> str:
    return _b64e(os.urandom(32))

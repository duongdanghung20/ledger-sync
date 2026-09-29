"""Refresh-token encryption at rest.

Fernet (AES-128-CBC + HMAC-SHA256 — authenticated symmetric encryption) with a
key read from ``TOKEN_ENC_KEY``. The key lives in the environment, never in the
database, so a raw DB dump alone cannot recover a stored refresh token. Tokens
are never logged.

``TOKEN_ENC_KEY`` is a Fernet key: ``python -c "from cryptography.fernet import
Fernet; print(Fernet.generate_key().decode())"``.
"""

from __future__ import annotations

from cryptography.fernet import Fernet

from app.config import env


def _fernet() -> Fernet:
    # Read at call time (not import) and don't cache: cheap to build, and this
    # stays correct if the key is rotated or overridden (e.g. in tests).
    return Fernet(env("TOKEN_ENC_KEY").encode())


def encrypt(plaintext: str) -> bytes:
    return _fernet().encrypt(plaintext.encode())


def decrypt(token: bytes) -> str:
    return _fernet().decrypt(bytes(token)).decode()

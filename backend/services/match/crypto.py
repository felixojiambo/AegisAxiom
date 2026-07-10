"""Fernet at-rest encryption for MATCH sensitive settings.

Ported verbatim from the MATCH source (backend/crypto.py) with two AEGISAXIOM-specific
adjustments:

  1. Env var renamed from ``APP_ENCRYPTION_KEY`` to ``MATCH_FERNET_KEY`` to
     avoid colliding with other AEGISAXIOM subsystems that may want their own keys
     down the road. The semantics are identical — a 32-byte urlsafe-base64
     Fernet key.

  2. Module-load behavior: log a warning when the key is unset rather than
     auto-generating a file. AEGISAXIOM runs in containers where a file-fallback
     would silently produce a new key per restart, defeating the whole point.
     ``get_encryption_key()`` raises ``RuntimeError`` lazily on first use.

Encrypted fields (at-rest in match_settings singleton):
  - n8n.shared_auth_token
  - appdirect.bearer_token
  - crm.sql.service_password
  - crm.api.api_key

Wire protocol: GET masks any encrypted value with MASKED_PASSWORD_SENTINEL.
PUT preserves existing ciphertext when the incoming value is empty or equals
the sentinel; otherwise re-encrypts plaintext. The masking/preserve logic
lives in the settings route module, not here.
"""
from __future__ import annotations

import logging
import os
from typing import Final

from cryptography.fernet import Fernet

logger = logging.getLogger(__name__)

# 12-bullet sentinel returned on GET in place of any encrypted field.
# Same character (U+2022 BULLET) used by the MATCH source.
MASKED_PASSWORD_SENTINEL: Final[str] = "•" * 12

_ENV_VAR = "MATCH_FERNET_KEY"

# One-shot warning on import if the key is not configured. Routes that never
# touch encrypted values will work fine without it; only encrypt/decrypt calls
# will raise.
if not os.environ.get(_ENV_VAR):
    logger.warning(
        "%s is not set. MATCH encrypted settings (shared_auth_token, bearer_token, "
        "sql.service_password, api.api_key) cannot be read or written until this "
        "env var is configured. Generate one with: "
        "python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'",
        _ENV_VAR,
    )


def get_encryption_key() -> bytes:
    """Return the Fernet key from the MATCH_FERNET_KEY env var.

    Raises RuntimeError if the env var is unset or empty. Called lazily by
    encrypt_value / decrypt_value so importing this module never raises.
    """
    env_key = os.environ.get(_ENV_VAR)
    if not env_key:
        raise RuntimeError(
            f"{_ENV_VAR} is not configured. Cannot encrypt or decrypt MATCH settings."
        )
    return env_key.encode() if isinstance(env_key, str) else env_key


def encrypt_value(plaintext: str) -> str:
    """Encrypt plaintext to a urlsafe-base64 ciphertext string. Empty -> empty."""
    if not plaintext:
        return ""
    return Fernet(get_encryption_key()).encrypt(plaintext.encode()).decode()


def decrypt_value(ciphertext: str) -> str:
    """Decrypt a Fernet ciphertext string. Empty -> empty.

    Raises cryptography.fernet.InvalidToken if the ciphertext is corrupt or
    was encrypted with a different key.
    """
    if not ciphertext:
        return ""
    return Fernet(get_encryption_key()).decrypt(ciphertext.encode()).decode()

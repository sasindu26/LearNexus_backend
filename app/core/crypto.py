"""
Fernet-based encryption for sensitive PII (phone numbers).
If PHONE_ENCRYPTION_KEY is not set, values are stored in plain text
with a warning — acceptable for local dev, not for production.
"""

import logging
from cryptography.fernet import Fernet
from app.core.config import settings

logger = logging.getLogger(__name__)

_fernet: Fernet | None = None


def _get_fernet() -> Fernet | None:
    global _fernet
    if _fernet is not None:
        return _fernet
    key = settings.phone_encryption_key.strip()
    if not key:
        logger.warning("PHONE_ENCRYPTION_KEY not set — phone numbers stored in plain text")
        return None
    try:
        _fernet = Fernet(key.encode())
        return _fernet
    except Exception as e:
        logger.error(f"Invalid PHONE_ENCRYPTION_KEY: {e}")
        return None


def encrypt_phone(phone: str) -> str:
    f = _get_fernet()
    if f is None:
        return phone
    return f.encrypt(phone.encode()).decode()


def decrypt_phone(ciphertext: str) -> str:
    f = _get_fernet()
    if f is None:
        return ciphertext
    try:
        return f.decrypt(ciphertext.encode()).decode()
    except Exception:
        return ciphertext   # already plain text (legacy)

"""BossMod AI — File-key wrapping for secret columns at rest.

HA-SEC-P1-05: API keys and token settings are stored in SQLite. A
chmod-600 data key next to the DB file wraps those columns so a copied
``.sqlite3`` dump (for example ``artifacts/db_backups/``) is not
plaintext. The key file is the control — disk encryption still covers
theft of the whole data directory. See ``docs/SECRETS_AT_REST.md``.

Format: ``bm1:<urlsafe-b64(nonce 16 || ciphertext || hmac-sha256 32)>``.
Keystream is SHA-256(key || nonce || counter); MAC is HMAC-SHA256 over
nonce+ciphertext (encrypt-then-MAC). Stdlib only — no new dependency.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import os
from pathlib import Path

from db.connection import database_path
from db.crud import execute, query

logger = logging.getLogger(__name__)

SECRET_PREFIX = "bm1:"
SECRET_SETTING_KEYS = frozenset({
    "telegram_bot_token",
    "local_api_token",
})

_DATA_KEY_NAME = ".bossmod_data_key"
_NONCE_LEN = 16
_MAC_LEN = 32
_BLOCK_LEN = 32
_cached_key: bytes | None = None
_cached_key_path: Path | None = None


def data_key_path() -> Path:
    """Return the data-key path beside the configured SQLite file."""
    return database_path().resolve().parent / _DATA_KEY_NAME


def is_encrypted(value: str | None) -> bool:
    """Return whether ``value`` is a wrapped secret blob."""
    return bool(value) and value.startswith(SECRET_PREFIX)


def get_or_create_data_key() -> bytes:
    """Load the 32-byte data key, creating it with mode 0600 if missing."""
    global _cached_key, _cached_key_path
    path = data_key_path()
    if _cached_key is not None and _cached_key_path == path:
        return _cached_key
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        key = path.read_bytes()
        if len(key) != 32:
            raise RuntimeError(f"Data key at {path} is not 32 bytes")
    else:
        key = os.urandom(32)
        fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            os.write(fd, key)
        finally:
            os.close(fd)
        os.chmod(path, 0o600)
        logger.info("Created secrets data key at %s", path)
    _cached_key = key
    _cached_key_path = path
    return key


def reset_data_key_cache() -> None:
    """Drop the process-local key cache (tests / DB path changes)."""
    global _cached_key, _cached_key_path
    _cached_key = None
    _cached_key_path = None


def encrypt_secret(plaintext: str | None) -> str | None:
    """Wrap a secret for storage. Empty/None stay empty/None."""
    if plaintext is None:
        return None
    if plaintext == "":
        return ""
    if is_encrypted(plaintext):
        return plaintext
    key = get_or_create_data_key()
    nonce = os.urandom(_NONCE_LEN)
    cipher = _xor_keystream(key, nonce, plaintext.encode("utf-8"))
    mac = hmac.new(key, nonce + cipher, hashlib.sha256).digest()
    blob = base64.urlsafe_b64encode(nonce + cipher + mac).decode("ascii")
    return f"{SECRET_PREFIX}{blob}"


def decrypt_secret(value: str | None) -> str | None:
    """Unwrap a stored secret. Plaintext leftovers pass through."""
    if value is None:
        return None
    if value == "" or not is_encrypted(value):
        return value
    raw = base64.urlsafe_b64decode(value[len(SECRET_PREFIX):].encode("ascii"))
    if len(raw) < _NONCE_LEN + _MAC_LEN:
        raise RuntimeError("Encrypted secret blob is truncated")
    nonce = raw[:_NONCE_LEN]
    mac = raw[-_MAC_LEN:]
    cipher = raw[_NONCE_LEN:-_MAC_LEN]
    key = get_or_create_data_key()
    expected = hmac.new(key, nonce + cipher, hashlib.sha256).digest()
    if not hmac.compare_digest(mac, expected):
        raise RuntimeError("Encrypted secret MAC mismatch (wrong data key?)")
    return _xor_keystream(key, nonce, cipher).decode("utf-8")


def encrypt_setting_value(key: str, value: str) -> str:
    """Encrypt ``value`` when ``key`` is a secret setting."""
    if key not in SECRET_SETTING_KEYS:
        return value
    wrapped = encrypt_secret(value)
    return "" if wrapped is None else wrapped


def decrypt_setting_value(key: str, value: str) -> str:
    """Decrypt ``value`` when ``key`` is a secret setting."""
    if key not in SECRET_SETTING_KEYS:
        return value
    plain = decrypt_secret(value)
    return "" if plain is None else plain


def migrate_plaintext_secrets() -> int:
    """Encrypt leftover plaintext secret columns. Returns rows rewritten."""
    rewritten = 0
    for row in query("SELECT id, api_key FROM ai_connections"):
        if _needs_wrap(row.get("api_key")):
            execute(
                "UPDATE ai_connections SET api_key = $1 WHERE id = $2",
                [encrypt_secret(str(row["api_key"])), row["id"]],
            )
            rewritten += 1
    for row in query("SELECT id, api_key FROM agents"):
        if _needs_wrap(row.get("api_key")):
            execute(
                "UPDATE agents SET api_key = $1 WHERE id = $2",
                [encrypt_secret(str(row["api_key"])), row["id"]],
            )
            rewritten += 1
    placeholders = ", ".join(f"${i + 1}" for i in range(len(SECRET_SETTING_KEYS)))
    keys = sorted(SECRET_SETTING_KEYS)
    for row in query(
        f"SELECT key, value FROM settings WHERE key IN ({placeholders})",
        keys,
    ):
        if _needs_wrap(row.get("value")):
            execute(
                "UPDATE settings SET value = $1 WHERE key = $2",
                [encrypt_setting_value(str(row["key"]), str(row["value"])), row["key"]],
            )
            rewritten += 1
    return rewritten


def _needs_wrap(value: object) -> bool:
    if value is None:
        return False
    text = str(value)
    return bool(text) and not is_encrypted(text)


def _xor_keystream(key: bytes, nonce: bytes, data: bytes) -> bytes:
    out = bytearray(len(data))
    offset = 0
    counter = 0
    while offset < len(data):
        block = hashlib.sha256(key + nonce + counter.to_bytes(8, "big")).digest()
        take = min(_BLOCK_LEN, len(data) - offset)
        for index in range(take):
            out[offset + index] = data[offset + index] ^ block[index]
        offset += take
        counter += 1
    return bytes(out)

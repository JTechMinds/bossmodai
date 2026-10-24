"""Encrypted local credentials, shared by the API and runtime worker.

All read/modify/write callers hold locked_store(): rotating refresh tokens
must never be exchanged concurrently by separate processes.
"""
from __future__ import annotations

import json
import os
import tempfile
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

from filelock import FileLock

from core.chatgpt import EXTENSION_ID
from core.extensions.paths import extension_data_dir
from db.secret_store import decrypt_secret, encrypt_secret


def _root() -> Path:
    root = extension_data_dir(EXTENSION_ID)
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    return root


@contextmanager
def locked_store():
    root = _root()
    with FileLock(root / "credentials.lock", timeout=60):
        path = root / "credentials.enc"
        if path.exists():
            data = json.loads(decrypt_secret(path.read_text(encoding="utf-8")))
        else:
            data = {"host_id": f"urn:uuid:{uuid4()}", "accounts": {}, "active": None}
            # Persist the host ID before the first sign-in.
            save_store(data)
        yield data


def save_store(data: dict) -> None:
    root = _root()
    fd, temporary = tempfile.mkstemp(dir=root, prefix="credentials-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(encrypt_secret(json.dumps(data)))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, root / "credentials.enc")
    finally:
        Path(temporary).unlink(missing_ok=True)

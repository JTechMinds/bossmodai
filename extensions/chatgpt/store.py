"""Encrypted extension-local state; serialize rotating credentials across processes."""

import json
import os
import tempfile
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4
from filelock import FileLock


class CredentialStore:
    def __init__(self, data_dir, seal, unseal, *, lock_factory=FileLock):
        self.root = data_dir
        self.seal = seal
        self.unseal = unseal
        self.lock_factory = lock_factory

    @contextmanager
    def locked(self):
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        with self.lock_factory(self.root / "credentials.lock", timeout=60):
            path = self.root / "credentials.enc"
            if path.exists():
                data = json.loads(self.unseal(path.read_text(encoding="utf-8")))
            else:
                data = {
                    "host_id": f"urn:uuid:{uuid4()}",
                    "accounts": {},
                    "active": None,
                }
                self.save(data)
            yield data

    def save(self, data):
        fd, temporary = tempfile.mkstemp(
            dir=self.root, prefix="credentials-", suffix=".tmp"
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                stream.write(self.seal(json.dumps(data)))
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.root / "credentials.enc")
        finally:
            Path(temporary).unlink(missing_ok=True)

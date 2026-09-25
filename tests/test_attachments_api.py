"""BossMod AI — API integration tests for attachment endpoints."""

from __future__ import annotations

import json
import os
import tempfile
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from main import app

_TOKEN = "test-attach-token-abc123"


@pytest.fixture(autouse=True)
def _client(tmp_path, monkeypatch):
    """Point company_root at a temp dir for isolation and inject the local API token."""
    monkeypatch.setenv("BOSSMOD_COMPANY_ROOT", str(tmp_path))
    monkeypatch.setenv("BOSSMOD_LOCAL_API_TOKEN", _TOKEN)
    import core.config as cfg
    monkeypatch.setattr(cfg, "get", lambda key, _orig=cfg.get: str(tmp_path) if key == "company_root" else _orig(key))
    monkeypatch.setattr(cfg, "get_live", lambda key, _orig=cfg.get_live: "10" if key == "bossmod.attach.max_size_mb" else _orig(key))
    client = TestClient(app, headers={"X-BossMod-Token": _TOKEN})
    yield client, tmp_path


def _png_bytes() -> bytes:
    # 1x1 red PNG
    return bytes.fromhex(
        "89504e470d0a1a0a0000000d49484452000000010000000108020000"
        "00907753de0000000c4944415408d7c000000003000185d29b"
        "290000000049454e44ae426082"
    )


def test_upload_png_success(_client):
    client, tmp = _client
    r = client.post(
        "/api/attachments/upload",
        files={"file": ("pic.png", _png_bytes(), "image/png")},
        data={"message_context": json.dumps({"type": "thread", "id": "t1"}), "original_name": "pic.png"},
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["preview_tier"] == "image"
    assert body["file_name"] == "pic.png"
    assert os.path.isfile(body["storage_path"])


def test_upload_exe_rejected(_client):
    client, tmp = _client
    r = client.post(
        "/api/attachments/upload",
        files={"file": ("bad.exe", b"MZ", "application/octet-stream")},
        data={"message_context": json.dumps({"type": "thread", "id": "t1"}), "original_name": "bad.exe"},
    )
    assert r.status_code == 415
    assert r.json()["detail"]["code"] == "BLOCKLISTED"


def test_upload_oversize_rejected(_client):
    client, tmp = _client
    # 11 MB of zeros
    big = b"\x00" * (11 * 1024 * 1024)
    r = client.post(
        "/api/attachments/upload",
        files={"file": ("big.bin", big, "application/octet-stream")},
        data={"message_context": json.dumps({"type": "thread", "id": "t1"}), "original_name": "big.bin"},
    )
    assert r.status_code == 413
    assert r.json()["detail"]["code"] == "SIZE_EXCEEDED"


def test_upload_bad_context(_client):
    client, tmp = _client
    r = client.post(
        "/api/attachments/upload",
        files={"file": ("x.txt", b"hi", "text/plain")},
        data={"message_context": json.dumps({"type": "bogus", "id": "x"}), "original_name": "x.txt"},
    )
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "BAD_CONTEXT"


def test_download_returns_file(_client):
    client, tmp = _client
    up = client.post(
        "/api/attachments/upload",
        files={"file": ("d.txt", b"hello world", "text/plain")},
        data={"message_context": json.dumps({"type": "unscoped", "id": ""}), "original_name": "d.txt"},
    )
    assert up.status_code == 201
    aid = up.json()["id"]
    dl = client.get(f"/api/attachments/{aid}")
    assert dl.status_code == 200
    assert dl.content == b"hello world"


def test_download_missing_404(_client):
    client, tmp = _client
    r = client.get(f"/api/attachments/{uuid.uuid4()}")
    assert r.status_code == 404


def test_preview_non_image_204(_client):
    client, tmp = _client
    up = client.post(
        "/api/attachments/upload",
        files={"file": ("n.txt", b"x", "text/plain")},
        data={"message_context": json.dumps({"type": "unscoped", "id": ""}), "original_name": "n.txt"},
    )
    aid = up.json()["id"]
    r = client.get(f"/api/attachments/{aid}/preview")
    assert r.status_code == 204


def test_preview_image_200(_client):
    client, tmp = _client
    up = client.post(
        "/api/attachments/upload",
        files={"file": ("p.png", _png_bytes(), "image/png")},
        data={"message_context": json.dumps({"type": "unscoped", "id": ""}), "original_name": "p.png"},
    )
    aid = up.json()["id"]
    r = client.get(f"/api/attachments/{aid}/preview")
    assert r.status_code == 200
    assert r.content == _png_bytes()

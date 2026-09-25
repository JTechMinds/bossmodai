"""Unit tests for core/attachments.py — pure logic, no I/O."""

from __future__ import annotations

import os

import pytest

from core.attachments import (
    BLOCKLIST,
    derive_storage_root,
    detect_mime_type,
    detect_preview_tier,
    get_file_extension,
    is_blocklisted,
    sanitize_file_name,
)


# ---------------------------------------------------------------------------
# derive_storage_root
# ---------------------------------------------------------------------------

class TestDeriveStorageRoot:
    def test_derive_storage_root_thread(self):
        result = derive_storage_root("thread", "task-123", "/data")
        assert result == os.path.join("/data", "projects", "task-123", "attachments")

    def test_derive_storage_root_direct(self):
        result = derive_storage_root("direct", "agent-42", "/data")
        assert result == os.path.join("/data", "agents", "agent-42", "attachments")

    def test_derive_storage_root_channel(self):
        result = derive_storage_root("channel", "proj-1/chan-2", "/data")
        assert result == os.path.join("/data", "projects", "proj-1", "channels", "chan-2", "attachments")

    def test_derive_storage_root_unscoped(self):
        result = derive_storage_root("unscoped", "", "/data")
        assert result == os.path.join("/data", "shared", "attachments")

    def test_derive_storage_root_invalid_type_raises(self):
        with pytest.raises(ValueError, match="Invalid context_type"):
            derive_storage_root("bogus", "x", "/data")


# ---------------------------------------------------------------------------
# sanitize_file_name
# ---------------------------------------------------------------------------

class TestSanitizeFileName:
    def test_sanitize_file_name_strips_traversal(self):
        assert sanitize_file_name("../../etc/passwd") == "etc_passwd"

    def test_sanitize_file_name_strips_null(self):
        assert sanitize_file_name("file\x00.txt") == "file.txt"

    def test_sanitize_file_name_truncates(self):
        long_name = "a" * 300 + ".txt"
        result = sanitize_file_name(long_name)
        assert len(result) <= 255

    def test_sanitize_file_name_backslash(self):
        assert sanitize_file_name("C:\\Users\\test.txt") == "C_Users_test.txt"

    def test_sanitize_file_name_empty(self):
        assert sanitize_file_name("///") == "unnamed"


# ---------------------------------------------------------------------------
# detect_preview_tier
# ---------------------------------------------------------------------------

class TestDetectPreviewTier:
    def test_detect_preview_tier_image(self):
        for ext in (".png", ".jpg", ".gif", ".webp"):
            assert detect_preview_tier(ext) == "image"

    def test_detect_preview_tier_text(self):
        for ext in (".txt", ".log", ".md", ".json"):
            assert detect_preview_tier(ext) == "text"

    def test_detect_preview_tier_document(self):
        for ext in (".pdf", ".docx", ".xlsx"):
            assert detect_preview_tier(ext) == "document"

    def test_detect_preview_tier_other(self):
        for ext in (".bin", ".dat"):
            assert detect_preview_tier(ext) == "other"


# ---------------------------------------------------------------------------
# is_blocklisted
# ---------------------------------------------------------------------------

class TestBlocklist:
    def test_blocklist_rejects_exe(self):
        assert is_blocklisted(".exe") is True

    def test_blocklist_rejects_all(self):
        for ext in (".exe", ".msi", ".bat", ".cmd", ".sh", ".ps1", ".app", ".dmg", ".deb", ".rpm", ".apk"):
            assert is_blocklisted(ext) is True, f"{ext} should be blocklisted"

    def test_blocklist_allows_common(self):
        for ext in (".png", ".txt", ".pdf"):
            assert is_blocklisted(ext) is False

    def test_blocklist_case_insensitive(self):
        assert is_blocklisted(".EXE") is True


# ---------------------------------------------------------------------------
# detect_mime_type
# ---------------------------------------------------------------------------

class TestDetectMimeType:
    def test_png(self):
        assert detect_mime_type(".png") == "image/png"

    def test_txt(self):
        assert detect_mime_type(".txt") == "text/plain"

    def test_unknown_fallback(self):
        assert detect_mime_type(".xyz123") == "application/octet-stream"


# ---------------------------------------------------------------------------
# get_file_extension
# ---------------------------------------------------------------------------

class TestGetFileExtension:
    def test_basic(self):
        assert get_file_extension("hello.png") == ".png"

    def test_no_extension(self):
        assert get_file_extension("README") == ""

    def test_case_normalized(self):
        assert get_file_extension("FILE.PNG") == ".png"

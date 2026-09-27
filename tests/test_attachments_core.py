"""Unit tests for core/attachments.py — pure logic, no I/O."""

from __future__ import annotations

from pathlib import Path

import pytest

from core.attachments import (
    BLOCKLIST,
    detect_mime_type,
    detect_preview_tier,
    get_file_extension,
    is_blocklisted,
    sanitize_file_name,
    storage_dir,
    virtual_path,
)


# ---------------------------------------------------------------------------
# storage_dir / virtual_path
# ---------------------------------------------------------------------------

class TestStorageDir:
    def test_direct_lives_under_the_floor(self):
        result = storage_dir(Path("/company/floor-1"), "direct", "agent-42")
        assert result == Path("/company/floor-1/.attachments/direct/agent-42")

    def test_thread_lives_under_the_floor(self):
        result = storage_dir(Path("/company/floor-1"), "thread", "chan-2")
        assert result == Path("/company/floor-1/.attachments/thread/chan-2")

    @pytest.mark.parametrize("retired", ["channel", "unscoped", "bogus"])
    def test_only_real_conversation_kinds_are_accepted(self, retired):
        with pytest.raises(ValueError, match="Invalid context_type"):
            storage_dir(Path("/company/floor-1"), retired, "x")


class TestVirtualPath:
    def test_mirrors_storage_dir_under_projects(self):
        assert (
            virtual_path("thread", "chan-2", "u_a.png")
            == "/projects/.attachments/thread/chan-2/u_a.png"
        )

    def test_invalid_type_raises(self):
        with pytest.raises(ValueError, match="Invalid context_type"):
            virtual_path("unscoped", "", "u_a.png")


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

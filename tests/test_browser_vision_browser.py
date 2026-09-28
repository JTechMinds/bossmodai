"""Browser Vision against a real headless browser and a local fixture site.

Uses the browser that setup installed for this checkout (read only: the
binaries under the real extension data dir). Everything the test writes —
screenshots, downloads — goes to a temp dir. Skipped only when that setup is
not ready.
"""

from __future__ import annotations

import io
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from PIL import Image

import db
from core import config
from core.bm_cli.install_layout import COMPANY_ROOT_ENV, default_company_root
from core.bm_cli.types import CliExecutionContext, ParsedCliCommand
from core.extensions.contract import ExtensionContext
from core.extensions.loader import import_package
from core.extensions.paths import extension_data_dir
from core.extensions.registry import get_discovery
from core.extensions.setup_runner import read_setup_status

_ENTRY = get_discovery().get("browser-vision")

_PAGE = b"""<!doctype html>
<html><head><title>fixture</title></head>
<body style="margin:0">
<button id="btn" style="position:absolute;left:0;top:0;width:200px;height:80px">Click</button>
<input id="inp" style="position:absolute;left:0;top:120px;width:300px;height:40px">
<a id="dl" href="/file.txt" download style="position:absolute;left:0;top:200px;width:200px;height:40px;display:block">Download</a>
<div style="height:4000px"></div>
<script>
let clicks = 0;
document.getElementById('btn').addEventListener('click', () => { clicks += 1; document.title = 'clicks:' + clicks; });
document.getElementById('inp').addEventListener('input', (e) => { document.title = 'typed:' + e.target.value; });
window.addEventListener('scroll', () => { document.title = 'scrolled:' + Math.round(window.scrollY); });
document.cookie = 'k=v; path=/';
</script>
</body></html>
"""
_COOKIE_PAGE = b"""<!doctype html>
<html><head><title>cookie</title></head><body>
<script>document.title = 'cookie:' + (document.cookie || 'none');</script>
</body></html>
"""
_FILE = b"downloaded fixture contents\n"


class _Fixture(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802 — http.server's hook name
        if self.path == "/":
            body, headers = _PAGE, {"Content-Type": "text/html"}
        elif self.path == "/cookie":
            body, headers = _COOKIE_PAGE, {"Content-Type": "text/html"}
        elif self.path == "/file.txt":
            body, headers = _FILE, {
                "Content-Type": "text/plain",
                "Content-Disposition": 'attachment; filename="file.txt"',
            }
        else:
            self.send_error(404)
            return
        self.send_response(200)
        for key, value in headers.items():
            self.send_header(key, value)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002 — base signature
        return


def _installed_browser_dir(monkeypatch: pytest.MonkeyPatch) -> Path:
    """The real install's extension data dir (conftest points the company root at a temp dir)."""
    saved = os.environ.get(COMPANY_ROOT_ENV)
    monkeypatch.delenv(COMPANY_ROOT_ENV)
    real_company = default_company_root()
    monkeypatch.setenv(COMPANY_ROOT_ENV, saved)
    test_dir = extension_data_dir(_ENTRY.id)
    return real_company / test_dir.parent.name / test_dir.name


def setup_function() -> None:
    db.close_connection()
    db_path = Path(os.environ["BOSSMOD_DB_PATH"])
    for suffix in ("", "-wal", "-shm"):
        candidate = Path(f"{db_path}{suffix}")
        if candidate.exists():
            candidate.unlink()
    db.init_db()
    config.reload()


def teardown_function() -> None:
    db.close_connection()


def test_an_agent_browses_clicks_types_scrolls_downloads_and_switches_windows(tmp_path, monkeypatch) -> None:
    install_dir = _installed_browser_dir(monkeypatch)
    status = read_setup_status(install_dir, required=True)
    if status.state != "ready":
        pytest.skip(f"Browser Vision setup is not ready at {install_dir} ({status.state}); run setup via Add → Extensions")
    # BrowserHost sets this for its process; restore it after the test.
    monkeypatch.delenv("PLAYWRIGHT_BROWSERS_PATH", raising=False)

    server = ThreadingHTTPServer(("127.0.0.1", 0), _Fixture)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"

    downloads = tmp_path / "me" / "downloads"
    package = import_package(_ENTRY)
    extension = package.BrowserVisionExtension(
        ExtensionContext(manifest=_ENTRY.manifest, data_dir=tmp_path / "data"),
        install_dir=install_dir,
        vision_model=lambda agent: ("vision-model", True),
        downloads_dir=lambda agent: downloads,
    )
    agent = db.create_agent("Iris", role="Researcher")
    ctx = CliExecutionContext(agent=agent, state=db.get_agent_state(agent.id), cwd="/me")

    def bv(raw: str, body: str | None = None):
        tokens = raw.split()
        result = extension.handle(ctx, ParsedCliCommand(raw=raw, name=tokens[0], args=tuple(tokens[1:])), body)
        assert result.ok, result.prompt_content
        return result

    def size_of(result) -> tuple[int, int]:
        return Image.open(io.BytesIO(Path(result.image_paths[0]).read_bytes())).size

    try:
        opened = bv(f"bv open {base}/")
        assert "title: fixture" in opened.prompt_content
        assert "viewport: 1280x800" in opened.prompt_content
        assert size_of(opened) == (1280, 800)

        # At the default density 160 a cell is bigger than every control, so,
        # as the prompt tells agents, each click zooms first: --focus on the
        # coarse cell holding the control, then click a fine cell read from
        # the zoomed grid. Coarse cell 0 is x 0–160, y 0–160.
        focused = bv("bv view --density 20 --focus 0-0")
        assert "focus: cells 0–0" in focused.prompt_content
        # 64 columns at density 20: fine cell 1*64+2 is (50, 30), on the button.
        assert "title: clicks:1" in bv(f"bv click {1 * 64 + 2}").prompt_content
        # The same at density 10 (128 columns): 1*128+2 is (25, 15).
        bv("bv view --density 10 --focus 0-0")
        assert "title: clicks:2" in bv(f"bv click {1 * 128 + 2}").prompt_content

        # The input (y 120–160): fine cell 7*64+2 is (50, 150).
        bv("bv view --density 20 --focus 0-0")
        bv(f"bv click {7 * 64 + 2}")
        assert "title: typed:hello" in bv("bv type", body="hello").prompt_content

        # The download link (y 200–240) is in coarse cell 8 (y 160–320);
        # fine cell 10*64+2 is (50, 210). The file lands in /me/downloads.
        bv("bv view --density 20 --focus 8-8")
        downloaded = bv(f"bv click {10 * 64 + 2}")
        assert f"downloaded: /me/downloads/file.txt ({len(_FILE)} bytes)" in downloaded.prompt_content
        assert (downloads / "file.txt").read_bytes() == _FILE

        assert "title: scrolled:640" in bv("bv scroll down").prompt_content

        # A cookie set by the page survives the phone context swap.
        assert "title: cookie:k=v" in bv(f"bv open {base}/cookie").prompt_content
        phone = bv("bv window phone")
        assert "title: cookie:k=v" in phone.prompt_content
        assert "viewport: 390x664" in phone.prompt_content
        assert size_of(phone) == (390, 664)

        desktop = bv("bv window desktop")
        assert "viewport: 1280x800" in desktop.prompt_content
        assert size_of(desktop) == (1280, 800)

        # Widescreen: the image shrinks to the 1568 cap, clicks stay in CSS px.
        wide = bv("bv window widescreen")
        assert "viewport: 1920x1080" in wide.prompt_content
        assert "image 1568x882 (scale ×0.8167)" in wide.prompt_content
        assert size_of(wide) == (1568, 882)
        bv(f"bv open {base}/")
        # 96 columns at density 20 over 1920 px: 1*96+2 is the button, 7*96+2 the input.
        bv("bv view --density 20 --focus 0-0")
        assert "title: clicks:1" in bv(f"bv click {1 * 96 + 2}").prompt_content
        bv("bv view --density 20 --focus 0-0")
        bv(f"bv click {7 * 96 + 2}")
        assert "title: typed:wide" in bv("bv type", body="wide").prompt_content

        # Any scheme the browser can open (D8): the fixture as a local file.
        page_file = tmp_path / "fixture.html"
        page_file.write_bytes(_PAGE)
        from_file = bv(f"bv open {page_file.as_uri()}")
        assert f"url: {page_file.as_uri()}" in from_file.prompt_content
        assert "title: fixture" in from_file.prompt_content

        assert "browser session closed" in bv("bv close").prompt_content
    finally:
        extension.shutdown()
        server.shutdown()

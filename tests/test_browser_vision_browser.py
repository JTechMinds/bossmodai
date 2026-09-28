"""Browser Vision against a real headless browser and a local fixture site.

Uses the browser that setup installed for this checkout (read only: the
binaries under the real extension data dir). Everything the test writes —
screenshots, downloads — goes to a temp dir. Skipped only when that setup is
not ready.
"""

from __future__ import annotations

import io
import os
import re
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
<body style="margin:0;font:16px sans-serif">
<button id="btn" style="position:absolute;left:20px;top:20px;width:200px;height:60px">Click me</button>
<label style="position:absolute;left:20px;top:110px">Your name <input id="inp" style="width:240px;height:30px"></label>
<select id="sel" style="position:absolute;left:20px;top:170px;width:160px;height:30px">
  <option>Apple</option><option>Banana</option><option>Cherry</option>
</select>
<a id="dl" href="/file.txt" download style="position:absolute;left:20px;top:230px;width:200px;height:30px;display:block">Download the file</a>
<canvas id="cv" width="160" height="100" style="position:absolute;left:640px;top:400px;background:#ddd"></canvas>
<iframe id="fr" src="/frame" style="position:absolute;left:900px;top:20px;width:300px;height:150px"></iframe>
<div style="height:4000px"></div>
<script>
let clicks = 0;
let canvasClicks = 0;
document.getElementById('btn').addEventListener('click', () => { clicks += 1; document.title = 'clicks:' + clicks; });
document.getElementById('inp').addEventListener('input', (e) => { document.title = 'typed:' + e.target.value; });
document.getElementById('sel').addEventListener('change', (e) => { document.title = 'picked:' + e.target.value; });
document.getElementById('cv').addEventListener('click', () => { canvasClicks += 1; document.title = 'canvas:' + canvasClicks; });
window.addEventListener('scroll', () => { document.title = 'scrolled:' + Math.round(window.scrollY); });
document.cookie = 'k=v; path=/';
</script>
</body></html>
"""
_FRAME_PAGE = b"""<!doctype html>
<html><body style="margin:0">
<button id="inner" style="margin:10px;width:140px;height:40px"
        onclick="parent.document.title = 'frame:clicked'">Inside button</button>
</body></html>
"""
_COOKIE_PAGE = b"""<!doctype html>
<html><head><title>cookie</title></head><body>
<script>document.title = 'cookie:' + (document.cookie || 'none');</script>
</body></html>
"""
# R19/R21: a Google-Places-like suggestion popup (rows with no role, no
# onclick, cursor:default, a script-attached mousedown handler), a clickable
# cursor:pointer card, a static floating banner, and a CSS :hover menu.
_PLACES_PAGE = b"""<!doctype html>
<html><head><title>places</title>
<style>
  .pac-item { cursor: default; height: 31px; line-height: 31px; padding: 0 4px; }
  .menu { position: absolute; left: 700px; top: 20px; width: 160px; height: 40px; background: #eee; }
  .menu .items { display: none; background: #fff; }
  .menu:hover .items { display: block; }
  .menu .items a { display: block; height: 30px; }
</style></head>
<body style="margin:0;font:16px sans-serif">
<input id="addr" placeholder="Enter your address" style="position:absolute;left:20px;top:20px;width:300px;height:30px">
<div class="menu">Menu<div class="items"><a href="#one">Menu one</a><a href="#two">Menu two</a></div></div>
<div id="card" style="position:absolute;left:400px;top:300px;width:200px;height:60px;cursor:pointer;background:#def">
  <span>Open</span> <span>card</span>
</div>
<div id="banner" style="position:absolute;z-index:5;left:20px;top:500px;width:400px;height:40px;background:#fe8">
  <p style="margin:0">Sale today only</p>
</div>
<script>
const box = document.createElement('div');
box.className = 'pac-container pac-logo';
box.style.cssText = 'position:absolute;z-index:1000;left:20px;top:56px;width:302px;background:#fff;border:1px solid #ccc';
for (const [query, town] of [['Alpha Road', 'Springfield'], ['Beta Street', 'Springfield'], ['Gamma Lane', 'Shelbyville']]) {
  const row = document.createElement('div');
  row.className = 'pac-item';
  row.innerHTML = '<span class="pac-icon"></span><span class="pac-item-query">' + query + '</span> <span>' + town + '</span>';
  row.addEventListener('mousedown', () => { document.title = 'place:' + query; });
  box.appendChild(row);
}
document.body.appendChild(box);
document.getElementById('card').addEventListener('click', () => { document.title = 'card:clicked'; });
</script>
</body></html>
"""
# R24: a single-page app. Its button shows "Starting…", then after 1.5 s
# replaces the content with the report and pushes a new URL (no page load).
_SPA_PAGE = """<!doctype html>
<html><head><title>spa</title></head>
<body style="margin:0;font:16px sans-serif">
<div id="app"><button id="run" style="position:absolute;left:20px;top:20px;width:200px;height:60px">Run report</button></div>
<script>
document.getElementById('run').addEventListener('click', () => {
  document.getElementById('app').textContent = 'Starting\u2026';
  document.title = 'starting';
  setTimeout(() => {
    document.getElementById('app').innerHTML = '<h1>Report ready</h1><button>Download report</button>';
    document.title = 'report ready';
    history.pushState({}, '', '/spa/report');
  }, 1500);
});
</script>
</body></html>
""".encode("utf-8")
_FILE = b"downloaded fixture contents\n"


class _Fixture(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802 — http.server's hook name
        if self.path == "/":
            body, headers = _PAGE, {"Content-Type": "text/html"}
        elif self.path == "/frame":
            body, headers = _FRAME_PAGE, {"Content-Type": "text/html"}
        elif self.path == "/cookie":
            body, headers = _COOKIE_PAGE, {"Content-Type": "text/html"}
        elif self.path == "/places":
            body, headers = _PLACES_PAGE, {"Content-Type": "text/html"}
        elif self.path == "/spa":
            body, headers = _SPA_PAGE, {"Content-Type": "text/html; charset=utf-8"}
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


@pytest.fixture()
def browse(tmp_path, monkeypatch):
    """A real Browser Vision extension, a fixture site and a ``bv`` runner.

    Yields ``(bv, base_url, downloads_dir, extension)``; ``bv(raw, body)``
    asserts the command succeeded and returns its result.
    """
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

    try:
        yield bv, base, downloads, extension
    finally:
        extension.shutdown()
        server.shutdown()


def _size_of(result) -> tuple[int, int]:
    return Image.open(io.BytesIO(Path(result.image_paths[0]).read_bytes())).size


def _legend(result) -> list[tuple[int, str, str]]:
    """Every ``[@n] kind "name"`` line of a result, as ``(n, kind, name)``."""
    found = []
    for line in result.prompt_content.splitlines():
        match = re.match(r'^\[@(\d+)\] (\w+) "(.*?)"', line.strip())
        if match:
            found.append((int(match.group(1)), match.group(2), match.group(3)))
    return found


def _mark(result, kind: str, name: str) -> int:
    """The number the legend gives a control, e.g. ``[@3] button "Click me"`` → 3."""
    for line in result.prompt_content.splitlines():
        match = re.match(r'^\[@(\d+)\] (\w+) "(.*)"', line.strip())
        if match and match.group(2) == kind and match.group(3) == name:
            return int(match.group(1))
    raise AssertionError(f"no {kind} {name!r} in the legend:\n{result.prompt_content}")


def test_an_agent_browses_with_marks_and_the_keypad(browse, tmp_path) -> None:
    bv, base, downloads, _extension = browse

    opened = bv(f"bv open {base}/")
    assert "title: fixture" in opened.prompt_content
    assert "viewport: 1280x800" in opened.prompt_content
    assert "view: full page" in opened.prompt_content
    assert _size_of(opened) == (1280, 800)
    assert "marks: 5" in opened.prompt_content

    # A mark click on the button, with feedback naming what it hit.
    button = _mark(opened, "button", "Click me")
    clicked = bv(f"bv click @{button}")
    assert "title: clicks:1" in clicked.prompt_content
    assert 'clicked button "Click me"' in clicked.prompt_content

    # Type into the input by its mark; its label names it, and it was empty.
    field = _mark(clicked, "textbox", "Your name")
    assert f'[@{field}] textbox "Your name" (empty)' in clicked.prompt_content
    typed = bv(f"bv type @{field}", body="hello")
    assert "title: typed:hello" in typed.prompt_content
    assert f'[@{field}] textbox "Your name" (filled)' in typed.prompt_content

    # Choose a dropdown option by its text.
    dropdown = _mark(typed, "select", "Apple")
    picked = bv(f"bv select @{dropdown} Banana")
    assert "title: picked:Banana" in picked.prompt_content
    assert f'[@{dropdown}] select "Banana"' in picked.prompt_content

    # A mark inside a same-origin iframe, offset into page coordinates.
    inner = _mark(picked, "button", "Inside button")
    framed = bv(f"bv click @{inner}")
    assert "title: frame:clicked" in framed.prompt_content
    assert 'clicked button "Inside button"' in framed.prompt_content

    # The canvas is not a control, so no mark: keypad zoom, then click.
    # Region 5 of 1280×800, then its region 9: centre (782, 489), on the canvas.
    zoomed = bv("bv zoom 5")
    assert "view: zoom 5 — region 427×267 px at (427, 267)" in zoomed.prompt_content
    assert _size_of(zoomed) == (1568, 980)
    on_canvas = bv("bv click 9")
    assert "title: canvas:1" in on_canvas.prompt_content
    assert "clicked canvas (not a control) at (782, 489)" in on_canvas.prompt_content
    assert "view: full page" in on_canvas.prompt_content

    # A chained zoom straight to the same spot, then a click.
    assert "view: zoom 5 › 9" in bv("bv zoom 5 9").prompt_content
    assert "title: canvas:2" in bv("bv click 5").prompt_content

    # A download by its link mark; the file lands in /me/downloads.
    link = _mark(on_canvas, "link", "Download the file")
    downloaded = bv(f"bv click @{link}")
    assert f"downloaded: /me/downloads/file.txt ({len(_FILE)} bytes)" in downloaded.prompt_content
    assert (downloads / "file.txt").read_bytes() == _FILE

    assert "title: scrolled:640" in bv("bv scroll down").prompt_content

    # A cookie set by the page survives the phone context swap.
    assert "title: cookie:k=v" in bv(f"bv open {base}/cookie").prompt_content
    phone = bv("bv window phone")
    assert "title: cookie:k=v" in phone.prompt_content
    assert "viewport: 390x664" in phone.prompt_content
    assert _size_of(phone) == (390, 664)

    desktop = bv("bv window desktop")
    assert "viewport: 1280x800" in desktop.prompt_content
    assert _size_of(desktop) == (1280, 800)

    # Widescreen: the image shrinks to the 1568 cap, marks still click in CSS px.
    wide = bv("bv window widescreen")
    assert "viewport: 1920x1080" in wide.prompt_content
    assert "image 1568x882 (scale ×0.8167)" in wide.prompt_content
    assert _size_of(wide) == (1568, 882)
    page = bv(f"bv open {base}/")
    assert "title: clicks:1" in bv(f"bv click @{_mark(page, 'button', 'Click me')}").prompt_content

    # Any scheme the browser can open (D8): the fixture as a local file.
    page_file = tmp_path / "fixture.html"
    page_file.write_bytes(_PAGE)
    from_file = bv(f"bv open {page_file.as_uri()}")
    assert f"url: {page_file.as_uri()}" in from_file.prompt_content
    assert "title: fixture" in from_file.prompt_content

    assert "browser session closed" in bv("bv close").prompt_content


def test_popup_rows_pointer_boxes_and_direct_pointing(browse) -> None:
    bv, base, _downloads, _extension = browse

    opened = bv(f"bv open {base}/places")
    legend = _legend(opened)
    # R19: the three suggestion rows are options, named by their text.
    options = [(n, name) for n, kind, name in legend if kind == "option"]
    assert [name for _n, name in options] == [
        "Alpha Road Springfield", "Beta Street Springfield", "Gamma Lane Shelbyville",
    ]
    # The cursor:pointer card is marked once, not its inner spans.
    assert [(kind, name) for _n, kind, name in legend if "card" in name or name in ("Open", "card")] == [
        ("other", "Open card"),
    ]
    # A static floating banner with one child is not a list.
    assert not any("Sale today" in name for _n, _kind, name in legend)
    # The hover menu's links are hidden until the mouse is over it.
    assert not any(name.startswith("Menu ") for _n, _kind, name in legend)
    assert "marks: 5" in opened.prompt_content and "image 1280x800" in opened.prompt_content

    beta = _mark(opened, "option", "Beta Street Springfield")
    picked = bv(f"bv click @{beta}")
    assert "title: place:Beta Street" in picked.prompt_content
    assert 'clicked option "Beta Street Springfield"' in picked.prompt_content

    card = _mark(picked, "other", "Open card")
    assert "title: card:clicked" in bv(f"bv click @{card}").prompt_content

    # R21: point at the Gamma row by image pixels (desktop: scale 1), check
    # what is under the cursor, then click where the mouse is.
    pointed = bv("bv point 170 134")
    assert 'pointer: (170, 134) px = (133, 168)‰ — hovering option "Gamma Lane Shelbyville"' in pointed.prompt_content
    clicked = bv("bv click")
    assert "title: place:Gamma Lane" in clicked.prompt_content
    assert 'clicked option "Gamma Lane Shelbyville" at (170, 134)' in clicked.prompt_content

    # The same row by the 0–1000 scale.
    bv(f"bv open {base}/places")
    assert 'hovering option "Alpha Road Springfield"' in bv("bv point1k 133 94").prompt_content
    assert "title: place:Alpha Road" in bv("bv click").prompt_content

    # Pointing hovers: the CSS :hover menu opens and its links are marked in
    # the returned screenshot.
    hovered = bv("bv point 780 28")  # on the "Menu" label line
    assert "hovering div (not a control)" in hovered.prompt_content
    names = [name for _n, kind, name in _legend(hovered) if kind == "link"]
    assert names == ["Menu one", "Menu two"]



def test_wait_watches_a_single_page_app_until_it_settles(browse) -> None:
    bv, base, _downloads, _extension = browse

    opened = bv(f"bv open {base}/spa")
    started = bv(f"bv click @{_mark(opened, 'button', 'Run report')}")
    assert "title: starting" in started.prompt_content
    assert started.data["status_lines"] == []  # same URL: nothing to announce yet

    waited = bv("bv wait 10")
    assert re.search(r"^wait: page changed after \d+\.\ds \(settled\)$", waited.prompt_content, re.M), waited.prompt_content
    assert "title: report ready" in waited.prompt_content
    assert f"url: {base}/spa/report" in waited.prompt_content
    assert ("button", "Download report") in [(kind, name) for _n, kind, name in _legend(waited)]
    short = base.split("://", 1)[1]
    assert waited.data["status_lines"] == [f"Browsing {short}/spa/report"]

    # Nothing moves on the settled page: the wait runs out and says so.
    still = bv("bv wait 1")
    assert "wait: no change after 1.0s" in still.prompt_content
    assert still.data["status_lines"] == []


def test_close_ends_the_session_its_screenshots_and_its_live_view(browse, tmp_path) -> None:
    """R28/R29: after bv close nothing shows the page as current."""
    bv, base, _downloads, extension = browse

    opened = bv(f"bv open {base}/")
    shot = Path(opened.image_paths[0])
    agent_dir = tmp_path / "data" / "shots" / extension.live_view()[0].agent_id
    marker = tmp_path / "data" / "sessions" / f"{agent_dir.name}.json"
    assert shot.is_file() and shot.parent.parent == agent_dir
    assert marker.is_file()
    assert [item.image_path for item in extension.live_view()] == [shot]

    assert "browser session closed" in bv("bv close").prompt_content

    assert not shot.parent.exists() and not agent_dir.exists()
    assert not marker.exists()
    assert extension.live_view() == []

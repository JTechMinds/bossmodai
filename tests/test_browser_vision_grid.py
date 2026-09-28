"""Browser Vision view rendering: keypad regions, zoom, scaling, marks, tags, contrast."""

from __future__ import annotations

import importlib
import io

import pytest
from PIL import Image, ImageDraw

from core.extensions.loader import import_package
from core.extensions.registry import get_discovery

_PACKAGE = import_package(get_discovery().get("browser-vision"))
grid = importlib.import_module(f"{_PACKAGE.__name__}.grid")
marks = importlib.import_module(f"{_PACKAGE.__name__}.marks")

_LABELS = grid.LabelStyle(font_ratio=0.2, font_min_px=10, font_max_px=14, opacity=0.8)
_FULL = (0.0, 0.0, 1280.0, 800.0)


def _png(width: int, height: int, color: tuple[int, int, int]) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), color).save(buffer, format="PNG")
    return buffer.getvalue()


def _mark(n: int, rect, kind: str = "button") -> object:
    x0, y0, x1, y1 = rect
    return marks.Mark(n=n, kind=kind, name=f"m{n}", rect=rect, point=((x0 + x1) / 2, (y0 + y1) / 2), state=None)


def _render(png: bytes, viewport, *, rect=None, color="auto", opacity=1.0, enabled=True,
            mark_list=(), show_marks=True, label=_LABELS, image_max_px=1568, pointer=None):
    style = grid.GridStyle(enabled=enabled, color=color, opacity=opacity)
    return grid.render_view(png, viewport, rect, style, list(mark_list), show_marks=show_marks,
                            image_max_px=image_max_px, label=label, keypad_font_px=28, pointer=pointer)


def _image(rendered) -> Image.Image:
    return Image.open(io.BytesIO(rendered.png)).convert("RGB")


# ─── keypad maths ───


def test_keypad_regions_are_numbered_like_a_phone() -> None:
    assert grid.keypad_region(_FULL, 1) == pytest.approx((0, 0, 1280 / 3, 800 / 3))
    assert grid.keypad_region(_FULL, 5) == pytest.approx((1280 / 3, 800 / 3, 2560 / 3, 1600 / 3))
    assert grid.keypad_region(_FULL, 9) == pytest.approx((2560 / 3, 1600 / 3, 1280, 800))
    assert grid.keypad_region(_FULL, 3)[0] == pytest.approx(2560 / 3)  # top row, right column
    assert grid.keypad_region(_FULL, 7)[1] == pytest.approx(1600 / 3)  # bottom row, left column
    assert grid.region_center(_FULL, 5) == pytest.approx((640, 400))


def test_zoom_chains_and_stops_below_one_pixel() -> None:
    rect = _FULL
    for _ in range(6):
        rect = grid.zoom_into(rect, 5)
    assert rect[2] - rect[0] == pytest.approx(1280 / 729)
    with pytest.raises(grid.ZoomLimit, match="ZOOM_LIMIT: region 5"):
        grid.zoom_into(rect, 5)


def test_parse_keypad_digit_accepts_1_to_9_only() -> None:
    assert [grid.parse_keypad_digit(str(d)) for d in range(1, 10)] == list(range(1, 10))
    for bad in ("0", "10", "a", "", "@3"):
        with pytest.raises(ValueError, match="one digit 1–9"):
            grid.parse_keypad_digit(bad)


def test_parsers_for_colour_and_opacity_name_their_forms() -> None:
    assert grid.parse_color("auto") == "auto"
    assert grid.parse_color("#FF0080") == (255, 0, 128)
    with pytest.raises(ValueError, match="auto or #rrggbb"):
        grid.parse_color("red")
    assert grid.parse_opacity("0.25") == 0.25
    for bad in ("1.5", "-0.1", "half"):
        with pytest.raises(ValueError, match="from 0 to 1"):
            grid.parse_opacity(bad)


# ─── scaling ───


def test_a_desktop_full_view_is_not_resized() -> None:
    rendered = _render(_png(1280, 800, (255, 255, 255)), (1280, 800))
    assert (rendered.width, rendered.height, rendered.scale) == (1280, 800, 1.0)


def test_a_widescreen_full_view_only_shrinks_to_the_cap() -> None:
    rendered = _render(_png(1920, 1080, (255, 255, 255)), (1920, 1080))
    assert (rendered.width, rendered.height) == (1568, 882)
    assert _image(rendered).size == (1568, 882)
    assert rendered.scale == pytest.approx(1568 / 1920)


def test_a_zoomed_view_is_scaled_to_the_cap_and_never_past_it() -> None:
    region = grid.keypad_region(_FULL, 5)
    rendered = _render(_png(1280, 800, (255, 255, 255)), (1280, 800), rect=region)
    assert (rendered.width, rendered.height) == (1568, 980)
    assert rendered.scale == pytest.approx(1568 / (1280 / 3))


def test_a_mismatched_screenshot_is_refused() -> None:
    with pytest.raises(ValueError, match="screenshot is 960×768 but the viewport is 960×960"):
        _render(_png(960, 768, (255, 255, 255)), (960, 960))


def test_a_zoom_shows_its_region_with_click_points_in_css_px() -> None:
    """A spot on the page appears where (css - origin) * s puts it in the zoom."""
    source = Image.new("RGB", (1280, 800), (255, 255, 255))
    cx, cy = grid.region_center(grid.zoom_into(_FULL, 5), 9)  # (782.2, 488.9)
    source.paste((255, 0, 0), (int(cx) - 3, int(cy) - 3, int(cx) + 4, int(cy) + 4))
    buffer = io.BytesIO()
    source.save(buffer, format="PNG")
    region = grid.zoom_into(_FULL, 5)
    rendered = _render(buffer.getvalue(), (1280, 800), rect=region, enabled=False)
    at = (round((cx - region[0]) * rendered.scale), round((cy - region[1]) * rendered.scale))
    red, green, blue = _image(rendered).getpixel(at)
    assert red > 200 and green < 60 and blue < 60


# ─── keypad drawing ───


def test_keypad_lines_are_dark_on_white_and_light_on_black() -> None:
    on_white = _image(_render(_png(300, 300, (255, 255, 255)), (300, 300)))
    on_black = _image(_render(_png(300, 300, (0, 0, 0)), (300, 300)))
    # x=100 is the first vertical line; y=20 is away from the digits.
    assert sum(on_white.getpixel((100, 20))) < 100
    assert sum(on_black.getpixel((100, 20))) > 600


def test_a_fixed_colour_is_honoured() -> None:
    rendered = _render(_png(300, 300, (255, 255, 255)), (300, 300), color=(255, 0, 0))
    assert _image(rendered).getpixel((100, 20)) == (255, 0, 0)


def test_grid_off_draws_no_keypad() -> None:
    image = _image(_render(_png(300, 300, (255, 255, 255)), (300, 300), enabled=False))
    assert image.getpixel((100, 20)) == (255, 255, 255)
    assert image.getpixel((150, 150)) == (255, 255, 255)  # no digit 5 either


def test_keypad_digits_sit_at_region_centres() -> None:
    image = _image(_render(_png(300, 300, (255, 255, 255)), (300, 300)))
    # Region 5's centre carries its tag (dark fill on white).
    assert sum(image.getpixel((150, 150))) < 400


# ─── marks ───


def test_a_mark_gets_an_outline_and_a_tag() -> None:
    mark = _mark(1, (100.0, 100.0, 300.0, 160.0))
    image = _image(_render(_png(400, 400, (255, 255, 255)), (400, 400), enabled=False, mark_list=[mark]))
    assert sum(image.getpixel((200, 100))) < 250  # top outline
    assert sum(image.getpixel((200, 130))) == 765  # inside is untouched
    assert sum(image.getpixel((103, 103))) < 250  # tag inside the top-left corner


def test_a_short_marks_tag_goes_just_above_it() -> None:
    mark = _mark(3, (100.0, 100.0, 300.0, 108.0))  # 8 px tall: no room inside
    image = _image(_render(_png(400, 400, (255, 255, 255)), (400, 400), enabled=False, mark_list=[mark]))
    assert sum(image.getpixel((101, 95))) < 250


def test_marks_off_draws_none_and_marks_outside_a_zoom_are_not_drawn() -> None:
    mark = _mark(1, (100.0, 100.0, 300.0, 160.0))
    hidden = _image(_render(_png(400, 400, (255, 255, 255)), (400, 400), enabled=False,
                            mark_list=[mark], show_marks=False))
    assert sum(hidden.getpixel((200, 100))) == 765
    # Zoom into region 9 (bottom right): the mark is elsewhere, so nothing is drawn.
    region = grid.keypad_region((0.0, 0.0, 400.0, 400.0), 9)
    zoomed = _image(_render(_png(400, 400, (255, 255, 255)), (400, 400), rect=region, enabled=False,
                            mark_list=[mark]))
    assert zoomed.getextrema() == ((255, 255), (255, 255), (255, 255))


def test_a_mark_partly_inside_a_zoom_is_drawn_clipped() -> None:
    mark = _mark(1, (100.0, 100.0, 300.0, 160.0))
    region = (200.0, 0.0, 400.0, 200.0)
    rendered = _render(_png(400, 400, (255, 255, 255)), (400, 400), rect=region, enabled=False, mark_list=[mark])
    image = _image(rendered)
    # Its right edge (x=300) is inside the zoom at (300-200)*s.
    assert sum(image.getpixel((round(100 * rendered.scale) - 1, round(130 * rendered.scale)))) < 250


def test_marks_are_numbered_in_reading_order_and_clipped_to_the_viewport() -> None:
    raw = [
        (marks.RawMark("link", "b", (500, 10, 600, 30), (550, 20), None), 0.0, 0.0, "main"),
        (marks.RawMark("link", "a", (10, 12, 100, 30), (50, 20), None), 0.0, 0.0, "main"),
        (marks.RawMark("button", "c", (10, 50, 100, 70), (50, 60), None), 0.0, 0.0, "main"),
        # Inside a frame at (900, 20): offset into page coordinates.
        (marks.RawMark("button", "in frame", (10, 10, 150, 50), (80, 30), None), 900.0, 20.0, "frame"),
        # Off the viewport after the offset: dropped.
        (marks.RawMark("button", "gone", (10, 10, 50, 50), (30, 30), None), 1275.0, 0.0, "frame"),
    ]
    placed = marks.place(raw, 1280, 800)
    assert [(m.n, m.name, key) for m, key in placed] == [
        (1, "a", "main"), (2, "b", "main"), (3, "in frame", "frame"), (4, "c", "main"),
    ]
    in_frame = placed[2][0]
    assert in_frame.rect == (910.0, 30.0, 1050.0, 70.0) and in_frame.point == (980.0, 50.0)


def test_legend_and_feedback_lines() -> None:
    mark = marks.Mark(n=12, kind="textbox", name="Enter your address", rect=(0, 0, 10, 10), point=(5, 5), state="empty")
    assert marks.legend_line(mark) == '[@12] textbox "Enter your address" (empty)'
    assert marks.feedback_line({"kind": "button", "name": "Go"}) == 'clicked button "Go"'
    assert marks.feedback_line({"tag": "CANVAS"}) == "clicked canvas (not a control)"
    assert marks.feedback_line(None) == "clicked nothing"
    try:
        marks.raw_from_js({"kind": "robot", "rect": [0, 0, 1, 1], "point": [0, 0]})
    except ValueError as exc:
        assert "unexpected mark" in str(exc)
    else:
        raise AssertionError("an unknown kind must be refused")


# ─── tags (R6/R9/R14 rules carried over to marks and keypad digits) ───


def _tag(rendered_px: float, text: str) -> tuple[int, int, int, int]:
    draw = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    font = grid._font(grid.label_font_size(rendered_px, _LABELS))
    return grid.tag_box(draw, (0, 0), text, font)


def test_label_font_size_follows_the_ratio_between_a_floor_and_a_cap() -> None:
    assert grid.label_font_size(160, _LABELS) == 14  # 32 by ratio, capped
    assert grid.label_font_size(60, _LABELS) == 12
    assert grid.label_font_size(24, _LABELS) == 10
    assert grid.label_font_size(200, _LABELS) == 14


def test_a_three_digit_tag_is_small() -> None:
    x0, y0, x1, y1 = _tag(60, "307")
    assert x1 - x0 + 1 <= 0.45 * 60


def test_tags_are_dark_on_light_ground_and_light_on_dark_ground() -> None:
    mark = _mark(1, (20.0, 20.0, 220.0, 80.0))
    on_white = _image(_render(_png(300, 300, (255, 255, 255)), (300, 300), enabled=False, mark_list=[mark]))
    on_black = _image(_render(_png(300, 300, (0, 0, 0)), (300, 300), enabled=False, mark_list=[mark]))
    # (22, 22) is the tag's padding: fill, not a digit.
    assert sum(on_white.getpixel((22, 22))) < 250
    assert sum(on_black.getpixel((22, 22))) > 500


def test_label_opacity_is_separate_from_line_opacity() -> None:
    png = _png(300, 300, (255, 255, 255))
    strong = _image(_render(png, (300, 300), opacity=1.0))
    faint = _image(_render(png, (300, 300), opacity=0.2))
    # Region 5's digit tag does not change with the line opacity…
    assert strong.getpixel((150, 150)) == faint.getpixel((150, 150))
    # …but the line does.
    assert strong.getpixel((100, 20)) != faint.getpixel((100, 20))
    lighter = _image(_render(png, (300, 300), label=grid.LabelStyle(0.2, 10, 14, 0.4)))
    assert sum(lighter.getpixel((150, 150))) > sum(strong.getpixel((150, 150)))


def test_a_tag_at_an_edge_is_moved_inside_the_image() -> None:
    assert grid.fit_inside((1270, 1, 1290, 14), (1280, 800)) == (1259, 1, 1279, 14)
    assert grid.fit_inside((1, 790, 20, 805), (1280, 800)) == (1, 784, 20, 799)
    # A tag drawn just above a mark at the very top moves down into view.
    assert grid.fit_inside((5, -12, 20, -1), (1280, 800)) == (5, 0, 20, 11)
    assert grid.fit_inside((61, 1, 80, 14), (1280, 800)) == (61, 1, 80, 14)


# ─── mark tags read @n (R20) ───


def test_a_mark_tag_reads_at_n_and_keypad_digits_stay_plain() -> None:
    assert grid.mark_tag_text(7) == "@7"
    font = grid._font(grid.label_font_size(60, _LABELS))
    draw = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    plain_w = grid.tag_box(draw, (0, 0), "1", font)[2] + 1
    at_w = grid.tag_box(draw, (0, 0), "@1", font)[2] + 1
    assert at_w > plain_w + 2
    mark = _mark(1, (100.0, 100.0, 300.0, 160.0))
    image = _image(_render(_png(400, 400, (255, 255, 255)), (400, 400), enabled=False, mark_list=[mark]))
    # The tag starts inside the outline at (102, 102); its top padding row is
    # filled out to the width of "@1", past where a plain "1" tag would end.
    assert sum(image.getpixel((102 + at_w - 1, 102))) < 250
    assert sum(image.getpixel((102 + at_w + 1, 110))) == 765

    keypad = _image(_render(_png(300, 300, (255, 255, 255)), (300, 300)))
    big = grid._font(28)
    five_w = grid.tag_box(draw, (0, 0), "5", big)[2] + 1
    five_h = grid.tag_box(draw, (0, 0), "5", big)[3] + 1
    left, top = round(150 - five_w / 2), round(150 - five_h / 2)
    assert sum(keypad.getpixel((left, top))) < 400  # the "5" tag's corner
    assert keypad.getpixel((left + five_w + 1, top)) == (255, 255, 255)  # no "@" widening it


# ─── image ↔ page mapping for bv point (R21) ───


def _geometry(viewport, rect=None):
    width, height = viewport
    return _render(_png(width, height, (255, 255, 255)), viewport, rect=rect, enabled=False).geometry


@pytest.mark.parametrize("case", ["full", "widescreen", "zoom 5", "zoom 5 9"])
def test_image_to_page_inverts_page_to_image(case) -> None:
    if case == "full":
        view = _geometry((1280, 800))
        assert grid.image_to_page(view, 412, 488) == (412, 488)
    elif case == "widescreen":
        view = _geometry((1920, 1080))
        assert (view.width, view.height) == (1568, 882)
        assert grid.image_to_page(view, 784, 441) == pytest.approx((960, 540))
    elif case == "zoom 5":
        region = grid.zoom_into(_FULL, 5)
        view = _geometry((1280, 800), region)
        assert grid.image_to_page(view, 0, 0) == pytest.approx(region[:2])
        assert grid.image_to_page(view, view.width / 2, view.height / 2) == pytest.approx((640, 400))
    else:
        region = grid.zoom_into(grid.zoom_into(_FULL, 5), 9)
        view = _geometry((1280, 800), region)
        centre = grid.region_center(grid.zoom_into(_FULL, 5), 9)
        assert grid.image_to_page(view, view.width / 2, view.height / 2) == pytest.approx(centre)
    for x, y in ((0, 0), (17, 250), (view.width - 1, view.height - 1)):
        assert grid.page_to_image(view, *grid.image_to_page(view, x, y)) == pytest.approx((x, y))


# ─── the pointer cursor (R21) ───


def test_the_cursor_is_a_crosshair_and_ring_that_leaves_the_centre_clear() -> None:
    image = _image(_render(_png(400, 400, (255, 255, 255)), (400, 400), enabled=False, pointer=(200.0, 100.0)))
    white = (255, 255, 255)
    # The exact point and its neighbours stay clear.
    for dx in (-1, 0, 1):
        for dy in (-1, 0, 1):
            assert image.getpixel((200 + dx, 100 + dy)) == white, (dx, dy)
    # Four 2 px arms, dark on a light page.
    for x, y in ((208, 100), (208, 101), (192, 100), (200, 108), (201, 108), (200, 92)):
        assert sum(image.getpixel((x, y))) < 100, (x, y)
    # The ring crosses each diagonal.
    assert any(sum(image.getpixel((200 + k, 100 + k))) < 100 for k in (3, 4, 5))
    assert any(sum(image.getpixel((200 - k, 100 - k))) < 100 for k in (3, 4, 5))
    # Nothing far away is touched.
    assert image.getpixel((230, 100)) == white


def test_the_cursor_is_light_on_a_dark_page_and_follows_the_zoom() -> None:
    dark = _image(_render(_png(400, 400, (0, 0, 0)), (400, 400), enabled=False, pointer=(200.0, 100.0)))
    assert sum(dark.getpixel((208, 100))) > 600
    region = grid.keypad_region((0.0, 0.0, 400.0, 400.0), 5)
    rendered = _render(_png(400, 400, (255, 255, 255)), (400, 400), rect=region, enabled=False, pointer=(200.0, 200.0))
    zoomed = _image(rendered)
    cx, cy = round(rendered.width / 2), round(rendered.height / 2)
    assert zoomed.getpixel((cx, cy)) == (255, 255, 255)
    assert sum(zoomed.getpixel((cx + 8, cy))) < 100
    # A pointer outside the zoomed region is not drawn.
    outside = _image(_render(_png(400, 400, (255, 255, 255)), (400, 400), rect=region, enabled=False,
                             pointer=(10.0, 10.0)))
    assert outside.getextrema() == ((255, 255), (255, 255), (255, 255))


def test_hover_line_uses_the_click_feedback_wording() -> None:
    assert marks.hover_line({"kind": "option", "name": "Alpha Road"}) == 'hovering option "Alpha Road"'
    assert marks.hover_line({"tag": "P"}) == "hovering p (not a control)"
    assert marks.hover_line(None) == "hovering nothing"

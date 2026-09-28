"""Browser Vision grid: numbering, click points, focus, rendering, contrast."""

from __future__ import annotations

import importlib
import io

import pytest
from PIL import Image

from core.extensions.loader import import_package
from core.extensions.registry import get_discovery


def _grid_module():
    package = import_package(get_discovery().get("browser-vision"))
    return importlib.import_module(f"{package.__name__}.grid")


grid = _grid_module()


def _png(width: int, height: int, color: tuple[int, int, int]) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), color).save(buffer, format="PNG")
    return buffer.getvalue()


_LABELS = grid.LabelStyle(font_ratio=0.2, font_min_px=10, opacity=0.8)


def _render(png: bytes, spec, *, color="auto", opacity=1.0, focus=None, enabled=True, label=_LABELS):
    style = grid.GridStyle(enabled=enabled, color=color, opacity=opacity)
    return grid.render_grid(png, spec, style, focus, label_min_px=24, image_max_px=1568, label=label)


def test_cols_rows_and_bounds_count_partial_edge_cells() -> None:
    spec = grid.GridSpec(1280, 800, 40)
    assert (spec.cols, spec.rows, spec.cell_count) == (32, 20, 640)
    odd = grid.GridSpec(1000, 610, 40)
    assert (odd.cols, odd.rows) == (25, 16)
    with pytest.raises(ValueError):
        grid.GridSpec(1280, 800, 0)


def test_numbering_round_trips() -> None:
    spec = grid.GridSpec(1000, 610, 40)
    for n in range(spec.cell_count):
        col, row = spec.cell_of(n)
        assert spec.cell_number(col, row) == n
    assert spec.cell_of(0) == (0, 0)
    assert spec.cell_of(25) == (0, 1)


def test_out_of_range_cells_raise_with_the_valid_range_and_are_never_clamped() -> None:
    spec = grid.GridSpec(1280, 800, 40)
    with pytest.raises(grid.CellOutOfRange, match="CELL_OUT_OF_RANGE: cell 640 .* valid cells are 0–639"):
        spec.cell_center(640)
    with pytest.raises(grid.CellOutOfRange):
        spec.cell_of(-1)


def test_cell_centres_and_the_geometric_clamp_on_partial_edge_cells() -> None:
    spec = grid.GridSpec(1280, 800, 40)
    assert spec.cell_center(0) == (20, 20)
    assert spec.cell_center(33) == (60, 60)
    edge = grid.GridSpec(1010, 800, 40)  # last column is 10 px wide
    assert edge.cell_center(edge.cols - 1) == (1009, 20)


def test_the_same_number_is_the_same_point_in_full_and_focused_views() -> None:
    """A cell read off a zoomed view clicks the spot the zoom showed it at."""
    full = grid.GridSpec(1280, 800, 10)
    n = full.cell_number(10, 9)
    cx, cy = full.cell_center(n)
    source = Image.new("RGB", (1280, 800), (255, 255, 255))
    source.paste((255, 0, 0), (int(cx) - 2, int(cy) - 2, int(cx) + 3, int(cy) + 3))
    buffer = io.BytesIO()
    source.save(buffer, format="PNG")
    rect = grid.focus_rect(grid.GridSpec(1280, 800, 40), 33, 66, 10)
    rendered = _render(buffer.getvalue(), full, focus=grid.FocusSpec(33, 66, rect), enabled=False)
    zoomed = Image.open(io.BytesIO(rendered.png)).convert("RGB")
    at = (round((cx - rect[0]) * rendered.scale), round((cy - rect[1]) * rendered.scale))
    red, green, blue = zoomed.getpixel(at)
    # LANCZOS rings a little at the marker's edge; the point is still the marker.
    assert red > 200 and green < 60 and blue < 60


def test_focus_snaps_outward_to_the_focus_density() -> None:
    base = grid.GridSpec(1280, 800, 40)
    # Cells 33 (x 40–80, y 40–80) and 66 (x 80–120, y 80–120) at density 40.
    assert grid.focus_rect(base, 33, 66, 10) == (40, 40, 120, 120)
    assert grid.focus_rect(base, 66, 33, 10) == (40, 40, 120, 120)
    # A focus density that does not divide the box widens it to whole cells.
    assert grid.focus_rect(base, 33, 66, 30) == (30, 30, 120, 120)
    # Never past the viewport.
    edge = grid.GridSpec(1010, 800, 40)
    assert grid.focus_rect(edge, edge.cols - 1, edge.cols - 1, 30)[2] == 1010
    with pytest.raises(grid.CellOutOfRange):
        grid.focus_rect(base, 33, 640, 10)


def test_parsers_accept_their_forms_and_name_them_on_error() -> None:
    assert grid.parse_density("7") == 7
    for bad in ("0", "-3", "4.5", "x"):
        with pytest.raises(ValueError, match="positive whole number"):
            grid.parse_density(bad)
    assert grid.parse_color("auto") == "auto"
    assert grid.parse_color("#FF0080") == (255, 0, 128)
    with pytest.raises(ValueError, match="auto or #rrggbb"):
        grid.parse_color("red")
    assert grid.parse_opacity("0.25") == 0.25
    for bad in ("1.5", "-0.1", "half"):
        with pytest.raises(ValueError, match="from 0 to 1"):
            grid.parse_opacity(bad)
    assert grid.parse_focus("245-290") == (245, 290)
    with pytest.raises(ValueError, match="like 245-290"):
        grid.parse_focus("245")


def test_a_full_view_keeps_the_viewport_size_and_is_labelled_at_the_default_density() -> None:
    spec = grid.GridSpec(1280, 800, 40)
    rendered = _render(_png(1280, 800, (255, 255, 255)), spec)
    assert (rendered.width, rendered.height) == (1280, 800)
    assert Image.open(io.BytesIO(rendered.png)).size == (1280, 800)
    assert rendered.labelled is True
    assert rendered.scale == 1.0


def test_a_mismatched_screenshot_is_refused() -> None:
    with pytest.raises(ValueError, match="screenshot is 960×768 but the grid is for 960×960"):
        _render(_png(960, 768, (255, 255, 255)), grid.GridSpec(960, 960, 40))


def test_label_step_small_cells_draw_lines_without_labels() -> None:
    spec = grid.GridSpec(400, 300, 10)
    rendered = _render(_png(400, 300, (255, 255, 255)), spec)
    assert rendered.labelled is False
    image = Image.open(io.BytesIO(rendered.png)).convert("RGB")
    assert image.getpixel((10, 5)) != (255, 255, 255)  # a line is drawn at x=10


def test_a_focused_view_scales_up_so_fine_cells_can_be_labelled() -> None:
    base = grid.GridSpec(1280, 800, 40)
    rect = grid.focus_rect(base, 33, 66, 10)
    spec = grid.GridSpec(1280, 800, 10)
    rendered = _render(_png(1280, 800, (255, 255, 255)), spec, focus=grid.FocusSpec(33, 66, rect))
    assert rendered.scale == pytest.approx(2.4)
    assert (rendered.width, rendered.height) == (192, 192)
    assert rendered.labelled is True


def test_a_focus_too_large_to_scale_is_unlabelled() -> None:
    base = grid.GridSpec(1280, 800, 40)
    rect = grid.focus_rect(base, 0, base.cell_count - 1, 5)  # the whole page at 4.8x
    spec = grid.GridSpec(1280, 800, 5)
    rendered = _render(_png(1280, 800, (255, 255, 255)), spec, focus=grid.FocusSpec(0, base.cell_count - 1, rect))
    assert rendered.labelled is False
    # Capped: 4.8x would be 6144 px wide; it stops at the long-edge cap.
    assert (rendered.width, rendered.height) == (1568, 980)


def test_auto_contrast_draws_dark_lines_on_white_and_light_lines_on_black() -> None:
    spec = grid.GridSpec(200, 200, 40)
    on_white = Image.open(io.BytesIO(_render(_png(200, 200, (255, 255, 255)), spec).png)).convert("RGB")
    on_black = Image.open(io.BytesIO(_render(_png(200, 200, (0, 0, 0)), spec).png)).convert("RGB")
    # x=40 is a vertical boundary; y=20 is mid-cell, away from labels.
    assert sum(on_white.getpixel((40, 20))) < 100
    assert sum(on_black.getpixel((40, 20))) > 600


def test_a_fixed_colour_is_honoured() -> None:
    spec = grid.GridSpec(200, 200, 40)
    rendered = _render(_png(200, 200, (255, 255, 255)), spec, color=(255, 0, 0))
    assert Image.open(io.BytesIO(rendered.png)).convert("RGB").getpixel((40, 20)) == (255, 0, 0)


def test_grid_off_draws_nothing() -> None:
    spec = grid.GridSpec(200, 200, 40)
    rendered = _render(_png(200, 200, (255, 255, 255)), spec, enabled=False)
    image = Image.open(io.BytesIO(rendered.png)).convert("RGB")
    assert image.getpixel((40, 20)) == (255, 255, 255)
    assert rendered.labelled is False


def test_a_widescreen_full_view_shrinks_to_the_cap_and_stays_labelled() -> None:
    spec = grid.GridSpec(1920, 1080, 40)
    rendered = _render(_png(1920, 1080, (255, 255, 255)), spec)
    assert (rendered.width, rendered.height) == (1568, 882)
    assert Image.open(io.BytesIO(rendered.png)).size == (1568, 882)
    assert rendered.scale == pytest.approx(1568 / 1920)
    assert rendered.labelled is True  # 40 x 0.8167 = 32.7 >= 24


def test_a_desktop_full_view_is_not_resized() -> None:
    rendered = _render(_png(1280, 800, (255, 255, 255)), grid.GridSpec(1280, 800, 40))
    assert (rendered.width, rendered.height, rendered.scale) == (1280, 800, 1.0)


def test_a_focus_crop_that_would_upscale_past_the_cap_is_capped() -> None:
    spec = grid.GridSpec(1920, 1080, 10)
    rect = (0, 0, 1000, 500)  # 2.4x would be 2400 px wide
    rendered = _render(_png(1920, 1080, (255, 255, 255)), spec, focus=grid.FocusSpec(0, 1, rect))
    assert max(rendered.width, rendered.height) == 1568
    assert rendered.scale == pytest.approx(1.568)
    assert rendered.labelled is False  # 10 x 1.568 < 24


def test_cell_centres_are_css_px_and_downscaling_only_maps_them() -> None:
    """The click point for n does not change; the drawn cell is at centre * s."""
    spec = grid.GridSpec(1920, 1080, 40)
    n = spec.cell_number(30, 15)
    cx, cy = spec.cell_center(n)
    assert (cx, cy) == (1220, 620)
    source = Image.new("RGB", (1920, 1080), (255, 255, 255))
    source.paste((255, 0, 0), (int(cx) - 6, int(cy) - 6, int(cx) + 7, int(cy) + 7))
    buffer = io.BytesIO()
    source.save(buffer, format="PNG")
    rendered = _render(buffer.getvalue(), spec, enabled=False)
    assert spec.cell_center(n) == (cx, cy)
    red, green, blue = Image.open(io.BytesIO(rendered.png)).convert("RGB").getpixel(
        (round(cx * rendered.scale), round(cy * rendered.scale))
    )
    assert red > 200 and green < 60 and blue < 60


# ─── labels are small corner tags (R6) ───


def _tag(cell_px: float, number: str) -> tuple[int, int, int, int]:
    from PIL import ImageDraw

    draw = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    font = grid._font(grid.label_font_size(cell_px, _LABELS))
    return grid.tag_box(draw, (0, 0), number, font)


def test_label_font_size_follows_the_ratio_with_a_floor() -> None:
    assert grid.label_font_size(60, _LABELS) == 12
    assert grid.label_font_size(24, _LABELS) == 10
    assert grid.label_font_size(200, _LABELS) == 40


def test_a_three_digit_tag_is_at_most_45_percent_of_a_density_60_cell() -> None:
    x0, y0, x1, y1 = _tag(60, "307")
    assert x1 - x0 + 1 <= 0.45 * 60


def test_a_three_digit_tag_fits_inside_the_smallest_labelled_cell() -> None:
    x0, y0, x1, y1 = _tag(24, "999")
    # Drawn one px in from the corner.
    assert 1 + (x1 - x0 + 1) <= 24 and 1 + (y1 - y0 + 1) <= 24


def test_tags_are_dark_on_light_ground_and_light_on_dark_ground() -> None:
    spec = grid.GridSpec(240, 240, 60)
    on_white = Image.open(io.BytesIO(_render(_png(240, 240, (255, 255, 255)), spec).png)).convert("RGB")
    on_black = Image.open(io.BytesIO(_render(_png(240, 240, (0, 0, 0)), spec).png)).convert("RGB")
    # (1, 1) is cell 0's tag padding: tag fill, not a digit and not a line.
    assert sum(on_white.getpixel((1, 1))) < 250
    assert sum(on_black.getpixel((1, 1))) > 500


def test_a_fixed_colour_fills_the_tag() -> None:
    spec = grid.GridSpec(240, 240, 60)
    rendered = _render(_png(240, 240, (255, 255, 255)), spec, color=(255, 0, 0))
    red, green, blue = Image.open(io.BytesIO(rendered.png)).convert("RGB").getpixel((1, 1))
    # Red at 0.8 over white.
    assert red == 255 and green == blue == 51


def test_label_opacity_is_separate_from_line_opacity() -> None:
    spec = grid.GridSpec(240, 240, 60)
    png = _png(240, 240, (255, 255, 255))
    strong = Image.open(io.BytesIO(_render(png, spec, opacity=1.0).png)).convert("RGB")
    faint = Image.open(io.BytesIO(_render(png, spec, opacity=0.2).png)).convert("RGB")
    # The tag does not change with the line opacity...
    assert strong.getpixel((1, 1)) == faint.getpixel((1, 1))
    # ...but the line does (x=60 is a boundary; y=40 is below the tags).
    assert strong.getpixel((60, 40)) != faint.getpixel((60, 40))
    # And a lower label opacity lightens the tag on its own.
    lighter = Image.open(io.BytesIO(_render(
        png, spec, label=grid.LabelStyle(font_ratio=0.2, font_min_px=10, opacity=0.4),
    ).png)).convert("RGB")
    assert sum(lighter.getpixel((1, 1))) > sum(strong.getpixel((1, 1)))


def _edge_tag(spec, n: int, size: tuple[int, int]):
    from PIL import ImageDraw

    cell = spec.cell_rect(n)
    draw = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    font = grid._font(grid.label_font_size(spec.density, _LABELS))
    anchored = grid.tag_box(draw, (cell[0] + 1, cell[1] + 1), str(n), font)
    return cell, anchored, grid.fit_inside(anchored, size)


def _inside_and_over_its_cell(cell, anchored, fitted, size) -> None:
    x0, y0, x1, y1 = fitted
    assert 0 <= x0 and x1 <= size[0] - 1 and 0 <= y0 and y1 <= size[1] - 1
    # Moved, not resized.
    assert (x1 - x0, y1 - y0) == (anchored[2] - anchored[0], anchored[3] - anchored[1])
    # Still over its own cell's rendered rect (inclusive box vs half-open rect).
    assert x1 >= cell[0] and x0 < cell[2] and y1 >= cell[1] and y0 < cell[3]


def test_cell_21s_tag_lies_inside_the_image_and_over_its_cell() -> None:
    spec = grid.GridSpec(1280, 800, 60)  # the last column is 20 px wide
    cell, anchored, fitted = _edge_tag(spec, 21, (1280, 800))
    assert cell == (1260, 0, 1280, 60)
    _inside_and_over_its_cell(cell, anchored, fitted, (1280, 800))


def test_a_three_digit_tag_in_the_narrow_last_column_is_shifted_left() -> None:
    spec = grid.GridSpec(1280, 800, 60)
    cell, anchored, fitted = _edge_tag(spec, 285, (1280, 800))
    assert anchored[2] > 1279, "unshifted, the tag would overflow the right edge"
    _inside_and_over_its_cell(cell, anchored, fitted, (1280, 800))
    assert fitted[2] == 1279
    # The rendered image shows tag fill in the last pixel column of that row.
    rendered = _render(_png(1280, 800, (255, 255, 255)), spec)
    image = Image.open(io.BytesIO(rendered.png)).convert("RGB")
    assert sum(image.getpixel((1279, fitted[1]))) < 250


def test_a_tag_in_a_short_last_row_is_shifted_up() -> None:
    spec = grid.GridSpec(1280, 790, 60)  # the last row is 10 px tall
    n = spec.cell_number(0, spec.rows - 1)
    cell, anchored, fitted = _edge_tag(spec, n, (1280, 790))
    assert anchored[3] > 789, "unshifted, the tag would overflow the bottom edge"
    _inside_and_over_its_cell(cell, anchored, fitted, (1280, 790))
    assert fitted[3] == 789


def test_a_tag_inside_the_image_is_not_moved() -> None:
    assert grid.fit_inside((61, 1, 80, 14), (1280, 800)) == (61, 1, 80, 14)

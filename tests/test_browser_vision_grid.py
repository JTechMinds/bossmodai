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


def _render(png: bytes, spec, *, color="auto", opacity=1.0, focus=None, enabled=True):
    style = grid.GridStyle(enabled=enabled, color=color, opacity=opacity)
    return grid.render_grid(png, spec, style, focus, label_min_px=24, render_max_px=1600)


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

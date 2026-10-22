"""Virtual Desktop screen maths: shrinking, coordinate mapping, the pointer line."""

from __future__ import annotations

import io

import pytest
from PIL import Image

from tests._virtual_desktop_fakes import module, png, types

screen = module("screen")


def _capture(width: int, height: int, pointer: tuple[int, int], window: str = "") -> object:
    return types.Capture(png=png(width, height), screen=(width, height), pointer=pointer, pointer_window=window, active_window="")


def test_a_screen_that_fits_is_sent_unchanged() -> None:
    original = png(1280, 800)
    rendered = screen.fit(original, 1568)
    assert rendered.png is original
    assert (rendered.width, rendered.height, rendered.scale) == (1280, 800, 1.0)
    assert screen.image_text(rendered) == "1280x800"


def test_a_larger_screen_is_shrunk_to_the_longest_side() -> None:
    rendered = screen.fit(png(1920, 1080), 1568)
    assert (rendered.width, rendered.height) == (1568, 882)
    assert rendered.scale == pytest.approx(1568 / 1920)
    assert Image.open(io.BytesIO(rendered.png)).size == (1568, 882)
    assert screen.image_text(rendered) == "1568x882 (scale ×0.8167)"


def test_image_pixels_map_to_screen_pixels_and_back() -> None:
    scale = 1568 / 1920
    assert screen.image_to_screen(784, 441, scale) == (960, 540)
    assert screen.screen_to_image(960, 540, scale) == (784, 441)
    assert screen.image_to_screen(1567, 881, scale) == (1919, 1079)


def test_point_takes_image_pixels_within_the_image() -> None:
    rendered = screen.fit(png(1280, 800), 1568)
    assert screen.parse_point("0", "0", rendered, thousandths=False) == (0, 0)
    assert screen.parse_point("1279", "799", rendered, thousandths=False) == (1279, 799)
    assert screen.parse_point("412.6", "88.2", rendered, thousandths=False) == (413, 88)


@pytest.mark.parametrize(("raw_x", "raw_y"), [("1280", "10"), ("10", "800"), ("-1", "5"), ("x", "5"), ("nan", "5"), ("inf", "1")])
def test_point_refuses_anything_outside_the_image_naming_the_range(raw_x: str, raw_y: str) -> None:
    rendered = screen.fit(png(1280, 800), 1568)
    with pytest.raises(ValueError, match=r"x must be 0–1279, y 0–799 \(pixels of the latest screenshot, image 1280x800\)"):
        screen.parse_point(raw_x, raw_y, rendered, thousandths=False)


def test_point1k_spans_the_image_from_corner_to_corner() -> None:
    rendered = screen.fit(png(1280, 800), 1568)
    assert screen.parse_point("0", "0", rendered, thousandths=True) == (0, 0)
    assert screen.parse_point("1000", "1000", rendered, thousandths=True) == (1279, 799)
    assert screen.parse_point("500", "500", rendered, thousandths=True) == (640, 400)
    with pytest.raises(ValueError, match="x and y must be 0–1000"):
        screen.parse_point("1001", "0", rendered, thousandths=True)


def test_the_pointer_line_reports_image_pixels_thousandths_and_the_window() -> None:
    rendered = screen.fit(png(1280, 800), 1568)
    text = screen.pointer_text(_capture(1280, 800, (412, 488), "Mozilla Firefox"), rendered)
    assert text == '(412, 488) px = (322, 611)‰ — over "Mozilla Firefox"'


def test_the_pointer_line_names_no_window_when_its_title_is_empty() -> None:
    rendered = screen.fit(png(1280, 800), 1568)
    assert screen.pointer_text(_capture(1280, 800, (0, 0)), rendered) == "(0, 0) px = (0, 0)‰"


def test_the_pointer_line_is_in_image_pixels_of_a_shrunk_screen() -> None:
    rendered = screen.fit(png(1920, 1080), 1568)
    assert screen.pointer_text(_capture(1920, 1080, (960, 540)), rendered) == "(784, 441) px = (500, 501)‰"


def test_change_key_is_a_small_greyscale_thumbnail() -> None:
    dark, light = screen.change_key(png(1280, 800, 10)), screen.change_key(png(1280, 800, 240))
    assert len(dark) == 64 * 40
    assert dark != light
    assert screen.change_key(png(1280, 800, 10)) == dark

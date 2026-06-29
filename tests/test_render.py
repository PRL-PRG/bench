"""Tests for the rendering toolkit (tag escaping + table alignment)."""

from __future__ import annotations

from bench.report.render import PLAIN, RICH, cell, cells, tag, table


# ----- tag -------------------------------------------------------------------


def test_tag_rich_wraps_in_theme_style():
    assert tag(RICH, "value", "1.23") == "[bench.value]1.23[/]"


def test_tag_rich_none_style_is_bare_text():
    assert tag(RICH, None, " ± ") == " ± "


def test_tag_plain_drops_style():
    assert tag(PLAIN, "value", "1.23") == "1.23"


def test_tag_escapes_bracket_for_both_renderers():
    # Both go through console.print, so a literal "[" must be escaped or rich
    # parses it as a tag and eats the text.
    assert tag(RICH, "label", "elapsed [ms]") == "[bench.label]elapsed \\[ms][/]"
    assert tag(PLAIN, None, "elapsed [ms]") == "elapsed \\[ms]"


# ----- table -----------------------------------------------------------------


def test_table_left_aligns_columns_with_gap():
    rows = [[cell("ab"), cell("1")], [cell("abcd"), cell("22")]]
    out = table(PLAIN, rows)
    # col0 width 4, col1 width 2, gap 3, indent 2; trailing pad trimmed.
    assert out == ["  ab     1", "  abcd   22"]
    # both col1 values start at the same offset (left-aligned)
    assert out[0].index("1") == out[1].index("22")


def test_table_right_align():
    rows = [[cell("x"), cell("1", align="r")], [cell("x"), cell("22", align="r")]]
    out = table(PLAIN, rows)
    assert out == ["  x    1", "  x   22"]


def test_table_widths_ignore_markup():
    # A styled cell and a plain cell of equal visible width align identically.
    styled = [[cell("name", "label"), cell("val", "value")], [cell("x"), cell("y")]]
    out = table(RICH, styled)
    plain_out = table(PLAIN, [[cell("name"), cell("val")], [cell("x"), cell("y")]])
    # Strip markup from the rich render and it matches the plain layout exactly.
    import re

    stripped = [re.sub(r"\[/?[^\]]*\]", "", line) for line in out]
    assert stripped == plain_out


def test_table_multispan_cell_width():
    # A multi-span cell's width is the sum of its visible spans.
    row = cells(("1.43", "value"), (" ± ", None), ("0.02", "success"))
    assert row.width == len("1.43 ± 0.02")
    out = table(PLAIN, [[cell("a"), row], [cell("bb"), cell("x")]])
    assert out[0] == "  a    1.43 ± 0.02"
    assert out[1] == "  bb   x"


def test_table_empty():
    assert table(RICH, []) == []

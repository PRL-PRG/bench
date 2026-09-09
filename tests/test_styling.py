"""Tests for the styling toolkit (tag escaping + table composition)."""

from __future__ import annotations

from io import StringIO

import pytest
from rich.console import Console, RenderableType
from rich.text import Text

from bench.console.styling import (
    INDENT,
    Cell,
    Span,
    Styling,
    compose_table,
    indented,
    join_blocks,
    num,
)


def _render(renderable: RenderableType) -> list[str]:
    buf = StringIO()
    Console(file=buf, force_terminal=False, width=200).print(renderable)
    return [line.rstrip() for line in buf.getvalue().splitlines()]


# ----- Styling ---------------------------------------------------------------


def test_span_rich_wraps_in_theme_style():
    assert Styling.RICH.span(Span("1.23", "value")) == "[bench.value]1.23[/]"


def test_span_plain_drops_style():
    assert Styling.PLAIN.span(Span("1.23", "value")) == "1.23"


def test_span_without_style_is_bare_text():
    assert Styling.RICH.span(Span(" ± ")) == " ± "


def test_span_escapes_brackets_whatever_the_styling():
    # Everything lands in console.print, so a literal "[ms]" must be escaped or
    # rich parses it as a tag and eats the text. Both branches of `span` do it:
    # the unit column and the matrix row label are unstyled spans.
    assert (
        Styling.RICH.span(Span("elapsed [ms]", "label"))
        == "[bench.label]elapsed \\[ms][/]"
    )
    assert Styling.PLAIN.span(Span("elapsed [ms]", "label")) == "elapsed \\[ms]"
    assert Styling.RICH.span(Span("elapsed [ms]")) == "elapsed \\[ms]"
    assert Styling.PLAIN.span(Span("elapsed [ms]")) == "elapsed \\[ms]"


def test_collapse_cell_concatenates_the_spans():
    cell = Cell(Span("1.43", "value"), Span(" ± "), Span("0.02", "adjustment"))
    assert Styling.PLAIN.collapse_cell(cell) == "1.43 ± 0.02"
    assert Styling.RICH.collapse_cell(cell) == (
        "[bench.value]1.43[/] ± [bench.adjustment]0.02[/]"
    )


def test_collapse_cells_joins_with_a_space():
    out = Styling.PLAIN.collapse_cells(Cell(Span("a")), Cell(Span("b")))
    assert out == "a b"


# ----- compose_table ---------------------------------------------------------


def test_compose_table_left_aligns_columns_with_gap():
    rows = [[Cell(Span("ab")), Cell(Span("1"))], [Cell(Span("abcd")), Cell(Span("22"))]]
    out = _render(compose_table(Styling.PLAIN, rows, ncols=2, gap=1))
    # both col1 values start at the same offset (left-aligned)
    assert out[0].index("1") == out[1].index("22")
    assert out == ["ab   1", "abcd 22"]


def test_compose_table_widths_ignore_markup():
    # A styled cell and a plain cell of equal visible width align identically.
    styled = [
        [Cell(Span("name", "label")), Cell(Span("val", "value"))],
        [Cell(Span("x")), Cell(Span("y"))],
    ]
    plain = [
        [Cell(Span("name")), Cell(Span("val"))],
        [Cell(Span("x")), Cell(Span("y"))],
    ]
    assert _render(compose_table(Styling.RICH, styled, ncols=2, gap=3)) == _render(
        compose_table(Styling.PLAIN, plain, ncols=2, gap=3)
    )


def test_compose_table_multispan_cell_is_one_column():
    # A multi-span cell collapses into a single column, so the next column lines
    # up behind its full visible width.
    rows = [
        [Cell(Span("a")), Cell(Span("1.43", "value"), Span(" ± "), Span("0.02"))],
        [Cell(Span("bb")), Cell(Span("x"))],
    ]
    out = _render(compose_table(Styling.PLAIN, rows, ncols=2, gap=3))
    assert out == ["a    1.43 ± 0.02", "bb   x"]


def test_compose_table_renders_headers():
    out = _render(
        compose_table(Styling.PLAIN, [[Cell(Span("1"))]], [Cell(Span("mean"))], gap=1)
    )
    assert out[0] == "mean"
    assert out[1] == "1"


def test_compose_table_needs_ncols_without_headers():
    with pytest.raises(ValueError, match="ncols or headers"):
        compose_table(Styling.PLAIN, [[Cell(Span("1"))]], gap=1)


def test_compose_table_rejects_ncols_that_contradicts_headers():
    with pytest.raises(ValueError, match="match the size of headers"):
        compose_table(
            Styling.PLAIN, [[Cell(Span("1"))]], [Cell(Span("mean"))], gap=1, ncols=2
        )


def test_compose_table_empty():
    assert _render(compose_table(Styling.RICH, [], ncols=1, gap=1)) == []


# ----- join_blocks / indented / num ------------------------------------------


def test_join_blocks_separates_with_a_blank_line():
    assert _render(join_blocks([Text("a"), Text("b")])) == ["a", "", "b"]


def test_join_blocks_of_one_adds_nothing():
    assert _render(join_blocks([Text("a")])) == ["a"]


def test_indented_shifts_by_the_shared_indent():
    assert _render(indented(Text("a"))) == [" " * INDENT + "a"]


def test_num_formats_to_the_requested_precision():
    assert num(1.239) == "1.24"
    assert num(1.239, 1) == "1.2"

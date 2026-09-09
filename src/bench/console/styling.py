from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum

from rich.console import Group, RenderableType
from rich.markup import escape
from rich.padding import Padding
from rich.table import Table
from rich.text import Text

INDENT = 2


@dataclass(frozen=True, slots=True)
class Span:
    """One run of text and the semantic style it carries - a theme key without the
    `bench.` prefix, or None for unstyled."""

    text: str
    style: str | None = None


@dataclass(frozen=True, slots=True)
class Cell:
    spans: tuple[Span]

    def __init__(self, *spans: Span) -> None:
        object.__setattr__(self, "spans", tuple(spans))


class Styling(Enum):
    """Whether semantic styles are applied at all."""

    RICH = True
    PLAIN = False

    def span(self, span: Span) -> str:
        text = escape(span.text)
        if span.style is not None and self.value:
            return f"[bench.{span.style}]{text}[/]"
        else:
            return text

    def collapse_cell(self, cell: Cell) -> str:
        return "".join(map(self.span, cell.spans))

    def collapse_cells(self, *cells: Cell, sep: str = " ") -> str:
        return sep.join(map(self.collapse_cell, cells))


def join_blocks(parts: Sequence[RenderableType]) -> Group:
    """Stack renderables with a blank line between them."""
    out: list[RenderableType] = []
    for p in parts:
        if out:
            out.append(Text(""))
        out.append(p)
    return Group(*out)


def compose_table(
    styling: Styling,
    cells: Sequence[Sequence[Cell]],
    headers: Sequence[Cell] | None = None,
    *,
    gap: int,
    ncols: int | None = None,
) -> Table:
    table = Table(
        box=None,
        show_header=headers is not None,
        header_style=None,
        pad_edge=False,
        collapse_padding=True,
        padding=(0, gap),
    )

    if headers is None:
        if ncols is None:
            raise ValueError("Either ncols or headers need to be specified")
        for _ in range(ncols):
            table.add_column(overflow="fold")
    else:
        if ncols is not None and len(headers) != ncols:
            raise ValueError("ncols need to match the size of headers")

        for h in headers:
            table.add_column(styling.collapse_cell(h), overflow="fold")

    for row in cells:
        table.add_row(*map(styling.collapse_cell, row))

    return table


def indented(inner: RenderableType) -> Padding:
    # `expand=False`: Padding otherwise stretches the table to the console width,
    # trailing every line with spaces.
    return Padding(inner, (0, 0, 0, INDENT), expand=False)


def num(x: float, p: int = 2) -> str:
    return f"{x:.{p}f}"

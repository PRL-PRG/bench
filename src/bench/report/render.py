"""Rendering toolkit: a markup-flag renderer, the styling chokepoint, and a table.

A formatter builds rows of `Cell`s and renders them with a `Renderer`. `RICH`
wraps each styled span in a `[bench.<style>]...[/]` tag. `PLAIN` leaves bare text.
Either way the text flows through `console.print`, so `tag` always escapes `[`
(otherwise a literal `[ms]` would be parsed as markup and eaten). Column widths
are measured on the *visible* text, never the markup, so styling never skews
alignment.
"""

from __future__ import annotations

from dataclasses import dataclass

# (text, style): style is a theme key (without the "bench." prefix) or None.
type Span = tuple[str, str | None]


@dataclass(frozen=True, slots=True)
class Renderer:
    markup: bool


RICH = Renderer(markup=True)
PLAIN = Renderer(markup=False)


def tag(r: Renderer, style: str | None, text: str) -> str:
    """Apply one semantic style to `text`. The single place `[` is escaped, so
    no caller can leak a bracket into rich's markup parser."""
    safe = text.replace("[", r"\[")
    if not r.markup or style is None:
        return safe
    return f"[bench.{style}]{safe}[/]"


@dataclass(frozen=True, slots=True)
class Cell:
    """One table cell: a sequence of styled spans plus an alignment."""

    spans: tuple[Span, ...]
    align: str = "l"  # "l" | "r"

    @property
    def width(self) -> int:
        """Visible width - the markup is irrelevant to layout."""
        return sum(len(t) for t, _ in self.spans)


def cell(text: str, style: str | None = None, align: str = "l") -> Cell:
    """A one-span cell (the common case)."""
    return Cell(((text, style),), align)


def cells(*spans: Span, align: str = "l") -> Cell:
    """A multi-span cell, e.g. a coloured `1.43 ± 0.02× worse`."""
    return Cell(tuple(spans), align)


def table(
    r: Renderer, rows: list[list[Cell]], *, indent: str = "  ", gap: int = 3
) -> list[str]:
    """Align `rows` into gap-separated columns. Widths come from visible text.
    Cells are left- or right-padded per their align. Trailing pad is trimmed."""
    if not rows:
        return []
    ncols = max(len(row) for row in rows)
    widths = [0] * ncols
    for row in rows:
        for i, c in enumerate(row):
            widths[i] = max(widths[i], c.width)

    sep = " " * gap
    out: list[str] = []
    for row in rows:
        parts: list[str] = []
        for i, c in enumerate(row):
            rendered = "".join(tag(r, s, t) for t, s in c.spans)
            pad = " " * (widths[i] - c.width)
            parts.append(pad + rendered if c.align == "r" else rendered + pad)
        out.append((indent + sep.join(parts)).rstrip())
    return out

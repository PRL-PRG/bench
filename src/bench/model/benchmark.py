from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, cast

from bench.core.policy import format_policy
from bench.model.invocation import (
    Invocation,
    SuccessFn,
)

if TYPE_CHECKING:
    from bench.core.metric import Metric
    from bench.core.outlier import OutlierDetection
    from bench.core.policy import StoppingPolicy
    from bench.runner import Controller

# A label function turns a resolved benchmark into the human-readable variant
# identifier shown in reports Benchmark (not a Context) because labels reflect
# the resolved execution.
type LabelFn = Callable[[Benchmark], str]


@dataclass(frozen=True, slots=True)
class Benchmark:
    """One fully-resolved benchmark variant."""

    suite: str
    name: str
    invocation: Invocation
    variant: Variant
    metrics: Sequence[Metric]
    success: SuccessFn
    warmup: StoppingPolicy
    runs: StoppingPolicy
    outlier_detection: OutlierDetection
    cooldown: float
    controller: Controller
    data: Mapping[str, Any]
    label_fn: LabelFn

    @property
    def variant_label(self) -> str:
        return self.label_fn(self)


# A skip predicate on a resolved `Benchmark`. Returning falsy drops the variant.
type BenchmarkPred = Callable[[Benchmark], bool]


@dataclass(frozen=True, slots=True)
class Variant:
    """Representation of a variant"""

    pairs: tuple[tuple[str, str], ...] = ()
    "Canonical representation: `((dimension, value), ...)`, sorted by dimension"

    def __post_init__(self) -> None:
        # Canonicalize once, at construction: equality and hashing are the pair
        # tuple's, so two variants with the same dimensions must order alike.
        object.__setattr__(self, "pairs", tuple(sorted(self.pairs)))

    @staticmethod
    def _stringify_value(v: Any) -> str:
        if isinstance(v, (list, tuple)):
            return " ".join(str(x) for x in cast(Sequence[object], v))
        return str(v)

    @staticmethod
    def of(mapping: Mapping[str, Any]) -> Variant:
        return Variant(
            tuple((k, Variant._stringify_value(v)) for k, v in mapping.items())
        )

    def __getitem__(self, dim: str, /) -> str:
        for k, v in self.pairs:
            if k == dim:
                return v

        raise KeyError(dim)

    def get(self, dim: str, default: str | None = None) -> str | None:
        for k, v in self.pairs:
            if k == dim:
                return v
        return default

    def keys(self) -> tuple[str, ...]:
        return tuple(k for k, _ in self.pairs)

    def as_dict(self) -> dict[str, str]:
        return dict(self.pairs)

    def __iter__(self):
        return self.pairs.__iter__()

    def __len__(self) -> int:
        return self.pairs.__len__()

    def __contains__(self, dim: str) -> bool:
        for k, _ in self.pairs:
            if k == dim:
                return True

        return False


# ---------------------------------------------------------------------------
# Format
# ---------------------------------------------------------------------------


def format_variant_pairs(pairs: Iterable[tuple[str, str]]) -> str:
    """`k=v, ...` naming a variant on its own. `""` if empty. Unlike
    `format_variant` this carries no surrounding ` (...)`, so it also serves where
    the variant is the whole string: a summary label, a directory component."""
    return ", ".join(f"{k}={v}" for k, v in pairs)


def format_variant(variant: Variant) -> str:
    """` (k=v, ...)` suffix identifying a matrix variant. `""` if empty."""
    if not variant:
        return ""

    return f" ({format_variant_pairs(variant)})"


def format_benchmark(
    suite: str,
    benchmark: str,
    variant: Variant,
    variant_label: str = "",
) -> str:
    """Resolved benchmark-variant name: `suite/benchmark` (collapsing the stutter
    when the two names match) with the variant label or `(k=v, ...)` suffix
    appended."""
    head = benchmark if suite == benchmark else f"{suite}/{benchmark}"
    if variant_label:
        return f"{head}/{variant_label}"
    return f"{head}{format_variant(variant)}"


def format_identifier(
    suite: str,
    benchmark: str,
    variant: Variant,
    run: int,
    variant_label: str = "",
) -> str:
    """Canonical run label: the benchmark name followed by `#run`."""
    return f"{format_benchmark(suite, benchmark, variant, variant_label)} #{run}"


def format_benchmark_verbose(b: Benchmark, run: int) -> str:
    def _metric_name(m: Any) -> str:
        """A metric's display name: its `metric` field if it has one, else the
        class name (e.g. Time, Rebench)."""
        return getattr(m, "metric", type(m).__name__)

    e = b.invocation
    env_str = ", ".join(f"{k}={v}" for k, v in e.env.items()) if e.env else ""
    stdin_str = f"{len(e.stdin)} bytes" if e.stdin is not None else "<none>"
    timeout_str = f"{e.timeout}s" if e.timeout is not None else "<none>"
    metric_str = ", ".join(_metric_name(m) for m in b.metrics)
    variant_str = str(b.variant.as_dict())
    label_str = b.variant_label or "<none>"

    return "\n".join(
        [
            format_identifier(b.suite, b.name, b.variant, run, b.variant_label),
            f"  suite:      {b.suite}",
            f"  benchmark:  {b.name}",
            f"  run:        {run}",
            f"  warmup:     {format_policy(b.warmup)}",
            f"  runs:       {format_policy(b.runs)}",
            f"  command:    {' '.join(e.command)}",
            f"  cwd:        {e.cwd}",
            f"  env:        {{{env_str}}}",
            f"  timeout:    {timeout_str}",
            f"  stdin:      {stdin_str}",
            f"  metrics:    {metric_str}",
            f"  variant:    {variant_str}",
            f"  label:      {label_str}",
        ]
    )

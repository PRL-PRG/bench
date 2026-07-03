"""OutlierDetection: flags statistical outliers in a metric's sample values.

A strategy operating on one metric's pooled values. Detection is informational:
flagged samples stay in the summary statistics (mean/median/stdev are unchanged),
they are only counted and warned about.

`ModifiedZScore` ports hyperfine's modified Z-score / MAD test.

References:
- Boris Iglewicz and David Hoaglin (1993), "How to Detect and Handle Outliers".
- <https://en.wikipedia.org/wiki/Median_absolute_deviation>
"""

from __future__ import annotations

import abc
import statistics
from collections.abc import Sequence
from dataclasses import dataclass

# Minimum modified Z-score for a datapoint to be an outlier. 1.4826 converts the
# MAD into an estimator for the standard deviation. 10 is the number of standard
# deviations. (We use the same hyperfine's scaled out OUTLIER_THRESHOLD.)
OUTLIER_THRESHOLD = 1.4826 * 10.0


def modified_zscores(xs: Sequence[float]) -> list[float]:
    """Modified Z-scores `(x_i - median) / MAD`, MAD = median absolute deviation.

    `MAD == 0` is the exact-fit case: it happens iff more than half the values
    are tied (Croux et al. 2006 - a property of every robust scale estimator). A
    metric with no robust spread has no meaningful outliers, so we report none
    (every score 0). hyperfine sidesteps this entirely: it only detects on
    wall-clock time, which always varies (`MAD > 0`), so its `epsilon` guard is
    never exercised. The `MAD == 0` branch has no effect when `MAD > 0`."""
    median = statistics.median(xs)
    deviations = [abs(x - median) for x in xs]
    mad = statistics.median(deviations)
    if mad == 0:
        return [0.0] * len(xs)
    return [(x - median) / mad for x in xs]


class OutlierDetection(abc.ABC):
    """Strategy: which values in a metric's sample are outliers."""

    __slots__ = ()

    @abc.abstractmethod
    def detect(self, values: Sequence[float]) -> list[bool]:
        """Return a boolean mask, one entry per value, True where outlier."""


@dataclass(frozen=True, slots=True)
class NoDetection(OutlierDetection):
    """Flags nothing - the off switch."""

    def detect(self, values: Sequence[float]) -> list[bool]:
        return [False] * len(values)


@dataclass(frozen=True, slots=True)
class ModifiedZScore(OutlierDetection):
    """Flags values whose modified Z-score exceeds `threshold`."""

    threshold: float = OUTLIER_THRESHOLD

    def detect(self, values: Sequence[float]) -> list[bool]:
        if not values:
            return []
        return [abs(z) > self.threshold for z in modified_zscores(values)]

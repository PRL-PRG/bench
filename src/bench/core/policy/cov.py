

from __future__ import annotations

import itertools
import math
from collections import deque
from typing import TYPE_CHECKING

from bench.core.policy.base import PolicyState, StoppingPolicy

if TYPE_CHECKING:
    from bench.model.results import Execution

class CoefficientOfVariation(StoppingPolicy):
    __slots__ = ("metric", "threshold", "window", "min_runs")

    def __init__(
        self,
        metric: str,
        threshold: float = 0.02,
        window: int = 5,
        min_runs: int = 10,
    ) -> None:
        if window < 2:
            raise ValueError("CoV window must be >= 2 for stdev")

        self.metric = metric
        self.threshold = threshold
        self.window = window
        self.min_runs = min_runs

    def start(self) -> CoVState:
        return CoVState(self)


class CoVState(PolicyState):
    __slots__ = ("cfg", "window", "sum", "sumsq", "n_runs")

    def __init__(self, cfg: CoefficientOfVariation):
        self.cfg = cfg
        self.window: deque[float] = deque(maxlen=cfg.window)
        self.sum = 0.0
        self.sumsq = 0.0
        self.n_runs = 0

    def observe(self, execution: Execution) -> None:
        # CoV tracks one scalar per run. More than one matching sample is
        # ambiguous and would inflate the
        # window / min_runs counters, so reject it loudly.

        iteration_samples = (s for i in execution.iterations for s in i.samples)
        samples = itertools.chain(execution.process_samples, iteration_samples)

        matching = [s.value for s in samples if s.metric == self.cfg.metric]
        if len(matching) > 1:
            raise ValueError(
                f"CoefficientOfVariation metric {self.cfg.metric!r} matched "
                f"{len(matching)} samples in a single run; it expects at most "
                f"one per run. Restrict the metric to a single line (e.g. "
                f".last_line()) or watch a different metric."
            )
        for value in matching:  # 0 or 1
            if len(self.window) == self.window.maxlen:
                old = self.window[0]
                self.sum -= old
                self.sumsq -= old * old
            self.window.append(value)
            self.sum += value
            self.sumsq += value * value
            self.n_runs += 1

    def satisfied(self) -> bool:
        cfg = self.cfg
        if self.n_runs < cfg.min_runs or len(self.window) < cfg.window:
            return False
        n = len(self.window)
        mean = self.sum / n
        if mean == 0:
            return False
        # Var = (E[X^2] - E[X]^2) * n / (n-1)   (Bessel correction)
        var = max((self.sumsq / n) - mean * mean, 0.0) * n / (n - 1)
        return math.sqrt(var) / abs(mean) <= cfg.threshold


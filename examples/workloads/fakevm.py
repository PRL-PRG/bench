#!/usr/bin/env python3
"""A fake VM harness: runs a workload N times, printing one time per line.

Models a JIT runtime warming up: the first iterations are slow, then the
times settle around a steady state. Usage: `fakevm.py <bench> -n N [-w W]`.

`-w` runs W extra warmup iterations first and does not print them, the way a
real harness does. bench measures whole processes, so leading iterations inside
one process are the harness's own business.
"""

import argparse
import random
import time

BASELINE_MS = {"fib": 40.0, "sort": 25.0}


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("bench", choices=sorted(BASELINE_MS))
    p.add_argument("-n", type=int, required=True, help="measured iteration count")
    p.add_argument(
        "-w", type=int, default=0, help="warmup iterations, run but not printed"
    )
    args = p.parse_args()

    steady = BASELINE_MS[args.bench]
    rng = random.Random(42)
    for i in range(args.w + args.n):
        # Warmup curve: ~3x slower at first, decaying towards steady state.
        factor = 1.0 + 2.0 * (0.5**i)
        elapsed = steady * factor * rng.uniform(0.97, 1.03)
        time.sleep(elapsed / 10_000)  # pretend to work (sped up 10x)
        if i >= args.w:
            print(f"{elapsed:.3f}")


if __name__ == "__main__":
    main()

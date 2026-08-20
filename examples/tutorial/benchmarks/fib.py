"""fib workload: runs the payload `iterations` times (default 1), printing
one elapsed-ms line per iteration.

Usage: `fib.py [iterations [warmup]]`. The optional second argument runs that
many extra leading iterations without printing them - a harness owns its own
warmup, because bench's warmup policy counts whole processes.

Run once it is an ordinary script (tutorial 1 times the whole process). Given a
loop count it becomes a harness workload (tutorial 5 reads each printed line).
"""

import sys
import time


def fib(n):
    if n < 2:
        return n
    return fib(n - 2) + fib(n - 1)


def payload():
    return fib(32)


iterations = int(sys.argv[1]) if len(sys.argv) > 1 else 1
warmup = int(sys.argv[2]) if len(sys.argv) > 2 else 0
for i in range(warmup + iterations):
    start = time.perf_counter()
    payload()
    if i >= warmup:
        print(f"{(time.perf_counter() - start) * 1000:.3f}")

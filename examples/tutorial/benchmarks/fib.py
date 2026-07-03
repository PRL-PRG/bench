"""fib workload: runs the payload `iterations` times (default 1), printing
one elapsed-ms line per iteration.

Run once it is an ordinary script (tutorial 1 times the whole process). Given a
loop count it becomes a harness workload (tutorial 3 reads each printed line).
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
for _ in range(iterations):
    start = time.perf_counter()
    payload()
    print(f"{(time.perf_counter() - start) * 1000:.3f}")

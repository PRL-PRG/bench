"""Towers of Hanoi workload: runs the payload `iterations` times (default 1),
printing one elapsed-ms line per iteration.

Like `fib.py`, it is dual-use: a plain script for tutorial 1, a harness workload
(when given a loop count) for tutorial 3.
"""

import sys
import time


def hanoi(disks, frm, to, via, moves):
    if disks == 0:
        return moves
    moves = hanoi(disks - 1, frm, via, to, moves)
    moves += 1
    return hanoi(disks - 1, via, to, frm, moves)


def payload():
    return hanoi(22, 0, 1, 2, 0)


iterations = int(sys.argv[1]) if len(sys.argv) > 1 else 1
for _ in range(iterations):
    start = time.perf_counter()
    payload()
    print(f"{(time.perf_counter() - start) * 1000:.3f}")

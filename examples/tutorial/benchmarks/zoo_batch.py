"""Time-boxed throughput workload: count how many 10k-call batches finish in a
fixed window. Wall time is ~constant (the window itself), so the meaningful
signal is the batch count, which tutorial 2 extracts with a custom metric.
"""

import time


class Zoo:
    def __init__(self):
        self.a = self.b = self.c = self.d = self.e = self.f = 1

    def ant(self):
        return self.a

    def banana(self):
        return self.b

    def tuna(self):
        return self.c

    def hay(self):
        return self.d

    def grass(self):
        return self.e

    def mouse(self):
        return self.f


DURATION = 5.0  # seconds, the fixed measurement window

zoo = Zoo()
total = 0
batches = 0
start = time.perf_counter()
while time.perf_counter() - start < DURATION:
    for _ in range(10000):
        total += (
            zoo.ant()
            + zoo.banana()
            + zoo.tuna()
            + zoo.hay()
            + zoo.grass()
            + zoo.mouse()
        )
    batches += 1

print(batches)
print(total)

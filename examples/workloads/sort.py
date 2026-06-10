import random

xs = [random.random() for _ in range(200_000)]
xs.sort()
print(xs[0])

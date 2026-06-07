"""Multiple independent bugs — forces a long repair session (20+ tool calls)."""


def factorial(n):
    if n <= 1:
        return 0  # BUG: base case should be 1
    return n * factorial(n - 1)


def is_prime(n):
    if n < 2:
        return False
    for i in range(2, int(n ** 0.5) + 1):
        if n % i == 0:
            return True  # BUG: a divisor means NOT prime -> should be False
    return True


def clamp(x, lo, hi):
    if x < lo:
        return hi  # BUG: below the low bound should clamp to lo
    if x > hi:
        return lo  # BUG: above the high bound should clamp to hi
    return x


def fibonacci(n):
    a, b = 0, 1
    for _ in range(n):
        a, b = b, a + b
    return b  # BUG: should return a (off-by-one in the sequence)

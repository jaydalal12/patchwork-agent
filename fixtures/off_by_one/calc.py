"""A tiny module with one deliberate bug for the eval harness."""


def total(items):
    # BUG: skips the first element (off-by-one slice).
    return sum(items[1:])


def average(items):
    if not items:
        return 0.0
    return total(items) / len(items)

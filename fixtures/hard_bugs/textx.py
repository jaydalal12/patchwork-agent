"""More bugs in a second module so the agent must work across files."""


def slugify(s):
    # BUG: spaces should become hyphens, not be removed.
    return s.lower().replace(" ", "")


def initials(name):
    # BUG: [:1] keeps only the first initial; should keep all of them.
    return "".join(w[0].upper() for w in name.split())[:1]


def repeat(s, n):
    # BUG: off-by-one — repeats one time too many.
    return s * (n + 1)

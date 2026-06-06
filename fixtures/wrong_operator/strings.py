"""Second eval fixture: a boolean/operator bug."""


def is_palindrome(s):
    cleaned = "".join(c.lower() for c in s if c.isalnum())
    # BUG: compares to the string itself instead of its reverse.
    return cleaned == cleaned


def shout(s):
    return s.upper() + "!"

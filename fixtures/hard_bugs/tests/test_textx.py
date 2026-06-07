from textx import initials, repeat, slugify


def test_slugify():
    assert slugify("Hello World Foo") == "hello-world-foo"


def test_initials():
    assert initials("ada lovelace") == "AL"
    assert initials("grace brewster hopper") == "GBH"


def test_repeat():
    assert repeat("ab", 3) == "ababab"

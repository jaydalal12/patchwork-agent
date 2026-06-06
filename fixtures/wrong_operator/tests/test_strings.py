from strings import is_palindrome, shout


def test_palindrome_true():
    assert is_palindrome("A man a plan a canal Panama") is True


def test_palindrome_false():
    assert is_palindrome("hello") is False


def test_shout():
    assert shout("hi") == "HI!"

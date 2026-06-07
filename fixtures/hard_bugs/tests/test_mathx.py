from mathx import clamp, factorial, fibonacci, is_prime


def test_factorial():
    assert factorial(5) == 120
    assert factorial(0) == 1


def test_is_prime_true():
    assert is_prime(7) is True
    assert is_prime(13) is True


def test_is_prime_false():
    assert is_prime(8) is False
    assert is_prime(9) is False


def test_clamp_below():
    assert clamp(-5, 0, 10) == 0


def test_clamp_above():
    assert clamp(99, 0, 10) == 10


def test_clamp_within():
    assert clamp(5, 0, 10) == 5


def test_fibonacci():
    assert fibonacci(0) == 0
    assert fibonacci(1) == 1
    assert fibonacci(7) == 13

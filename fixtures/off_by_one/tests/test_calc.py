from calc import average, total


def test_total_sums_all_items():
    assert total([1, 2, 3, 4]) == 10


def test_total_single():
    assert total([5]) == 5


def test_average():
    assert average([2, 4, 6]) == 4.0

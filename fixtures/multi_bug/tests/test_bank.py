import pytest

from bank import deposit, withdraw


def test_deposit_increases_balance():
    assert deposit(100, 50) == 150


def test_withdraw_decreases_balance():
    assert withdraw(100, 30) == 70


def test_withdraw_guards_overdraft():
    with pytest.raises(ValueError):
        withdraw(10, 100)

"""Third eval fixture: two independent bugs (forces a multi-step repair)."""


def deposit(balance, amount):
    # BUG: deposit should increase the balance.
    return balance - amount


def withdraw(balance, amount):
    if amount > balance:
        raise ValueError("insufficient funds")
    # BUG: withdraw should decrease the balance.
    return balance + amount

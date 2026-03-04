import json
from datetime import datetime

import pytest

from src.accounts import Account, Transaction, INITIAL_BALANCE


class DummyAccount(Account):
    """Account subclass that avoids DB and logging side effects."""

    def save(self):  # type: ignore[override]
        # Override to avoid writing to the real database during tests
        pass


def test_transaction_total_and_repr():
    tx = Transaction(
        symbol="AAPL",
        quantity=2,
        price=150.0,
        timestamp=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        rationale="test",
    )

    assert tx.total() == pytest.approx(300.0)
    assert "2 shares of AAPL" in repr(tx)


def test_deposit_increases_balance():
    acct = DummyAccount(
        name="test",
        balance=INITIAL_BALANCE,
        strategy="",
        holdings={},
        transactions=[],
        portfolio_value_time_series=[],
    )

    acct.deposit(500.0)
    assert acct.balance == pytest.approx(INITIAL_BALANCE + 500.0)


def test_deposit_must_be_positive():
    acct = DummyAccount(
        name="test",
        balance=INITIAL_BALANCE,
        strategy="",
        holdings={},
        transactions=[],
        portfolio_value_time_series=[],
    )

    with pytest.raises(ValueError):
        acct.deposit(0)


def test_withdraw_cannot_overdraw():
    acct = DummyAccount(
        name="test",
        balance=100.0,
        strategy="",
        holdings={},
        transactions=[],
        portfolio_value_time_series=[],
    )

    with pytest.raises(ValueError):
        acct.withdraw(200.0)


def test_report_returns_valid_json(monkeypatch):
    # Use a dummy account but keep the original type to exercise report()
    acct = DummyAccount(
        name="test",
        balance=INITIAL_BALANCE,
        strategy="",
        holdings={},
        transactions=[],
        portfolio_value_time_series=[],
    )

    # Avoid calling real market or DB in report() by patching methods it uses
    monkeypatch.setattr("src.accounts.get_share_price", lambda symbol: 100.0)
    monkeypatch.setattr("src.accounts.write_log", lambda *args, **kwargs: None)

    report_json = acct.report()
    data = json.loads(report_json)

    assert data["name"] == "test"
    assert "total_portfolio_value" in data
    assert "total_profit_loss" in data


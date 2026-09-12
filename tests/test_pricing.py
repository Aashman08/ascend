"""Unit tests for the money math. No database, no HTTP."""

from decimal import Decimal

from app.pricing import compute_totals, policy_downpayment

RATE = Decimal("0.20")  # the rate the exercise specifies


def test_spec_example_matches_document():
    """The worked example from the exercise: two policies -> $200 down, $400 financed."""
    policies = [(Decimal("200"), Decimal("50")), (Decimal("300"), Decimal("50"))]

    totals = compute_totals(policies, RATE)

    assert policy_downpayment(Decimal("200"), Decimal("50"), RATE) == Decimal("90.00")
    assert policy_downpayment(Decimal("300"), Decimal("50"), RATE) == Decimal("110.00")
    assert totals.total_amount == Decimal("600.00")
    assert totals.total_downpayment == Decimal("200.00")
    assert totals.amount_financed == Decimal("400.00")


def test_half_cents_round_up_and_total_equals_sum_of_lines():
    """premium 10.03 * 0.20 = 2.006 -> must round HALF_UP to 2.01, not truncate to 2.00.

    Also guards the invariant that the total is the sum of the per-line figures
    the customer sees, i.e. rounding happens per policy before summing.
    """
    policies = [(Decimal("10.03"), Decimal("0")), (Decimal("10.03"), Decimal("0"))]

    totals = compute_totals(policies, RATE)

    assert policy_downpayment(Decimal("10.03"), Decimal("0"), RATE) == Decimal("2.01")
    assert totals.total_downpayment == Decimal("4.02")
    assert totals.amount_financed == Decimal("16.04")
    assert totals.total_downpayment + totals.amount_financed == totals.total_amount

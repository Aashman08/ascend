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


def test_schedule_splits_principal_with_remainder_on_final_installment():
    """800 over 100 days is three 30-day steps: 266.67, 266.67, 266.66; interest 1% each."""
    from datetime import date

    from app.pricing import build_schedule

    rows = build_schedule(Decimal("200"), Decimal("800"), date(2026, 1, 1), date(2026, 4, 11))
    assert [(r.number, r.kind) for r in rows] == [
        (0, "downpayment"), (1, "installment"), (2, "installment"), (3, "installment")
    ]
    assert (rows[0].principal, rows[0].interest, rows[0].due_date) == (
        Decimal("200.00"), Decimal("0.00"), date(2026, 1, 1)
    )
    assert [r.principal for r in rows[1:]] == [Decimal("266.67"), Decimal("266.67"), Decimal("266.66")]
    assert sum(r.principal for r in rows[1:]) == Decimal("800.00")
    assert [r.interest for r in rows[1:]] == [Decimal("2.67")] * 3
    assert [r.due_date for r in rows[1:]] == [date(2026, 1, 31), date(2026, 3, 2), date(2026, 4, 1)]


def test_schedule_never_has_fewer_than_one_installment_nor_a_date_past_payoff():
    from datetime import date

    from app.pricing import build_schedule

    rows = build_schedule(Decimal("50"), Decimal("100"), date(2026, 1, 1), date(2026, 1, 10))
    assert [r.number for r in rows] == [0, 1]
    assert rows[1].principal == Decimal("100.00")
    assert rows[1].due_date == date(2026, 1, 10)

"""Money math for finance terms. The single owner of every formula in the system.

Pure functions, no I/O, no configuration. Everything is Decimal; floats are
never used because binary floating point cannot represent most cent amounts
exactly.

Rounding rule: each policy's downpayment is rounded to the cent (ROUND_HALF_UP,
the convention customers expect on an invoice) before summing, so the total is
always exactly the sum of the per-policy figures shown in the response.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

CENT = Decimal("0.01")
DOWNPAYMENT_RATE = Decimal("0.20")
MAX_AMOUNT = Decimal("9999999999.99")

PolicyAmounts = tuple[Decimal, Decimal]  # (premium, tax_fee)


def to_cents(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def policy_downpayment(
    premium: Decimal, tax_fee: Decimal, rate: Decimal = DOWNPAYMENT_RATE
) -> Decimal:
    """Spec: downpayment = (premium * rate) + tax_fee, with rate = 0.20."""
    return to_cents(premium * rate) + to_cents(tax_fee)


def validate_policy_downpayment(premium: Decimal, tax_fee: Decimal) -> None:
    """Ensure one calculated downpayment fits the database amount column."""
    if policy_downpayment(premium, tax_fee) > MAX_AMOUNT:
        raise ValueError(f"Policy downpayment must not exceed {MAX_AMOUNT}")


def total_amount(policies: Iterable[PolicyAmounts]) -> Decimal:
    """Sum of (premium + tax_fee) across policies."""
    return to_cents(sum((to_cents(p) + to_cents(t) for p, t in policies), Decimal("0")))


def validate_total_downpayment(policies: Iterable[PolicyAmounts]) -> None:
    """Ensure the calculated total downpayment fits the database amount column."""
    total = sum((policy_downpayment(p, t) for p, t in policies), Decimal("0"))
    if total > MAX_AMOUNT:
        raise ValueError(f"Total downpayment must not exceed {MAX_AMOUNT}")


def amount_financed(total: Decimal, total_downpayment: Decimal) -> Decimal:
    """Spec: amount_financed = total amount to pay for each policy - total downpayment."""
    return to_cents(total - total_downpayment)


@dataclass(frozen=True)
class TermsTotals:
    total_amount: Decimal
    total_downpayment: Decimal
    amount_financed: Decimal


def compute_totals(policies: list[PolicyAmounts], rate: Decimal = DOWNPAYMENT_RATE) -> TermsTotals:
    """Everything the create path needs, computed once from inputs and rate."""
    total = total_amount(policies)
    total_down = to_cents(sum((policy_downpayment(p, t, rate) for p, t in policies), Decimal("0")))
    if total_down > MAX_AMOUNT:
        raise ValueError(f"Total downpayment must not exceed {MAX_AMOUNT}")
    return TermsTotals(
        total_amount=total,
        total_downpayment=total_down,
        amount_financed=amount_financed(total, total_down),
    )

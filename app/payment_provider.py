"""Mock payment provider.

Stands in for the checkout integration that will eventually charge the customer.
The only behaviour it has is the one the ledger needs to handle: a charge is
either approved or declined. A reference beginning with ``decline`` is declined,
so tests and manual checks can exercise the failure path deterministically.
"""

import uuid
from dataclasses import dataclass
from decimal import Decimal

DECLINE_PREFIX = "decline"


@dataclass(frozen=True)
class ChargeResult:
    approved: bool
    provider_reference: str
    reason: str | None = None


def charge(amount: Decimal, reference: str | None) -> ChargeResult:
    """Attempt to collect ``amount``; never raises, always reports an outcome."""
    provider_reference = f"mock_{uuid.uuid4().hex[:12]}"
    if reference is not None and reference.lower().startswith(DECLINE_PREFIX):
        return ChargeResult(False, provider_reference, reason="declined_by_provider")
    return ChargeResult(True, provider_reference)

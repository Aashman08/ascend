"""Conversions from database entities to API response models."""

from app.models import FinanceTerms
from app.pricing import amount_financed, total_amount
from app.schemas import FinanceTermsResponse, PolicyResponse


def finance_terms_response(terms: FinanceTerms) -> FinanceTermsResponse:
    """Build the public response for a finance-terms database record."""
    policies = [PolicyResponse.model_validate(policy) for policy in terms.policies]
    total = total_amount((policy.premium, policy.tax_fee) for policy in policies)
    return FinanceTermsResponse(
        id=terms.id,
        status=terms.status,
        due_date=terms.due_date,
        total_downpayment=terms.total_downpayment,
        total_amount=total,
        amount_financed=amount_financed(total, terms.total_downpayment),
        agreed_at=terms.agreed_at,
        created_at=terms.created_at,
        updated_at=terms.updated_at,
        policies=policies,
    )

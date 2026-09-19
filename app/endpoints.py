"""HTTP endpoint definitions."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Header, Query, Request, Response, status

from app.client import create_finance_terms_client
from app.schemas import (
    AuditListResponse,
    FinanceTermsCreate,
    FinanceTermsFilters,
    FinanceTermsListResponse,
    FinanceTermsResponse,
    ErrorResponse,
)

router = APIRouter()
ERROR_404 = {"model": ErrorResponse, "description": "Resource not found."}
ERROR_409 = {"model": ErrorResponse, "description": "Terms have expired and cannot be agreed."}
ERROR_409_IDEMPOTENCY = {
    "model": ErrorResponse,
    "description": "Idempotency-Key was already used with a different request body.",
}
IDEMPOTENCY_KEY_HEADER = "Idempotency-Key"
IDEMPOTENT_REPLAYED_HEADER = "Idempotent-Replayed"
ERROR_500 = {"model": ErrorResponse, "description": "Unexpected server error."}


@router.post(
    "/finance-terms",
    tags=["Finance Terms"],
    status_code=status.HTTP_201_CREATED,
    response_model=FinanceTermsResponse,
    responses={409: ERROR_409_IDEMPOTENCY, 500: ERROR_500},
)
def create_finance_terms(
    payload: FinanceTermsCreate,
    request: Request,
    response: Response,
    idempotency_key: Annotated[
        str | None,
        Header(
            alias=IDEMPOTENCY_KEY_HEADER,
            min_length=1,
            max_length=200,
            description=(
                "Optional client-generated key, reused on retries. A repeated key with the "
                "same body returns the original terms with an Idempotent-Replayed: true header."
            ),
        ),
    ] = None,
) -> FinanceTermsResponse:
    with create_finance_terms_client(request.state.request_id) as client:
        result = client.create_finance_terms(payload, idempotency_key)
    if result.replayed:
        response.headers[IDEMPOTENT_REPLAYED_HEADER] = "true"
    return result.terms


@router.get(
    "/finance-terms",
    tags=["Finance Terms"],
    response_model=FinanceTermsListResponse,
    responses={500: ERROR_500},
)
def list_finance_terms(
    filters: Annotated[FinanceTermsFilters, Query()], request: Request
) -> FinanceTermsListResponse:
    with create_finance_terms_client(request.state.request_id) as client:
        return client.list_finance_terms(filters)


@router.get(
    "/finance-terms/{terms_id}",
    tags=["Finance Terms"],
    response_model=FinanceTermsResponse,
    responses={404: ERROR_404, 500: ERROR_500},
)
def get_finance_terms(terms_id: uuid.UUID, request: Request) -> FinanceTermsResponse:
    with create_finance_terms_client(request.state.request_id) as client:
        return client.get_finance_terms(terms_id)


@router.post(
    "/finance-terms/{terms_id}/agree",
    tags=["Finance Terms"],
    response_model=FinanceTermsResponse,
    responses={404: ERROR_404, 409: ERROR_409, 500: ERROR_500},
)
def agree_finance_terms(terms_id: uuid.UUID, request: Request) -> FinanceTermsResponse:
    with create_finance_terms_client(request.state.request_id) as client:
        return client.agree_finance_terms(terms_id)


@router.get(
    "/audit",
    tags=["Audit"],
    response_model=AuditListResponse,
    responses={500: ERROR_500},
)
def list_audit_events(
    request: Request,
    finance_terms_id: uuid.UUID | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> AuditListResponse:
    with create_finance_terms_client(request.state.request_id) as client:
        return client.list_audit_events(finance_terms_id, limit=limit, offset=offset)


@router.get("/health", tags=["Health"], summary="Liveness check")
def health() -> dict[str, str]:
    return {"status": "ok"}

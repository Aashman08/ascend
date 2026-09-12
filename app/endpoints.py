"""HTTP endpoint definitions."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Query, Request, status

from app.client import create_finance_terms_client
from app.schemas import (
    AuditListResponse,
    FinanceTermsCreate,
    FinanceTermsFilters,
    FinanceTermsListResponse,
    FinanceTermsResponse,
)

router = APIRouter()


@router.post(
    "/finance-terms",
    tags=["Finance Terms"],
    status_code=status.HTTP_201_CREATED,
    response_model=FinanceTermsResponse,
)
def create_finance_terms(payload: FinanceTermsCreate, request: Request) -> FinanceTermsResponse:
    with create_finance_terms_client(request.state.request_id) as client:
        return client.create_finance_terms(payload)


@router.get("/finance-terms", tags=["Finance Terms"], response_model=FinanceTermsListResponse)
def list_finance_terms(
    filters: Annotated[FinanceTermsFilters, Query()], request: Request
) -> FinanceTermsListResponse:
    with create_finance_terms_client(request.state.request_id) as client:
        return client.list_finance_terms(filters)


@router.get(
    "/finance-terms/{terms_id}", tags=["Finance Terms"], response_model=FinanceTermsResponse
)
def get_finance_terms(terms_id: uuid.UUID, request: Request) -> FinanceTermsResponse:
    with create_finance_terms_client(request.state.request_id) as client:
        return client.get_finance_terms(terms_id)


@router.post(
    "/finance-terms/{terms_id}/agree", tags=["Finance Terms"], response_model=FinanceTermsResponse
)
def agree_finance_terms(terms_id: uuid.UUID, request: Request) -> FinanceTermsResponse:
    with create_finance_terms_client(request.state.request_id) as client:
        return client.agree_finance_terms(terms_id)


@router.get(
    "/finance-terms/{terms_id}/audit", tags=["Finance Terms"], response_model=AuditListResponse
)
def list_audit_events(
    terms_id: uuid.UUID,
    request: Request,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> AuditListResponse:
    with create_finance_terms_client(request.state.request_id) as client:
        return client.list_audit_events(terms_id, limit=limit, offset=offset)


@router.get("/health", tags=["Health"], summary="Liveness check")
def health() -> dict[str, str]:
    return {"status": "ok"}

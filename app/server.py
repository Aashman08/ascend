"""FastAPI application declaration and registration."""

from fastapi import FastAPI

from app.endpoints import router
from app.errors import APIError
from app.exception_handlers import api_error_response
from app.database import lifespan
from app.middleware import request_id_middleware

app = FastAPI(
    title="Ascend Checkout API",
    version="0.1.0",
    description=(
        "Create finance terms for insurance policies, agree to them on behalf of a "
        "customer, and list them with filtering and sorting. Amounts are decimal "
        'strings (e.g. "200.00"); dates are ISO 8601.'
    ),
    lifespan=lifespan,
    exception_handlers={
        APIError: api_error_response,
        Exception: api_error_response,
    },
)

app.middleware("http")(request_id_middleware)
app.include_router(router)

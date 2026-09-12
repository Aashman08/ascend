"""FastAPI exception handlers for consistent HTTP error responses."""

import logging

from fastapi import Request
from fastapi.responses import JSONResponse

from app.errors import APIError
from app.middleware import REQUEST_ID_HEADER

logger = logging.getLogger(__name__)


async def api_error_response(request: Request, exc: Exception) -> JSONResponse:
    """Render typed application errors; log and sanitize unexpected failures."""
    request_id = getattr(request.state, "request_id", "")
    if isinstance(exc, APIError):
        error = exc
    else:
        logger.error("Unexpected server error request_id=%s", request_id, exc_info=exc)
        error = APIError()
    return JSONResponse(
        status_code=error.status_code,
        headers={REQUEST_ID_HEADER: request_id},
        content={
            "error": {
                "type": error.error_type,
                "message": error.message,
                "request_id": request_id,
            }
        },
    )

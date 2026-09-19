"""Application exceptions. HTTP response handling lives in exception_handlers.py."""

from uuid import UUID


class APIError(Exception):
    status_code = 500
    error_type = "internal_error"
    message = "An unexpected error occurred. Quote the request ID when contacting support."

    def __init__(self, message: str | None = None) -> None:
        self.message = message if message is not None else self.message
        super().__init__(self.message)


class NotFoundError(APIError):
    status_code = 404
    error_type = "not_found"
    message = "The requested resource was not found."


class InvalidStateError(APIError):
    status_code = 409
    error_type = "invalid_state"
    message = "The resource's current state does not allow this action."


class FinanceTermsNotFoundError(NotFoundError):
    def __init__(self, terms_id: UUID) -> None:
        super().__init__(f"No finance terms found with id '{terms_id}'.")


class TermsExpiredError(InvalidStateError):
    message = "These terms have expired. Create new terms with a current due date."


class IdempotencyConflictError(InvalidStateError):
    error_type = "idempotency_conflict"

    def __init__(self, key: str) -> None:
        super().__init__(
            f"Idempotency-Key '{key}' was already used with a different request body. "
            "Retry with the original body or use a new key."
        )

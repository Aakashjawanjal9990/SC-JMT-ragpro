"""Retrieval failure types, kept distinct so the query backend can map them onto the
shared error envelope ({code, message, retryable}). An empty result is not an error:
retrieval returns an empty sequence.

Suggested mapping in the backend's /v1/query handler:

    FilterError              422  VALIDATION_ERROR      retryable=False
    RetrievalTimeoutError    503  SERVICE_UNAVAILABLE   retryable=True
    RetrievalFailure         503  SERVICE_UNAVAILABLE   retryable=True
"""


class RetrievalError(Exception):
    code = "RETRIEVAL_ERROR"
    status_code = 500
    retryable = False


class FilterError(RetrievalError):
    """A filter the caller sent cannot be applied. Never silently ignored."""

    code = "VALIDATION_ERROR"
    status_code = 422
    retryable = False

    def __init__(self, field: str, message: str) -> None:
        super().__init__(f"{field}: {message}")
        self.field = field
        # Fixed, client-safe text; never echoes the submitted value.
        self.message = message


class UnsupportedFilterError(FilterError):
    def __init__(self, field: str) -> None:
        super().__init__(field, "This filter is not supported.")


class InvalidFilterValueError(FilterError):
    def __init__(self, field: str) -> None:
        super().__init__(field, "This filter has an invalid value.")


class RetrievalTimeoutError(RetrievalError):
    """Retrieval or one of its dependencies (vector DB, models) exceeded its deadline."""

    code = "RETRIEVAL_TIMEOUT"
    status_code = 503
    retryable = True


class RetrievalFailure(RetrievalError):
    """Retrieval could not complete: a dependency failed or returned inconsistent data."""

    code = "RETRIEVAL_FAILED"
    status_code = 503
    retryable = True

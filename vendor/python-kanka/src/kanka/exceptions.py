"""Kanka API exception classes.

This module defines all custom exceptions that can be raised by the
Kanka API client. These exceptions provide specific error handling for
different API error scenarios.

Exception Hierarchy:
    KankaException: Base exception for all Kanka errors
    ├── NotFoundError: Resource not found (404)
    ├── ValidationError: Invalid request data (422)
    ├── RateLimitError: Rate limit exceeded (429)
    ├── AuthenticationError: Invalid authentication (401)
    └── ForbiddenError: Access forbidden (403)

Example:
    >>> try:
    ...     character = client.characters.get(999999)
    ... except NotFoundError:
    ...     print("Character not found")
    ... except KankaException as e:
    ...     print(f"API error: {e}")
"""


class KankaException(Exception):
    """Base exception for all Kanka API errors."""

    pass


class NotFoundError(KankaException):
    """Raised when a requested resource is not found (HTTP 404)."""

    pass


class ValidationError(KankaException):
    """Raised when request data fails validation (HTTP 422)."""

    pass


class RateLimitError(KankaException):
    """Raised when API rate limit is exceeded (HTTP 429)."""

    pass


class AuthenticationError(KankaException):
    """Raised when authentication fails (HTTP 401)."""

    pass


class ForbiddenError(KankaException):
    """Raised when access to a resource is forbidden (HTTP 403)."""

    pass

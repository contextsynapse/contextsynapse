"""AIContextDB SDK exceptions."""


class AIContextDBError(Exception):
    """Base exception for AIContextDB SDK."""

    def __init__(self, message: str, status_code: int = 0):
        super().__init__(message)
        self.status_code = status_code


class AuthError(AIContextDBError):
    """Authentication or authorization error."""
    pass


class NotFoundError(AIContextDBError):
    """Resource not found."""
    pass


# Backward compatibility
QGraphError = AIContextDBError

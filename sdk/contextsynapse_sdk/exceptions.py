"""ContextSynapse SDK exceptions."""


class ContextSynapseError(Exception):
    """Base exception for ContextSynapse SDK."""

    def __init__(self, message: str, status_code: int = 0):
        super().__init__(message)
        self.status_code = status_code


class AuthError(ContextSynapseError):
    """Authentication or authorization error."""
    pass


class NotFoundError(ContextSynapseError):
    """Resource not found."""
    pass


# Backward compatibility aliases
AIContextDBError = ContextSynapseError
QGraphError = ContextSynapseError

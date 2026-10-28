"""Stable API errors.

401 missing/expired/revoked session, 403 insufficient membership or role,
409 revision/conflict, 429 throttled, 503 a dependency is unavailable.
"""
from __future__ import annotations


class Problem(Exception):
    def __init__(self, status: int, code: str, message: str, **extra):
        super().__init__(f"{status} {code}")
        self.status = status
        self.code = code
        self.message = message
        self.extra = extra

    def body(self) -> dict:
        return {"error": {"code": self.code, "message": self.message, **self.extra}}


def unauthenticated(code: str = "session_invalid",
                    message: str = "Sign in again.") -> Problem:
    return Problem(401, code, message)


def forbidden(code: str, message: str) -> Problem:
    return Problem(403, code, message)


def conflict(code: str, message: str, **extra) -> Problem:
    return Problem(409, code, message, **extra)


def unavailable(code: str = "dependency_unavailable",
                message: str = "The team server cannot reach its database.") -> Problem:
    return Problem(503, code, message)

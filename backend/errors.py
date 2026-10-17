"""One exception for every client-visible failure; app.py turns it into the JSON error body."""


class ApiError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status, self.message = status, message

"""Failures from the approval vault. Callers branch on `code`."""


class ApprovalError(Exception):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)

from __future__ import annotations


class APIError(Exception):
    def __init__(
        self,
        message: str,
        status_code: int = 400,
        *,
        code: str = "api_error",
        category: str = "application",
        retryable: bool = False,
        details: dict | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.code = code
        self.category = category
        self.retryable = retryable
        self.details = dict(details or {})


class QueueError(APIError):
    def __init__(
        self,
        message: str,
        status_code: int = 503,
        *,
        code: str = "queue_error",
        retryable: bool = True,
        details: dict | None = None,
    ) -> None:
        super().__init__(
            message,
            status_code=status_code,
            code=code,
            category="queue",
            retryable=retryable,
            details=details,
        )


class ConfigError(APIError):
    def __init__(
        self,
        message: str,
        *,
        code: str = "config_error",
        details: dict | None = None,
    ) -> None:
        super().__init__(
            message,
            status_code=500,
            code=code,
            category="config",
            retryable=False,
            details=details,
        )


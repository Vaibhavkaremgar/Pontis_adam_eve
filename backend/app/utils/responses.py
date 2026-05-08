from __future__ import annotations


def success_response(data):
    return {"success": True, "data": data, "error": None}


def error_response(
    message: str,
    *,
    code: str = "api_error",
    category: str = "application",
    retryable: bool = False,
    details: dict | None = None,
    request_id: str = "",
):
    return {
        "success": False,
        "data": None,
        "error": message,
        "errorCode": code,
        "errorCategory": category,
        "retryable": bool(retryable),
        "errorDetails": dict(details or {}),
        "requestId": request_id,
    }


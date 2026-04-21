from __future__ import annotations


def success_response(data):
    return {"success": True, "data": data, "error": None}


def error_response(message: str):
    return {"success": False, "data": None, "error": message}


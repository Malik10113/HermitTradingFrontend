"""Stable API error shape: {code, message, details}."""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException


class ApiError(HTTPException):
    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        details: Any = None,
    ) -> None:
        super().__init__(
            status_code=status_code,
            detail={"code": code, "message": message, "details": details},
        )


def not_found(what: str, ident: str) -> ApiError:
    return ApiError(404, "not_found", f"{what} not found", {"id": ident})


def bad_request(code: str, message: str, details: Any = None) -> ApiError:
    return ApiError(422, code, message, details)


def unavailable(code: str, message: str) -> ApiError:
    return ApiError(503, code, message, None)

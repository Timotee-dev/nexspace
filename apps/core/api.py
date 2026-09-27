"""Consistent API error envelope: {"error": {"code", "message", "fields"}}."""
from rest_framework import status
from rest_framework.exceptions import ValidationError
from rest_framework.views import exception_handler as drf_exception_handler


def exception_handler(exc, context):
    response = drf_exception_handler(exc, context)
    if response is None:
        return None
    if isinstance(exc, ValidationError):
        fields = response.data if isinstance(response.data, dict) else {"non_field_errors": response.data}
        response.data = {"error": {"code": "invalid", "message": "Check the highlighted fields.", "fields": fields}}
        return response
    detail = response.data.get("detail", "") if isinstance(response.data, dict) else str(response.data)
    code = getattr(getattr(exc, "detail", None), "code", None) or getattr(exc, "default_code", "error")
    if response.status_code == status.HTTP_429_TOO_MANY_REQUESTS:
        code = "throttled"
    response.data = {"error": {"code": str(code), "message": str(detail), "fields": {}}}
    return response

"""API error responses are always JSON with the same shape: {"error": ..., "details": ...}."""
import logging

from rest_framework import exceptions, status
from rest_framework.response import Response
from rest_framework.views import exception_handler as drf_exception_handler

log = logging.getLogger("fuel.api")


def api_exception_handler(exc, context):
    response = drf_exception_handler(exc, context)
    if response is None:
        # Unexpected error: log the traceback, never send it to the client.
        view = context.get("view")
        log.exception("Unhandled error in %s", view.__class__.__name__ if view else "API", exc_info=exc)
        return Response(
            {"error": "Internal server error", "details": "Something went wrong on our side. Please try again."},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )
    if isinstance(exc, exceptions.Throttled):
        wait = f" Try again in {exc.wait:.0f} seconds." if exc.wait else ""
        response.data = {"error": "Too many requests", "details": f"Rate limit reached.{wait}"}
    elif isinstance(exc, exceptions.APIException) and "error" not in (response.data or {}):
        response.data = {"error": response.status_text, "details": response.data.get("detail", response.data)
                         if isinstance(response.data, dict) else response.data}
    return response

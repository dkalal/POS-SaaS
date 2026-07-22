import logging

from django.http import HttpResponse, JsonResponse
from django.template.loader import render_to_string


logger = logging.getLogger("pos_saas.security")


def _wants_json(request):
    return request.path.startswith("/api/") or "application/json" in request.headers.get("Accept", "")


def _error(request, *, status, title, message):
    if _wants_json(request):
        return JsonResponse({"error": message}, status=status)
    # Avoid request context processors here: an error page must still render when
    # a database-backed context processor is part of the original failure.
    content = render_to_string(
        f"errors/{status}.html", {"title": title, "message": message}, request=None
    )
    return HttpResponse(content, status=status, content_type="text/html; charset=utf-8")


def bad_request(request, exception=None):
    return _error(request, status=400, title="Invalid request", message="The request could not be processed.")


def permission_denied(request, exception=None):
    return _error(request, status=403, title="Access denied", message="You do not have permission to perform this action.")


def page_not_found(request, exception=None):
    return _error(request, status=404, title="Page not found", message="The requested page could not be found.")


def server_error(request):
    return _error(request, status=500, title="Something went wrong", message="An unexpected error occurred. Please try again.")


def csrf_failure(request, reason=""):
    logger.warning("csrf_rejected path=%s", request.path)
    return _error(
        request,
        status=403,
        title="Request expired",
        message="This form is no longer valid. Refresh the page and try again.",
    )

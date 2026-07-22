import logging

from django.core.cache import cache
from django.db import connections
from django.http import JsonResponse
from django.views.decorators.http import require_GET


logger = logging.getLogger("pos_saas.health")


@require_GET
def liveness(request):
    """A process-only probe that stays independent from upstream services."""
    return JsonResponse({"status": "ok"})


@require_GET
def readiness(request):
    """Verify dependencies required to serve tenant traffic safely."""
    try:
        with connections["default"].cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
        cache.get("pos-saas-readiness-probe")
    except Exception:
        logger.exception("readiness_check_failed")
        return JsonResponse({"status": "unavailable"}, status=503)
    return JsonResponse({"status": "ok"})

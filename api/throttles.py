from rest_framework.throttling import SimpleRateThrottle


class ApiKeyRateThrottle(SimpleRateThrottle):
    scope = "api_key"

    def get_cache_key(self, request, view):
        api_key = getattr(request, "auth", None)
        if api_key is None:
            return None
        return f"throttle_api_key_{api_key.pk}"


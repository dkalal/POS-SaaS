from dataclasses import dataclass
from hashlib import sha256

from django.utils import timezone
from rest_framework.authentication import BaseAuthentication
from rest_framework.exceptions import AuthenticationFailed

from api.models import APIKey


@dataclass(frozen=True)
class APIKeyPrincipal:
    api_key: APIKey

    @property
    def is_authenticated(self):
        return True

    @property
    def is_active(self):
        return self.api_key.is_active

    @property
    def username(self):
        return self.api_key.label


class APIKeyAuthentication(BaseAuthentication):
    keyword = "Api-Key"

    def authenticate(self, request):
        raw_key = self._extract_key(request)
        if not raw_key:
            return None

        key_hash = sha256(raw_key.encode("utf-8")).hexdigest()
        try:
            api_key = APIKey.objects.select_related("tenant").get(key_hash=key_hash, is_active=True)
        except APIKey.DoesNotExist as exc:
            raise AuthenticationFailed("Invalid or revoked API key.") from exc

        api_key.last_used_at = timezone.now()
        api_key.save(update_fields=["last_used_at", "updated_at"])
        request.tenant = api_key.tenant
        return APIKeyPrincipal(api_key), api_key

    def _extract_key(self, request):
        header = request.headers.get("Authorization", "")
        if header.startswith(f"{self.keyword} "):
            return header.split(" ", 1)[1].strip()
        return request.headers.get("X-API-Key")


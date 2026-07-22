import hashlib
import ipaddress
import re
from dataclasses import dataclass

from django.conf import settings
from django.core.cache import cache


_RATE_PATTERN = re.compile(r"^(?P<count>[1-9]\d*)/(?P<amount>[1-9]\d*)?(?P<unit>[smhd])$")
_SECONDS = {"s": 1, "m": 60, "h": 3600, "d": 86400}


@dataclass(frozen=True)
class Rate:
    limit: int
    window: int


def parse_rate(value):
    match = _RATE_PATTERN.fullmatch(value.strip().lower())
    if not match:
        raise ValueError(f"Invalid rate limit: {value!r}")
    amount = int(match.group("amount") or 1)
    return Rate(limit=int(match.group("count")), window=amount * _SECONDS[match.group("unit")])


def client_ip(request):
    """Return a validated client address without blindly trusting forwarding headers."""
    remote = request.META.get("REMOTE_ADDR", "")
    trusted_proxies = set(getattr(settings, "TRUSTED_PROXY_IPS", ()))
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
    if remote in trusted_proxies and forwarded:
        chain = [part.strip() for part in forwarded.split(",") if part.strip()]
        hop_count = max(1, getattr(settings, "TRUSTED_PROXY_COUNT", 1))
        if len(chain) >= hop_count:
            remote = chain[-hop_count]
    try:
        return ipaddress.ip_address(remote).compressed
    except ValueError:
        return "unknown"


def opaque(value):
    normalized = (value or "").strip().casefold()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _key(scope, identity):
    return f"security-rate:{scope}:{opaque(identity)}"


def _rate(scope):
    return parse_rate(settings.RATE_LIMITS[scope])


def is_limited(scope, identity):
    rate = _rate(scope)
    return int(cache.get(_key(scope, identity), 0) or 0) >= rate.limit


def record(scope, identity):
    rate = _rate(scope)
    key = _key(scope, identity)
    if cache.add(key, 1, timeout=rate.window):
        return False, rate.window
    try:
        count = cache.incr(key)
    except ValueError:
        cache.set(key, 1, timeout=rate.window)
        count = 1
    return count > rate.limit, rate.window


def consume(scope, *identities):
    rate = _rate(scope)
    if any(is_limited(scope, identity) for identity in identities):
        return True, rate.window
    limited = False
    for identity in identities:
        over_limit, _ = record(scope, identity)
        limited = limited or over_limit
    return limited, rate.window


def clear(scope, identity):
    cache.delete(_key(scope, identity))

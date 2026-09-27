"""Cache-backed login attempt limiter (brute-force protection, spec Section 8)."""
from django.conf import settings
from django.core.cache import cache


def _client_ip(request) -> str:
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
    return forwarded.split(",")[0].strip() if forwarded else request.META.get("REMOTE_ADDR", "")


def _keys(request, email: str) -> list[str]:
    email = (email or "").strip().lower()
    return [f"login-fail:email:{email}", f"login-fail:ip:{_client_ip(request)}"]


def is_locked(request, email: str) -> bool:
    email_key, ip_key = _keys(request, email)
    return (
        cache.get(email_key, 0) >= settings.LOGIN_MAX_FAILURES
        or cache.get(ip_key, 0) >= settings.LOGIN_MAX_FAILURES * 4
    )


def record_failure(request, email: str) -> None:
    for key in _keys(request, email):
        cache.add(key, 0, settings.LOGIN_LOCKOUT_SECONDS)
        try:
            cache.incr(key)
        except ValueError:
            cache.set(key, 1, settings.LOGIN_LOCKOUT_SECONDS)


def clear_failures(request, email: str) -> None:
    cache.delete(_keys(request, email)[0])


def allow(key: str, limit: int, window_seconds: int) -> bool:
    """Generic fixed-window limiter. Returns False once `limit` hits occur in the window."""
    cache_key = f"rl:{key}"
    cache.add(cache_key, 0, window_seconds)
    try:
        count = cache.incr(cache_key)
    except ValueError:
        cache.set(cache_key, 1, window_seconds)
        count = 1
    return count <= limit

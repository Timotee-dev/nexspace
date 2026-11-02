"""Cache-backed limits: sign-in brute-force protection and simple per-key rate limits.

Sign-in lockout works on three counters (15 minutes each):
- this email from this address: 5 wrong passwords, then that address is locked out of that account;
- this address, any email: 20 wrong passwords (someone trying many accounts);
- this email from anywhere: 50 wrong passwords (a guessing attack spread over many addresses).
A stranger can therefore no longer lock the real owner out with 5 wrong tries from their own phone;
they would need 50, and the owner can still reset their password by email.
"""
from django.conf import settings
from django.core.cache import cache

from .net import client_ip  # noqa: F401 (re-exported)

PER_IP_ANY_EMAIL = 4      # x LOGIN_MAX_FAILURES
PER_EMAIL_ANYWHERE = 10   # x LOGIN_MAX_FAILURES


def _client_ip(request) -> str:  # kept for older imports
    return client_ip(request)


def _keys(request, email: str) -> dict:
    email = (email or "").strip().lower()[:254]
    ip = client_ip(request)
    return {
        "pair": (f"login-fail:pair:{email}:{ip}", settings.LOGIN_MAX_FAILURES),
        "ip": (f"login-fail:ip:{ip}", settings.LOGIN_MAX_FAILURES * PER_IP_ANY_EMAIL),
        "email": (f"login-fail:email:{email}", settings.LOGIN_MAX_FAILURES * PER_EMAIL_ANYWHERE),
    }


def is_locked(request, email: str) -> bool:
    return any(cache.get(key, 0) >= limit for key, limit in _keys(request, email).values())


def _incr(key, window):
    if cache.add(key, 1, window):
        return 1
    try:
        return cache.incr(key)
    except ValueError:  # expired between add and incr
        cache.set(key, 1, window)
        return 1


def record_failure(request, email: str) -> None:
    for key, _ in _keys(request, email).values():
        _incr(key, settings.LOGIN_LOCKOUT_SECONDS)


def clear_failures(request, email: str) -> None:
    cache.delete(_keys(request, email)["pair"][0])


def allow(key: str, limit: int, window_seconds: int) -> bool:
    """Generic fixed-window limiter. Returns False once `limit` hits occur in the window."""
    return _incr(f"rl:{key}", window_seconds) <= limit


def allow_ip(request, action: str, limit: int, window_seconds: int) -> bool:
    return allow(f"{action}:ip:{client_ip(request)}", limit, window_seconds)

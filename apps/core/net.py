"""Request helpers that security checks rely on.

client_ip: on Render, traffic arrives through Cloudflare. Cloudflare sets CF-Connecting-IP and
True-Client-IP to the visitor's real address and overwrites anything the visitor sent, so those are
trustworthy. X-Forwarded-For is *not*: Render appends to whatever the visitor sent, so its first entry
can be anything. Rate limits must never key on a value the visitor chooses.

On Vercel there is no Cloudflare in front, so a visitor could send their own CF-Connecting-IP. Vercel
overwrites X-Vercel-Forwarded-For / X-Real-IP with the visitor's real address, so only those are used there.
"""
import ipaddress

from django.utils.http import url_has_allowed_host_and_scheme


def _valid(ip: str) -> str:
    ip = (ip or "").strip()
    try:
        return str(ipaddress.ip_address(ip))
    except ValueError:
        return ""


def client_ip(request) -> str:
    from django.conf import settings

    meta = request.META
    if getattr(settings, "ON_VERCEL", False):
        for header in ("HTTP_X_VERCEL_FORWARDED_FOR", "HTTP_X_REAL_IP", "HTTP_X_FORWARDED_FOR"):
            ip = _valid((meta.get(header) or "").split(",")[0])
            if ip:
                return ip
        return _valid(meta.get("REMOTE_ADDR", "")) or "unknown"
    for header in ("HTTP_CF_CONNECTING_IP", "HTTP_TRUE_CLIENT_IP"):
        ip = _valid(meta.get(header, ""))
        if ip:
            return ip
    # No Cloudflare (e.g. running locally): the entry added by the nearest proxy is the last one.
    forwarded = [p for p in (meta.get("HTTP_X_FORWARDED_FOR") or "").split(",") if p.strip()]
    if forwarded:
        ip = _valid(forwarded[-1])
        if ip:
            return ip
    return _valid(meta.get("REMOTE_ADDR", "")) or "unknown"


def safe_next(request, value, fallback):
    """Only redirect to pages on this site. Blocks //evil.com, /\\evil.com, javascript: and the like."""
    if value and isinstance(value, str) and url_has_allowed_host_and_scheme(
            value, allowed_hosts={request.get_host()}, require_https=request.is_secure()):
        return value
    return fallback


def int_param(value, default=0, minimum=None, maximum=None):
    """Parse a number from user input without ever raising (junk becomes the default)."""
    try:
        number = int(str(value).strip())
    except (TypeError, ValueError):
        return default
    if minimum is not None:
        number = max(minimum, number)
    if maximum is not None:
        number = min(maximum, number)
    return number


def digits(values):
    """Keep only whole-number IDs from a submitted list (junk would otherwise crash the database query)."""
    return [v for v in values if isinstance(v, str) and v.isdigit()]

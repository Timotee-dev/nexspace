"""Limits on actions that send email, so nobody can flood someone's inbox or use up the daily
email allowance (Brevo free plan: 300 a day) and block everyone else's verification codes."""
import hashlib
from urllib.parse import urlparse

from django.conf import settings

from apps.core import ratelimit

HOUR, DAY = 60 * 60, 24 * 60 * 60
SIGNUPS_PER_IP = ((5, HOUR), (20, DAY))
RESETS_PER_IP = ((5, HOUR), (20, DAY))
RESETS_PER_EMAIL = ((3, HOUR), (6, DAY))
VERIFY_EMAILS_PER_USER = ((10, DAY),)


def _all(key, rules):
    return all(ratelimit.allow(f"{key}:{window}", limit, window) for limit, window in rules)


def signup_allowed(request) -> bool:
    return _all(f"signup:ip:{ratelimit.client_ip(request)}", SIGNUPS_PER_IP)


def reset_allowed(request, email) -> bool:
    """False when this address or this email has asked too often. The page still says "sent"
    either way, so it never reveals whether an email has an account."""
    digest = hashlib.sha256((email or "").strip().lower().encode()).hexdigest()[:32]
    ip_ok = _all(f"reset:ip:{ratelimit.client_ip(request)}", RESETS_PER_IP)
    return ip_ok and _all(f"reset:email:{digest}", RESETS_PER_EMAIL)


def verify_email_allowed(user) -> bool:
    return _all(f"verify-mail:{user.pk}", VERIFY_EMAILS_PER_USER)


def site_domain_and_https():
    """Reset links always point at SITE_URL, never at whatever Host header a request carried."""
    parsed = urlparse(settings.SITE_URL)
    return parsed.netloc or None, parsed.scheme == "https"

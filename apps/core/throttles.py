"""API rate limits keyed on the visitor's real address (see apps.core.net.client_ip).

DRF's own throttles read X-Forwarded-For, which a visitor can fake on Render to get a fresh
allowance on every request."""
from rest_framework.throttling import AnonRateThrottle as _Anon
from rest_framework.throttling import UserRateThrottle as _User

from .net import client_ip


class RealIPMixin:
    def get_ident(self, request):
        return client_ip(request)


class AnonRateThrottle(RealIPMixin, _Anon):
    pass


class UserRateThrottle(RealIPMixin, _User):
    pass


class AuthRateThrottle(RealIPMixin, _Anon):
    """Sign-in, sign-up and password reset: 10 a minute per address."""

    scope = "auth"

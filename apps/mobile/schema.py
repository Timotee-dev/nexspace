"""Tells the API docs (drf-spectacular) how the app's Bearer sign-in works."""
from drf_spectacular.extensions import OpenApiAuthenticationExtension


class DeviceTokenScheme(OpenApiAuthenticationExtension):
    target_class = "apps.mobile.authentication.DeviceTokenAuthentication"
    name = "deviceToken"

    def get_security_definition(self, auto_schema):
        return {"type": "http", "scheme": "bearer", "description": "Device key from /api/m/auth/login/"}

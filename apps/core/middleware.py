"""Security headers added to every response.

- Content-Security-Policy: the browser only runs NexSpace's own script files. Even if someone found a way
  to slip HTML into a page, their <script>, onclick= or javascript: link would not run.
- Permissions-Policy: NexSpace never uses the camera, microphone, location or payments, so pages can't.
- Cache-Control on signed-in pages: browsers and shared computers don't keep copies of private pages.
"""
from django.conf import settings

STORAGE_HOSTS = "https://res.cloudinary.com https://*.supabase.co"

CSP = "; ".join([
    "default-src 'self'",
    "script-src 'self'",
    "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com",  # style="" attributes in templates
    "font-src 'self' https://fonts.gstatic.com",
    f"img-src 'self' data: blob: {STORAGE_HOSTS}",
    f"media-src 'self' blob: {STORAGE_HOSTS}",
    "connect-src 'self'",
    "worker-src 'self'",
    "manifest-src 'self'",
    "frame-src 'none'",
    "frame-ancestors 'none'",
    "object-src 'none'",
    "base-uri 'self'",
    "form-action 'self'",
])

PERMISSIONS = ("camera=(), microphone=(), geolocation=(), payment=(), usb=(), "
               "interest-cohort=(), browsing-topics=()")


class SecurityHeadersMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        if not request.path.startswith("/django-admin/"):  # Django's own admin manages itself
            header = "Content-Security-Policy-Report-Only" if settings.CSP_REPORT_ONLY else "Content-Security-Policy"
            response.headers.setdefault(header, CSP)
        response.headers.setdefault("Permissions-Policy", PERMISSIONS)
        if not request.path.startswith(("/static/", "/media/")):  # emails and the app may show our images
            response.headers.setdefault("Cross-Origin-Resource-Policy", "same-origin")
        user = getattr(request, "user", None)
        if (user is not None and user.is_authenticated and "Cache-Control" not in response.headers
                and not request.path.startswith(("/static/", "/manifest.webmanifest", "/sw.js"))):
            response.headers["Cache-Control"] = "private, no-cache"
        return response

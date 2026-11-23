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
    "connect-src 'self' https://api.cloudinary.com",  # direct uploads go straight to Cloudinary
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


class DirectUploadMiddleware:
    """Turns files the browser sent straight to Cloudinary back into normal uploads (see core/direct_uploads.py).

    Forms then see them in request.FILES exactly as if they had been sent with the form, so every check and
    view works unchanged. The app's API (/api/) is left alone: the Android app still sends files directly.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if self._wants(request):
            from apps.core import direct_uploads

            keys = direct_uploads.fields_in(request.POST)
            if keys:
                try:
                    for key in keys:
                        for token in request.POST.getlist(key)[:10]:
                            request.FILES.appendlist(direct_uploads.target_field(key),
                                                     direct_uploads.receive(request.user, token))
                except direct_uploads.DirectUploadError as exc:
                    return self._failed(request, str(exc))
        return self.get_response(request)

    @staticmethod
    def _wants(request) -> bool:
        from apps.core import direct_uploads

        content_type = request.META.get("CONTENT_TYPE", "")
        return (request.method == "POST" and direct_uploads.enabled() and not request.path.startswith("/api/")
                and content_type.startswith(("multipart/form-data", "application/x-www-form-urlencoded"))
                and getattr(request, "user", None) is not None and request.user.is_authenticated)

    @staticmethod
    def _failed(request, message):
        from django.contrib import messages
        from django.http import HttpResponseRedirect, JsonResponse

        from apps.core.net import safe_next

        if "application/json" in request.headers.get("Accept", ""):
            return JsonResponse({"error": {"message": message}}, status=400)
        messages.error(request, message)
        return HttpResponseRedirect(safe_next(request, request.META.get("HTTP_REFERER"), "/"))

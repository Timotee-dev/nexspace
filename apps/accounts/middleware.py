from django.shortcuts import redirect
from django.urls import reverse

ALLOWED_PREFIXES = (
    "/onboarding/", "/logout/", "/verify-email/", "/password-reset/",
    "/static/", "/media/", "/api/", "/django-admin/", "/privacy/", "/guidelines/", "/internal/", "/email/",
)


class OnboardingMiddleware:
    """Send signed-in users who haven't finished onboarding back to it."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, "user", None)
        if (
            user is not None
            and user.is_authenticated
            and not request.path.startswith(ALLOWED_PREFIXES)
        ):
            if user.department_id is None:
                return redirect(reverse("accounts:onboarding") + "?step=department")
            if not user.onboarding_completed:
                return redirect(reverse("accounts:onboarding"))
        return self.get_response(request)


class LastSeenMiddleware:
    """Record when a user was last active (at most one write every 5 minutes) for analytics."""

    INTERVAL = 300

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        user = getattr(request, "user", None)
        if user is not None and user.is_authenticated:
            from django.core.cache import cache
            from django.utils import timezone

            if cache.add(f"last-seen:{user.pk}", 1, self.INTERVAL):
                from .models import User

                User.objects.filter(pk=user.pk).update(last_seen_at=timezone.now())
        return response

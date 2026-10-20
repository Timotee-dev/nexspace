"""Verified-email gate (spec Section 8).

Unverified users may read and edit their own profile, but every community write
(post, comment, vote, upload, rate, report, poll/event/opportunity creation)
must use one of these. Enforced server-side; the UI hiding buttons is not enough.
"""
from functools import wraps

from django.contrib import messages
from django.shortcuts import redirect
from rest_framework.permissions import SAFE_METHODS, BasePermission


class IsVerifiedOrReadOnly(BasePermission):
    message = "Verify your email address to do this."
    code = "email_unverified"

    def has_permission(self, request, view):
        if request.method in SAFE_METHODS:
            return bool(request.user and request.user.is_authenticated)
        return bool(request.user and request.user.is_authenticated and request.user.can_write)


def verified_required(view_func):
    """Template-view equivalent: blocks non-GET requests from unverified users."""

    @wraps(view_func)
    def _wrapped(request, *args, **kwargs):
        if request.method not in SAFE_METHODS and not request.user.can_write:
            messages.error(request, "Verify your email address to do this.")
            return redirect(request.META.get("HTTP_REFERER") or "core:home")
        return view_func(request, *args, **kwargs)

    return _wrapped

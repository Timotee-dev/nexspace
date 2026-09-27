from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from . import services
from .models import Category, Notification

PAGE = 30


@login_required
def list_view(request):
    category = request.GET.get("category", "")
    qs = Notification.objects.filter(recipient=request.user).select_related("actor__profile")
    if category in Category.values:
        qs = qs.filter(category=category)
    try:
        page = max(int(request.GET.get("page", 1)), 1)
    except ValueError:
        page = 1
    items = list(qs[(page - 1) * PAGE: page * PAGE + 1])
    has_more = len(items) > PAGE
    return render(request, "notifications/list.html", {
        "items": items[:PAGE], "category": category, "categories": Category.choices,
        "next_page": page + 1 if has_more else None,
    })


@login_required
def open_view(request, pk):
    item = get_object_or_404(Notification, pk=pk, recipient=request.user)
    if not item.is_read:
        services.mark_read(request.user, [item.pk])
    target = item.url if url_has_allowed_host_and_scheme(item.url, {request.get_host()}) else "/"
    return redirect(target)


@login_required
@require_POST
def mark_all_view(request):
    services.mark_read(request.user)
    messages.success(request, "All caught up.")
    return redirect(request.POST.get("next") or "notifications:list")


PREF_FIELDS = [
    (Category.SOCIAL, "Replies, mentions, new followers and upvote milestones"),
    (Category.ACADEMIC, "New course materials, past questions and exam or deadline reminders"),
    (Category.DEPARTMENT, "Announcements from your department and course reps"),
    (Category.OPPORTUNITIES, "New opportunities and reminders you set"),
]


@login_required
def preferences_view(request):
    prefs = services.prefs_for(request.user)
    if request.method == "POST":
        for category, _ in PREF_FIELDS:
            for channel in ("in_app", "push"):
                setattr(prefs, f"{category}_{channel}", f"{category}_{channel}" in request.POST)
        prefs.save()
        messages.success(request, "Notification settings saved.")
        return redirect("notifications:preferences")
    rows = [{"key": c, "label": Category(c).label, "help": h, "in_app": prefs.allows(c, "in_app"),
             "push": prefs.allows(c, "push")} for c, h in PREF_FIELDS]
    return render(request, "notifications/preferences.html", {
        "rows": rows, "section": "notifications", "push_enabled": settings.PUSH_ENABLED,
        "vapid_public_key": settings.VAPID_PUBLIC_KEY,
        "device_count": request.user.push_subscriptions.count(),
    })



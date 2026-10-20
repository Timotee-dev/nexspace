from django.db.models import Count
from django.shortcuts import render

from apps.accounts.models import User
from apps.posts.views import feed_context
from apps.social.services import followed_topic_ids, followed_user_ids
from apps.topics.models import Topic


def home_view(request):
    """Landing page for visitors; the feed for signed-in users."""
    if not request.user.is_authenticated:
        return render(request, "core/landing.html")
    context = feed_context(request)
    if request.GET.get("partial"):
        return render(request, "posts/_feed_page.html", context)
    from apps.notices.services import active_announcements_for, countdowns_for

    context["announcements"] = active_announcements_for(request.user, limit=5)
    context["countdowns"] = countdowns_for(request.user, limit=3)

    user = request.user
    steps = user.profile.completion_steps()
    followed_users = followed_user_ids(user)
    people = (
        User.objects.filter(department_id=user.department_id, is_active=True)
        .exclude(pk__in=followed_users | {user.pk})
        .select_related("profile")
        .order_by("-date_joined")
    )
    from apps.discover.services import people_you_may_know, recommended_resources

    suggested_people = people_you_may_know(user) or list(people.filter(level=user.level)[:4]) or list(people[:4])
    context["recommended_resources"] = recommended_resources(user, limit=3)
    suggested_topics = list(
        Topic.objects.filter(is_active=True)
        .exclude(pk__in=followed_topic_ids(user))
        .annotate(n=Count("posts"))
        .order_by("-n", "sort_order")[:6]
    )
    context.update({
        "steps": steps,
        "steps_done": sum(1 for s in steps if s["done"]),
        "suggested_people": suggested_people,
        "suggested_topics": suggested_topics,
    })
    return render(request, "core/home.html", context)


def error_403(request, exception=None):
    return render(request, "errors/403.html", status=403)


def error_404(request, exception=None):
    return render(request, "errors/404.html", status=404)


def error_500(request):
    return render(request, "errors/500.html", status=500)


def run_scheduled_view(request):
    """Lets a free external scheduler (e.g. cron-job.org) run the scheduled jobs.

    Disabled unless CRON_SECRET is set. Send the secret as `Authorization: Bearer <secret>`
    or `?token=<secret>`. Returns 404 for a wrong or missing secret, so the URL reveals nothing.
    """
    import hmac

    from django.conf import settings
    from django.core.cache import cache
    from django.http import Http404, JsonResponse

    secret = settings.CRON_SECRET
    given = request.headers.get("Authorization", "").removeprefix("Bearer ").strip() or request.GET.get("token", "")
    if not secret or not hmac.compare_digest(given.encode(), secret.encode()):
        raise Http404
    if not cache.add("run-scheduled-lock", 1, 300):  # skip if a previous run is still going
        return JsonResponse({"status": "already running"})
    try:
        from apps.core.scheduled import run_all

        return JsonResponse({"status": "ok", "result": run_all()})
    finally:
        cache.delete("run-scheduled-lock")

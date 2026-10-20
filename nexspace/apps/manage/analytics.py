"""Aggregate-only platform analytics for department admins (spec Section 51)."""
from datetime import timedelta

from django.db.models import Count, Q, Sum
from django.db.models.functions import TruncDate
from django.utils import timezone

from apps.accounts.models import User
from apps.moderation.models import Report
from apps.posts.models import Comment, Post, PostVote
from apps.resources.models import Resource, ResourceDownload
from apps.spaces.models import Space


def _daily(qs, field, days):
    start = timezone.localdate() - timedelta(days=days - 1)
    rows = dict(qs.filter(**{f"{field}__date__gte": start}).annotate(day=TruncDate(field))
                .values_list("day").annotate(n=Count("id")).values_list("day", "n"))
    return [{"day": start + timedelta(days=i), "n": rows.get(start + timedelta(days=i), 0)} for i in range(days)]


def overview(department, days=30):
    now = timezone.now()
    since = now - timedelta(days=days)
    users = User.objects.filter(department=department)
    posts = Post.objects.filter(department=department, is_deleted=False)
    comments = Comment.objects.filter(post__department=department, is_deleted=False)
    resources = Resource.objects.filter(course__department=department, is_removed=False)
    downloads = ResourceDownload.objects.filter(resource__course__department=department)
    reports = Report.objects.filter(department=department)
    return {
        "totals": {
            "users": users.filter(is_active=True).count(),
            "active_7": users.filter(last_seen_at__gte=now - timedelta(days=7)).count(),
            "active_30": users.filter(last_seen_at__gte=since).count(),
            "new_users": users.filter(date_joined__gte=since).count(),
            "posts": posts.count(),
            "posts_period": posts.filter(created_at__gte=since).count(),
            "comments": comments.count(),
            "comments_period": comments.filter(created_at__gte=since).count(),
            "resources": resources.count(),
            "downloads": resources.aggregate(n=Sum("download_count"))["n"] or 0,
            "votes_period": PostVote.objects.filter(post__department=department, created_at__gte=since).count(),
            "opportunities_period": posts.filter(kind=Post.Kind.OPPORTUNITY, created_at__gte=since).count(),
            "reports_open": reports.filter(status=Report.Status.OPEN).count(),
            "reports_resolved": reports.filter(resolved_at__gte=since).count(),
            "nexai_questions": _nexai_count(department, since),
            "verified_pct": round(100 * users.filter(email_verified=True).count() / max(users.count(), 1)),
        },
        "series": {
            "registrations": _daily(users, "date_joined", days),
            "posts": _daily(posts, "created_at", days),
            "comments": _daily(comments, "created_at", days),
            "downloads": _daily_downloads(downloads, days),
        },
        "popular_spaces": list(Space.objects.filter(department=department).exclude(kind=Space.Kind.COURSE)
                               .annotate(recent=Count("posts", filter=Q(posts__created_at__gte=since)))
                               .order_by("-member_count", "-recent")[:6]),
        "popular_courses": list(Space.objects.filter(department=department, kind=Space.Kind.COURSE)
                                .select_related("course")
                                .annotate(recent=Count("posts", filter=Q(posts__created_at__gte=since), distinct=True),
                                          dl=Sum("course__resources__download_count"))
                                .order_by("-member_count", "-recent")[:6]),
        "top_resources": list(resources.select_related("course").order_by("-download_count")[:6]),
        "levels": list(users.filter(is_active=True).values("level").annotate(n=Count("id")).order_by("level")),
        "days": days,
    }


def _daily_downloads(qs, days):
    start = timezone.localdate() - timedelta(days=days - 1)
    rows = dict(qs.filter(day__gte=start).values_list("day").annotate(n=Count("id")).values_list("day", "n"))
    return [{"day": start + timedelta(days=i), "n": rows.get(start + timedelta(days=i), 0)} for i in range(days)]


def _nexai_count(department, since):
    from apps.nexai.models import Usage

    return Usage.objects.filter(department=department, created_at__gte=since).count()

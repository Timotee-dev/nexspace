from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db.models import Count
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST

from apps.academics.models import Course
from apps.accounts.models import User
from apps.posts.feed import annotate_for_user
from apps.posts.models import OpportunityDetails, Post
from apps.posts.views import get_visible_post
from apps.resources.models import Resource
from apps.social.services import followed_topic_ids, followed_user_ids
from apps.spaces.models import Space
from apps.spaces.services import joined_course_ids, joined_space_ids
from apps.topics.models import Topic

from . import services
from .models import OpportunityReminder


@login_required
def explore_view(request):
    user = request.user
    snap = services.trending_for(user.department_id)
    by_id = {p.pk: p for p in annotate_for_user(Post.objects.for_viewer(user).with_related(), user)
             .filter(pk__in=snap.post_ids)}
    trending = [by_id[i] for i in snap.post_ids if i in by_id][:5]
    topics = {t.pk: t for t in Topic.objects.filter(pk__in=snap.topic_ids)}
    joined = joined_space_ids(user)
    interests = followed_topic_ids(user)
    dept_spaces = Space.objects.filter(department_id=user.department_id).exclude(kind=Space.Kind.COURSE)
    recommended = list(dept_spaces.exclude(pk__in=joined).filter(level__in=[user.level, None])
                       .order_by("-is_official", "-member_count")[:4])
    followed = followed_user_ids(user)
    return render(request, "discover/explore.html", {
        "trending": trending,
        "trending_topics": [topics[i] for i in snap.topic_ids if i in topics],
        "popular_spaces": list(dept_spaces.order_by("-member_count")[:5]),
        "recommended_spaces": recommended,
        "courses": list(Course.objects.filter(department_id=user.department_id, level=user.level, is_active=True)
                        .exclude(pk__in=joined_course_ids(user))[:6]),
        "opportunities": list(services.opportunities(user)[:4]),
        "resources": list(Resource.objects.for_viewer(user).select_related("course", "session", "uploaded_by__staff_profile")
                          .order_by("-download_count", "-created_at")[:5]),
        "people": list(User.objects.filter(department_id=user.department_id, is_active=True)
                       .exclude(pk__in=followed | {user.pk}).select_related("profile")
                       .order_by("-profile__nexscore")[:5]),
        "interest_count": len(interests),
    })


@login_required
def opportunities_view(request):
    category = request.GET.get("category") if request.GET.get("category") in OpportunityDetails.Category.values else None
    show_closed = request.GET.get("closed") == "1"
    posts = list(annotate_for_user(services.opportunities(request.user, category=category, include_closed=show_closed),
                                   request.user)[:60])
    reminders = dict(OpportunityReminder.objects.filter(user=request.user, post__in=posts)
                     .values_list("post_id", "days_before"))
    for p in posts:
        p.reminder_days = reminders.get(p.pk)
    counts = dict(services.opportunities(request.user).values_list("opportunity__category")
                  .annotate(n=Count("id")).values_list("opportunity__category", "n"))
    return render(request, "discover/opportunities.html", {
        "posts": posts, "category": category, "show_closed": show_closed,
        "categories": [(v, l, counts.get(v, 0)) for v, l in OpportunityDetails.Category.choices],
        "reminder_choices": services.REMINDER_CHOICES,
    })


@login_required
@require_POST
def reminder_view(request, pk):
    post = get_visible_post(request.user, pk)
    days = request.POST.get("days")
    if days == "off":
        services.clear_reminder(user=request.user, post=post)
        messages.success(request, "Reminder removed.")
    else:
        try:
            reminder = services.set_reminder(user=request.user, post=post, days_before=days)
            messages.success(request, f"We'll remind you on {reminder.remind_on:%d %b}.")
        except (ValidationError, ValueError, TypeError) as exc:
            messages.error(request, " ".join(getattr(exc, "messages", [str(exc)])))
    return redirect(request.POST.get("next") or "discover:opportunities")

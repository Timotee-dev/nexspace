from datetime import timedelta

from django.core.exceptions import ValidationError
from django.db.models import Count, Q
from django.utils import timezone

from apps.posts.models import Comment, Post, PostVote

from .models import OpportunityReminder, TrendingSnapshot

TRENDING_WINDOW = timedelta(hours=48)
SNAPSHOT_MAX_AGE = timedelta(hours=2)
REMINDER_CHOICES = (1, 3, 7)


def compute_trending(department_id, limit=10):
    """Engagement velocity: upvotes + 2×comments received in the last 48 hours."""
    since = timezone.now() - TRENDING_WINDOW
    votes = dict(PostVote.objects.filter(post__department_id=department_id, value=1, created_at__gte=since)
                 .values_list("post_id").annotate(n=Count("id")))
    comments = dict(Comment.objects.filter(post__department_id=department_id, created_at__gte=since, is_deleted=False)
                    .values_list("post_id").annotate(n=Count("id")))
    fresh = set(Post.objects.visible().exclude(kind="repost").filter(department_id=department_id, created_at__gte=since)
                .values_list("id", flat=True))
    scores = {}
    for pid in set(votes) | set(comments) | fresh:
        scores[pid] = votes.get(pid, 0) + 2 * comments.get(pid, 0) + (0.5 if pid in fresh else 0)
    live = set(Post.objects.visible().filter(pk__in=scores).values_list("id", flat=True))
    ranked = [pid for pid, _ in sorted(scores.items(), key=lambda kv: -kv[1]) if pid in live][:limit]
    topic_counts = (Post.topics.through.objects.filter(post_id__in=ranked or list(live)[:50])
                    .values("topic_id").annotate(n=Count("id")).order_by("-n")[:8])
    snapshot, _ = TrendingSnapshot.objects.update_or_create(
        department_id=department_id,
        defaults={"post_ids": ranked, "topic_ids": [t["topic_id"] for t in topic_counts], "computed_at": timezone.now()},
    )
    return snapshot


def trending_for(department_id):
    snap = TrendingSnapshot.objects.filter(department_id=department_id).first()
    if snap is None or snap.computed_at < timezone.now() - SNAPSHOT_MAX_AGE:
        snap = compute_trending(department_id)
    return snap


def opportunities(user, *, category=None, include_closed=False):
    qs = (Post.objects.for_viewer(user).with_related().filter(kind=Post.Kind.OPPORTUNITY)
          .select_related("opportunity"))
    today = timezone.localdate()
    if not include_closed:
        qs = qs.filter(Q(opportunity__deadline__isnull=True) | Q(opportunity__deadline__gte=today))
    if category:
        qs = qs.filter(opportunity__category=category)
    return qs.order_by("opportunity__deadline", "-created_at")


def set_reminder(*, user, post, days_before):
    if post.kind != Post.Kind.OPPORTUNITY or not getattr(post, "opportunity", None):
        raise ValidationError("Reminders are only for opportunities.")
    deadline = post.opportunity.deadline
    if deadline is None:
        raise ValidationError("This opportunity has no deadline to remind you about.")
    days_before = int(days_before)
    if days_before not in REMINDER_CHOICES:
        raise ValidationError("Choose 1, 3 or 7 days before.")
    remind_on = max(deadline - timedelta(days=days_before), timezone.localdate())
    if deadline < timezone.localdate():
        raise ValidationError("The deadline has passed.")
    reminder, _ = OpportunityReminder.objects.update_or_create(
        user=user, post=post, defaults={"days_before": days_before, "remind_on": remind_on, "sent_at": None}
    )
    return reminder


def clear_reminder(*, user, post):
    OpportunityReminder.objects.filter(user=user, post=post).delete()


def recommended_resources(user, limit=4):
    """Well-rated or popular materials from the user's courses that they haven't downloaded yet."""
    from apps.resources.models import Resource, ResourceDownload
    from apps.resources.services import search
    from apps.spaces.services import joined_course_ids

    courses = joined_course_ids(user)
    if not courses:
        return []
    seen = ResourceDownload.objects.filter(user=user).values_list("resource_id", flat=True)
    qs = (Resource.objects.for_viewer(user).filter(course_id__in=courses).exclude(pk__in=seen)
          .exclude(uploaded_by=user).select_related("course", "session"))
    return list(search(qs, sort="useful")[:limit])


def people_you_may_know(user, limit=4):
    """Classmates who share the most course Spaces with you and you don't follow yet."""
    from django.db.models import Count

    from apps.accounts.models import User
    from apps.social.services import followed_user_ids
    from apps.spaces.services import joined_space_ids

    mine = joined_space_ids(user)
    exclude = followed_user_ids(user) | {user.pk}
    return list(User.objects.filter(department_id=user.department_id, is_active=True,
                                    space_memberships__space_id__in=mine)
                .exclude(pk__in=exclude).annotate(shared=Count("space_memberships", distinct=True))
                .select_related("profile").order_by("-shared", "-profile__nexscore")[:limit])

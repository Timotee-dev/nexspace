"""Email digests: a summary of what someone missed, sent daily or weekly (their choice; weekly by default).

Sent by the scheduled job from DIGEST_HOUR (local time). Each person gets at most one digest per period
(DigestLog). Brevo's free plan allows 300 emails a day, so no more than DIGEST_DAILY_CAP digests go out
per day; anyone left over gets theirs on the next run. People with nothing new get no email at all.
"""
import logging
from datetime import timedelta

from django.conf import settings
from django.core import signing
from django.core.mail import EmailMultiAlternatives
from django.db.models import F, Q
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone

from .models import DigestLog, Notification, NotificationPreference

logger = logging.getLogger(__name__)
UNSUBSCRIBE_SALT = "nexspace.digest.unsubscribe"
PER_RUN = 60  # emails per scheduled run
CHECKS_PER_RUN = 400  # people looked at per run, so a run stays short


def unsubscribe_token(user):
    return signing.dumps({"u": user.pk}, salt=UNSUBSCRIBE_SALT)


def user_from_token(token):
    from apps.accounts.models import User

    try:
        data = signing.loads(token, salt=UNSUBSCRIBE_SALT, max_age=60 * 60 * 24 * 120)
    except signing.BadSignature:
        return None
    return User.objects.filter(pk=data.get("u")).first()


def period_for(frequency, now):
    local = timezone.localtime(now)
    if frequency == NotificationPreference.Digest.DAILY:
        return f"daily:{local:%Y-%m-%d}", timedelta(days=1)
    year, week, _ = local.isocalendar()
    return f"weekly:{year}-W{week:02d}", timedelta(days=7)


def build(user, since):
    """What to put in someone's digest, or None if there's nothing worth an email."""
    from apps.messaging.services import unread_count as unread_messages
    from apps.notices.models import AcademicEvent, Announcement
    from apps.posts.models import Post
    from apps.resources.models import Resource
    from apps.spaces.services import joined_course_ids

    now = timezone.now()
    unread = Notification.objects.filter(recipient=user, is_read=False, created_at__gte=since)
    events = list(AcademicEvent.objects.relevant_to(user).filter(starts_at__gte=now, starts_at__lte=now + timedelta(days=7))
                  .select_related("target_course").order_by("starts_at")[:5])
    announcements = list(Announcement.objects.active().relevant_to(user).filter(created_at__gte=since)
                         .order_by("-created_at")[:3])
    materials = list(Resource.objects.for_viewer(user).filter(course_id__in=joined_course_ids(user), created_at__gte=since)
                     .exclude(uploaded_by=user).select_related("course").order_by("-created_at")[:5])
    top = list(Post.objects.for_viewer(user).filter(created_at__gte=since).exclude(author=user)
               .exclude(kind=Post.Kind.REPOST).filter(Q(score__gt=0) | Q(comment_count__gt=0))
               .select_related("author").order_by(-(F("score") + F("comment_count") * 2), "-created_at")[:3])
    data = {
        "unread_count": unread.count(), "unread": list(unread.order_by("-created_at")[:5]),
        "messages": unread_messages(user), "events": events, "announcements": announcements,
        "materials": materials, "top_posts": top,
    }
    if not any([data["unread_count"], data["messages"], events, announcements, materials, top]):
        return None
    return data


def _subject(data, weekly):
    bits = []
    if data["events"]:
        first = data["events"][0]
        days = (timezone.localtime(first.starts_at).date() - timezone.localdate()).days
        bits.append(f"{first.get_kind_display().lower()} {'today' if days <= 0 else f'in {days} day' + ('s' if days > 1 else '')}")
    if data["messages"]:
        bits.append(f"{data['messages']} unread conversation{'s' if data['messages'] > 1 else ''}")
    if data["unread_count"]:
        bits.append(f"{data['unread_count']} notification{'s' if data['unread_count'] > 1 else ''}")
    head = "Your NexSpace week" if weekly else "Your NexSpace day"
    return f"{head}: {', '.join(bits[:2])}" if bits else head


def send_digest(user, frequency, now=None):
    now = now or timezone.now()
    period, span = period_for(frequency, now)
    data = build(user, now - span)
    if data is None:
        DigestLog.objects.get_or_create(user=user, period=period, defaults={"emailed": False})  # check again next period
        return False
    token = unsubscribe_token(user)
    unsubscribe = settings.SITE_URL + reverse("notifications:digest-unsubscribe", args=[token])
    context = {**data, "user": user, "site_url": settings.SITE_URL, "weekly": frequency == "weekly",
               "unsubscribe": unsubscribe, "settings_url": settings.SITE_URL + reverse("notifications:preferences")}
    message = EmailMultiAlternatives(
        subject=_subject(data, frequency == "weekly"),
        body=render_to_string("emails/digest.txt", context), to=[user.email],
        headers={"List-Unsubscribe": f"<{unsubscribe}>", "List-Unsubscribe-Post": "List-Unsubscribe=One-Click"},
    )
    message.attach_alternative(render_to_string("emails/digest.html", context), "text/html")
    message.send()
    DigestLog.objects.get_or_create(user=user, period=period, defaults={"emailed": True})
    return True


def send_due_digests(now=None):
    """Called by the scheduled job. Returns how many digests were emailed."""
    from apps.accounts.models import User

    now = now or timezone.now()
    local = timezone.localtime(now)
    if local.hour < settings.DIGEST_HOUR:
        return 0
    sent_today = DigestLog.objects.filter(sent_at__date=local.date(), emailed=True).count()
    budget = min(PER_RUN, max(settings.DIGEST_DAILY_CAP - sent_today, 0))
    if not budget:
        return 0
    sent = checked = 0
    for frequency in (NotificationPreference.Digest.DAILY, NotificationPreference.Digest.WEEKLY):
        period, _ = period_for(frequency, now)
        wants = Q(notification_prefs__digest=frequency)
        if frequency == NotificationPreference.Digest.WEEKLY:
            wants |= Q(notification_prefs__isnull=True)  # weekly is the default
        users = (User.objects.filter(wants, is_active=True, email_verified=True, department__isnull=False)
                 .exclude(digests__period=period).exclude(email__endswith="@deleted.invalid").order_by("pk"))
        for user in users.iterator():
            checked += 1
            try:
                if send_digest(user, frequency, now):
                    sent += 1
            except Exception:  # one bad address must not stop the rest
                logger.exception("Digest to user %s failed", user.pk)
            if sent >= budget or checked >= CHECKS_PER_RUN:
                return sent
    return sent

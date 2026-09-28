"""Creating notifications and delivering push. All hooks are best-effort: a failure
here must never break the action that triggered it."""
import logging

from django.conf import settings
from django.core.cache import cache
from django.db import transaction
from django.utils import timezone

from .models import Category, Notification, NotificationPreference, PushSubscription

logger = logging.getLogger(__name__)
VOTE_MILESTONES = (5, 10, 25, 50, 100, 250, 500)
UNREAD_CACHE = "notif-unread:{}"


def prefs_for(user):
    prefs, _ = NotificationPreference.objects.get_or_create(user=user)
    return prefs


def unread_count(user) -> int:
    key = UNREAD_CACHE.format(user.pk)
    count = cache.get(key)
    if count is None:
        count = Notification.objects.filter(recipient=user, is_read=False).count()
        cache.set(key, count, 60)
    return count


def _bust(user_ids):
    cache.delete_many([UNREAD_CACHE.format(uid) for uid in user_ids])


def notify(recipients, *, category, kind, text, url, actor=None, critical=False, dedupe_key=""):
    """Create notifications for many users at once, honouring each user's preferences."""
    recipients = [u for u in recipients if u and u.is_active and (actor is None or u.pk != actor.pk)]
    if not recipients:
        return []
    prefs = {p.user_id: p for p in NotificationPreference.objects.filter(user__in=recipients)}
    rows = []
    for user in {u.pk: u for u in recipients}.values():
        p = prefs.get(user.pk) or NotificationPreference(user=user)
        if not critical and not p.allows(category, "in_app"):
            continue
        wants_push = settings.PUSH_ENABLED and (critical or p.allows(category, "push"))
        rows.append(Notification(
            recipient=user, actor=actor, category=category, kind=kind, text=text[:240], url=url[:300],
            is_critical=critical, dedupe_key=dedupe_key[:120],
            push_status=Notification.PushStatus.PENDING if wants_push else Notification.PushStatus.NONE,
        ))
    created = Notification.objects.bulk_create(rows, ignore_conflicts=bool(dedupe_key))
    _bust([r.recipient_id for r in rows])
    return created


def safely(fn):
    """Run a hook after the surrounding transaction commits, and never raise."""
    def wrapper(*args, **kwargs):
        def run():
            try:
                fn(*args, **kwargs)
            except Exception:  # pragma: no cover - logged, never user-facing
                logger.exception("Notification hook %s failed", fn.__name__)
        transaction.on_commit(run)
    wrapper.__name__ = fn.__name__
    wrapper.hook = fn
    return wrapper


def _name(user, anonymous=False):
    return "Anonymous Student" if anonymous else user.full_name


# --- Hooks -------------------------------------------------------------------
@safely
def post_created(post):
    url = post.get_absolute_url()
    who = _name(post.author, post.is_anonymous)
    from apps.posts.services import can_view

    mentioned = [u for u in post.mentions.all() if can_view(u, post)]  # no pings into private Spaces
    notify(mentioned, category=Category.SOCIAL, kind=Notification.Kind.MENTION, text=f"{who} mentioned you in a post",
           url=url, actor=None if post.is_anonymous else post.author)
    if post.kind == "opportunity":
        from apps.accounts.models import User

        audience = User.objects.filter(department_id=post.department_id, is_active=True).exclude(
            pk__in=[u.pk for u in mentioned])
        notify(audience, category=Category.OPPORTUNITIES, kind=Notification.Kind.OPPORTUNITY,
               text=f"New opportunity: {post.title}", url=url, actor=post.author)


@safely
def comment_created(comment):
    post = comment.post
    url = f"{post.get_absolute_url()}#c-{comment.pk}"
    who = comment.author.full_name
    notified = {comment.author_id}
    targets = []
    if comment.parent_id and comment.parent.author_id not in notified:
        targets.append((comment.parent.author, f"{who} replied to your comment"))
    if comment.reply_to_id and comment.reply_to_id not in {t[0].pk for t in targets}:
        targets.append((comment.reply_to, f"{who} replied to you"))
    if post.author_id not in {t[0].pk for t in targets}:
        if post.kind == "question" and not comment.parent_id:
            verb = "answered your question"
        else:
            verb = "replied in your question" if post.kind == "question" else "commented on your post"
        targets.append((post.author, f"{who} {verb}"))
    for user, text in targets:
        if user.pk in notified:
            continue
        notified.add(user.pk)
        notify([user], category=Category.SOCIAL, kind=Notification.Kind.REPLY, text=text, url=url, actor=comment.author)
    from apps.posts.services import can_view

    mentioned = [u for u in comment.mentions.all() if u.pk not in notified and can_view(u, post)]
    notify(mentioned, category=Category.SOCIAL, kind=Notification.Kind.MENTION,
           text=f"{who} mentioned you in a comment", url=url, actor=comment.author)


@safely
def followed(follower, target):
    notify([target], category=Category.SOCIAL, kind=Notification.Kind.FOLLOW, text=f"{follower.full_name} followed you",
           url=f"/u/{follower.username}/", actor=follower, dedupe_key=f"follow:{follower.pk}")


@safely
def score_changed(obj, new_score, previous_score):
    for milestone in VOTE_MILESTONES:
        if previous_score < milestone <= new_score:
            is_post = obj._meta.model_name == "post"
            url = obj.get_absolute_url() if is_post else f"{obj.post.get_absolute_url()}#c-{obj.pk}"
            notify([obj.author], category=Category.SOCIAL, kind=Notification.Kind.VOTES,
                   text=f"Your {'post' if is_post else 'comment'} reached {milestone} upvotes", url=url,
                   dedupe_key=f"votes:{obj._meta.model_name}:{obj.pk}:{milestone}")


@safely
def answer_accepted(comment, by):
    notify([comment.author], category=Category.SOCIAL, kind=Notification.Kind.ACCEPTED,
           text="Your answer was accepted (+15 NexScore)", url=f"{comment.post.get_absolute_url()}#c-{comment.pk}",
           dedupe_key=f"accepted:{comment.pk}")


@safely
def resource_uploaded(resource):
    from apps.spaces.models import SpaceMembership

    members = [m.user for m in SpaceMembership.objects.filter(space__course=resource.course).select_related("user")]
    is_pq = resource.resource_type == "past_question"
    notify(members, category=Category.ACADEMIC,
           kind=Notification.Kind.PAST_QUESTION if is_pq else Notification.Kind.RESOURCE,
           text=f"New {'past question' if is_pq else resource.get_resource_type_display().lower()} in "
                f"{resource.course.code}: {resource.title}",
           url=resource.get_absolute_url(), actor=resource.uploaded_by)


@safely
def join_requested(space, user):
    from apps.accounts.models import RoleAssignment, User
    from apps.spaces.models import SpaceMembership

    managers = {m.user for m in SpaceMembership.objects.filter(space=space, role="moderator").select_related("user")}
    if not managers:  # fall back to the department admins
        managers = set(User.objects.filter(role_assignments__role=RoleAssignment.Role.DEPARTMENT_ADMIN,
                                           role_assignments__department=space.department, is_active=True))
    notify(list(managers), category=Category.SOCIAL, kind=Notification.Kind.SPACE_REQUEST,
           text=f"{user.full_name} asked to join {space.name}", url=f"{space.get_absolute_url()}?tab=requests",
           actor=user, dedupe_key=f"space-request:{space.pk}:{user.pk}:{timezone.now():%Y%m%d%H}")


@safely
def join_decided(join_request):
    space = join_request.space
    approved = join_request.status == "approved"
    notify([join_request.user], category=Category.SOCIAL, kind=Notification.Kind.SPACE_REQUEST,
           text=f"You're in! Your request to join {space.name} was approved" if approved
           else f"Your request to join {space.name} wasn't approved",
           url=space.get_absolute_url() if approved else "/spaces/", actor=join_request.decided_by)


@safely
def staff_requested(staff):
    from django.conf import settings
    from django.db.models import Q

    from apps.accounts.models import RoleAssignment, User

    platform = User.objects.filter(Q(is_superuser=True) | Q(role_assignments__role=RoleAssignment.Role.SUPER_ADMIN),
                                   is_active=True)
    if settings.STAFF_VERIFICATION == "platform":
        admins = list(platform.distinct())
        url = "/platform/staff/"
    else:
        admins = list(User.objects.filter(role_assignments__role=RoleAssignment.Role.DEPARTMENT_ADMIN,
                                          role_assignments__department=staff.user.department, is_active=True))
        admins = admins or list(platform.distinct())  # a brand-new department: platform admins verify
        url = "/manage/staff/"
    notify(admins, category=Category.DEPARTMENT, kind=Notification.Kind.STAFF,
           text=f"{staff.user.full_name} ({staff.user.department.name}) signed up as {staff.get_position_display()} "
                "and needs verifying",
           url=url, actor=staff.user, critical=True, dedupe_key=f"staff-request:{staff.pk}")


@safely
def staff_decided(staff):
    approved = staff.status == "verified"
    notify([staff.user], category=Category.DEPARTMENT, kind=Notification.Kind.STAFF, critical=True,
           text=(f"You're verified as {staff.get_position_display()}. Your staff dashboard is ready."
                 if approved else f"Your {staff.get_position_display()} account wasn't verified"
                 + (f": {staff.note}" if staff.note else ".")),
           url="/dashboard/" if approved else "/settings/account/", actor=staff.decided_by,
           dedupe_key=f"staff-decided:{staff.pk}:{staff.status}")


def audience_users(item):
    """Everyone a targeted announcement/event is meant for."""
    from apps.accounts.models import User
    from apps.notices.models import Audience

    users = User.objects.filter(department_id=item.department_id, is_active=True)
    if item.audience == Audience.LEVEL:
        users = users.filter(level=item.target_level)
    elif item.audience == Audience.COURSE:
        users = users.filter(space_memberships__space__course_id=item.target_course_id)
    elif item.audience == Audience.SPACE:
        users = users.filter(space_memberships__space_id=item.target_space_id)
    return users.distinct()


@safely
def announcement_published(announcement):
    notify(audience_users(announcement), category=Category.DEPARTMENT, kind=Notification.Kind.ANNOUNCEMENT,
           text=f"{announcement.get_priority_display() + ': ' if announcement.priority != 'normal' else ''}"
                f"{announcement.title}",
           url=f"/announcements/{announcement.pk}/", actor=announcement.created_by,
           critical=announcement.priority != "normal", dedupe_key=f"announcement:{announcement.pk}")


# --- Delivery ------------------------------------------------------------------
def mark_read(user, ids=None):
    qs = Notification.objects.filter(recipient=user, is_read=False)
    if ids is not None:
        qs = qs.filter(pk__in=ids)
    updated = qs.update(is_read=True)
    _bust([user.pk])
    return updated


def send_pending_push(limit=500) -> dict:
    """Deliver queued push notifications. Called from the run_scheduled cron command."""
    stats = {"sent": 0, "skipped": 0, "expired_subscriptions": 0}
    pending = list(Notification.objects.filter(push_status=Notification.PushStatus.PENDING)
                   .select_related("recipient")[:limit])
    if not settings.PUSH_ENABLED:
        Notification.objects.filter(pk__in=[n.pk for n in pending]).update(push_status=Notification.PushStatus.NONE)
        stats["skipped"] = len(pending)
        return stats
    from . import webpush

    subs = {}
    for s in PushSubscription.objects.filter(user__in={n.recipient_id for n in pending}):
        subs.setdefault(s.user_id, []).append(s)
    for n in pending:
        for sub in subs.get(n.recipient_id, []):
            try:
                status = webpush.send(sub, {"title": "NexSpace", "body": n.text, "url": n.url, "tag": f"n{n.pk}"},
                                      public_b64=settings.VAPID_PUBLIC_KEY, private_b64=settings.VAPID_PRIVATE_KEY,
                                      subject=settings.VAPID_SUBJECT, urgency="high" if n.is_critical else "normal")
            except Exception:
                logger.exception("Push to subscription %s failed", sub.pk)
                continue
            if status in (404, 410):
                sub.delete()
                stats["expired_subscriptions"] += 1
            elif 200 <= status < 300:
                PushSubscription.objects.filter(pk=sub.pk).update(last_success_at=timezone.now())
                stats["sent"] += 1
        Notification.objects.filter(pk=n.pk).update(push_status=Notification.PushStatus.SENT)
    return stats

"""Reporting and moderation (spec Sections 34 and 37)."""
from datetime import timedelta

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Count, F
from django.utils import timezone

from apps.accounts.models import User
from apps.core.models import audit
from apps.posts.models import Comment, Post
from apps.posts.services import _limit, require_verified
from apps.reputation import rules
from apps.reputation import services as nexscore
from apps.resources.models import Resource
from apps.spaces.models import Space

from apps.messaging.models import Message

from .models import ModerationAction, Report, TargetType

AUTO_HIDE_THRESHOLD = 5
REPORT_RATE = (20, 60 * 60)
MODELS = {TargetType.POST: Post, TargetType.COMMENT: Comment, TargetType.RESOURCE: Resource,
          TargetType.USER: User, TargetType.SPACE: Space, TargetType.MESSAGE: Message}
HIDEABLE = {TargetType.POST, TargetType.COMMENT, TargetType.RESOURCE, TargetType.MESSAGE}


def load_target(target_type, target_id):
    model = MODELS.get(target_type)
    if model is None:
        return None
    try:
        return model.objects.filter(pk=int(target_id)).first()
    except (TypeError, ValueError):
        return None


def can_see_target(user, target_type, obj) -> bool:
    """Only content the person can already see can be reported (and previewed on the report page).
    Without this, the report page would show a snippet of any private message or private-Space post."""
    from apps.posts.services import can_view as can_view_post
    from apps.resources.services import can_view as can_view_resource

    if obj is None or target_department_id(target_type, obj) != user.department_id:
        return False
    if target_type == TargetType.POST:
        return can_view_post(user, obj)
    if target_type == TargetType.COMMENT:
        return not obj.is_deleted and can_view_post(user, obj.post)
    if target_type == TargetType.RESOURCE:
        return can_view_resource(user, obj)
    if target_type == TargetType.MESSAGE:
        return (not obj.is_deleted and obj.conversation.members.filter(user=user).exists())
    if target_type == TargetType.SPACE:
        return True  # every Space's name and description are listed for the whole department
    if target_type == TargetType.USER:
        return obj.is_active and obj.profile.can_be_viewed_by(user)
    return False


def _require_target(target_type, target_id):
    if target_type not in MODELS:
        raise ValidationError("Unknown content type.")
    obj = load_target(target_type, target_id)
    if obj is None:
        raise ValidationError("That content no longer exists.")
    return obj


def _require_reported_if_private(target_type, obj):
    """Moderators only ever see or act on private messages that someone in the chat reported."""
    if target_type == TargetType.MESSAGE and not Report.objects.filter(target_type=target_type, target_id=obj.pk).exists():
        raise PermissionDenied("Private messages can only be moderated after someone in the chat reports them.")


def target_department_id(target_type, obj):
    return {
        TargetType.POST: lambda o: o.department_id,
        TargetType.COMMENT: lambda o: o.post.department_id,
        TargetType.RESOURCE: lambda o: o.course.department_id,
        TargetType.USER: lambda o: o.department_id,
        TargetType.SPACE: lambda o: o.department_id,
        TargetType.MESSAGE: lambda o: o.conversation.department_id,
    }[target_type](obj)


def target_owner(target_type, obj):
    return {
        TargetType.POST: lambda o: o.author, TargetType.COMMENT: lambda o: o.author,
        TargetType.RESOURCE: lambda o: o.uploaded_by, TargetType.USER: lambda o: o,
        TargetType.SPACE: lambda o: o.created_by,
        TargetType.MESSAGE: lambda o: o.sender,
    }[target_type](obj)


def label(target_type, obj):
    if target_type == TargetType.POST:
        return (obj.title or obj.body)[:80] or f"Post #{obj.pk}"
    if target_type == TargetType.COMMENT:
        return obj.body[:80] or f"Comment #{obj.pk}"
    if target_type == TargetType.RESOURCE:
        return obj.title[:80]
    if target_type == TargetType.USER:
        return f"@{obj.username}"
    if target_type == TargetType.MESSAGE:
        return f"Message: {obj.body[:70]}" if obj.body else f"Message #{obj.pk}"
    return obj.name


def _log(moderator, department_id, action, target_type, obj, note=""):
    return ModerationAction.objects.create(
        moderator=moderator, department_id=department_id, action=action, target_type=target_type,
        target_id=obj.pk, target_label=label(target_type, obj), note=note[:500],
    )


# --- Reporting -------------------------------------------------------------------
@transaction.atomic
def submit_report(*, reporter, target_type, target_id, reason, details=""):
    require_verified(reporter)
    if reason not in Report.Reason.values:
        raise ValidationError("Choose a reason.")
    obj = load_target(target_type, target_id)
    if not can_see_target(reporter, target_type, obj):
        raise ValidationError("That content can't be reported.")
    owner = target_owner(target_type, obj)
    if owner is not None and owner.pk == reporter.pk:
        raise ValidationError("You can't report your own content.")
    if reason == Report.Reason.OTHER and not (details or "").strip():
        raise ValidationError("Tell moderators what's wrong.")
    _limit(reporter, "report", REPORT_RATE)
    try:
        with transaction.atomic():
            report = Report.objects.create(
                reporter=reporter, department_id=reporter.department_id, target_type=target_type,
                target_id=obj.pk, reason=reason, details=(details or "").strip()[:500],
            )
    except IntegrityError:
        raise ValidationError("You've already reported this. Moderators will review it.")

    if target_type in HIDEABLE and not obj.is_hidden:
        open_reporters = Report.objects.filter(
            target_type=target_type, target_id=obj.pk, status=Report.Status.OPEN
        ).values("reporter").distinct().count()
        if open_reporters >= AUTO_HIDE_THRESHOLD:
            obj.is_hidden = True
            obj.save(update_fields=["is_hidden"])
            _log(None, report.department_id, ModerationAction.Action.AUTO_HIDE, target_type, obj,
                 f"{open_reporters} reports")
    return report


# --- Moderator queue -------------------------------------------------------------
def require_moderator(user, department_id):
    from apps.academics.models import Department

    if not user.can_moderate(Department.objects.get(pk=department_id)):
        raise PermissionDenied("Only moderators can do this.")


def queue_for(user):
    """Open reports grouped by target, most-reported first."""
    groups = (
        Report.objects.filter(department_id=user.department_id, status=Report.Status.OPEN)
        .values("target_type", "target_id").annotate(n=Count("id")).order_by("-n")
    )
    items = []
    for g in groups[:100]:
        obj = load_target(g["target_type"], g["target_id"])
        if obj is None:
            continue
        reports = list(Report.objects.filter(target_type=g["target_type"], target_id=g["target_id"],
                                             status=Report.Status.OPEN).select_related("reporter"))
        items.append({"target_type": g["target_type"], "obj": obj, "count": g["n"], "reports": reports,
                      "owner": target_owner(g["target_type"], obj)})
    return items


def _close_reports(moderator, target_type, obj, status):
    Report.objects.filter(target_type=target_type, target_id=obj.pk, status=Report.Status.OPEN).update(
        status=status, resolved_by=moderator, resolved_at=timezone.now()
    )


@transaction.atomic
def dismiss(*, moderator, target_type, target_id, note=""):
    obj = _require_target(target_type, target_id)
    dept = target_department_id(target_type, obj)
    require_moderator(moderator, dept)
    _require_reported_if_private(target_type, obj)
    if target_type in HIDEABLE and obj.is_hidden:
        obj.is_hidden = False
        obj.save(update_fields=["is_hidden"])
    _close_reports(moderator, target_type, obj, Report.Status.DISMISSED)
    _log(moderator, dept, ModerationAction.Action.DISMISS, target_type, obj, note)


@transaction.atomic
def remove_content(*, moderator, target_type, target_id, note=""):
    if target_type not in HIDEABLE and target_type != TargetType.SPACE:
        raise ValidationError("Users are suspended or banned, not removed.")
    obj = _require_target(target_type, target_id)
    dept = target_department_id(target_type, obj)
    require_moderator(moderator, dept)
    _require_reported_if_private(target_type, obj)
    owner = target_owner(target_type, obj)
    _close_reports(moderator, target_type, obj, Report.Status.ACTIONED)

    if target_type == TargetType.SPACE:
        from apps.accounts.models import RoleAssignment

        if not moderator.has_role(RoleAssignment.Role.DEPARTMENT_ADMIN, department=obj.department):
            raise PermissionDenied("Only department admins can delete Spaces.")
        if obj.course_id:
            raise ValidationError("Course Spaces can't be deleted; deactivate the course instead.")
        _log(moderator, dept, ModerationAction.Action.DELETE_SPACE, target_type, obj, note)
        from apps.spaces.services import delete_space

        delete_space(obj)
        return

    anonymous = False
    if target_type == TargetType.POST:
        obj.is_deleted, obj.deleted_at, anonymous = True, timezone.now(), obj.is_anonymous
        for comment in obj.comments.all():
            nexscore.reverse(source=comment)
    elif target_type == TargetType.COMMENT:
        obj.is_deleted = True
        Post.objects.filter(pk=obj.post_id, comment_count__gt=0).update(comment_count=F("comment_count") - 1)
    elif target_type == TargetType.RESOURCE:
        obj.is_removed = True
    elif target_type == TargetType.MESSAGE:
        obj.is_deleted = True
    if hasattr(obj, "removed_by_moderator"):
        obj.removed_by_moderator = True
    obj.is_hidden = False
    obj.save()
    nexscore.reverse(source=obj)
    if owner is not None:
        nexscore.award(user=owner, reason="content_removed", amount=rules.CONTENT_REMOVED, source=obj,
                       anonymous=anonymous)
    _log(moderator, dept, ModerationAction.Action.REMOVE, target_type, obj, note)


def _outranks(moderator, user) -> bool:
    """Moderators can act on members; only department admins can act on moderators/admins;
    nobody can act on the platform owner or a platform admin from here."""
    from apps.accounts.models import RoleAssignment

    if user.is_superuser or user.is_platform_admin or getattr(user, "is_owner", False):
        return False
    if user.has_role(RoleAssignment.Role.DEPARTMENT_ADMIN, department=user.department):
        return moderator.is_platform_admin
    if user.department_id and user.can_moderate(user.department):
        return moderator.is_platform_admin or moderator.has_role(RoleAssignment.Role.DEPARTMENT_ADMIN,
                                                                  department=user.department)
    return True


@transaction.atomic
def suspend_user(*, moderator, user, days=7, note=""):
    require_moderator(moderator, user.department_id)
    if user.pk == moderator.pk or not _outranks(moderator, user):
        raise PermissionDenied("You can't suspend this account.")
    try:
        days = max(1, min(365, int(days)))
    except (TypeError, ValueError):
        raise ValidationError("Choose how many days.")
    user.suspended_until = timezone.now() + timedelta(days=days)
    user.save(update_fields=["suspended_until"])
    _close_reports(moderator, TargetType.USER, user, Report.Status.ACTIONED)
    _log(moderator, user.department_id, ModerationAction.Action.SUSPEND, TargetType.USER, user, note or f"{days} days")
    audit(moderator, "user.suspended", user, days=int(days))


@transaction.atomic
def unsuspend_user(*, moderator, user):
    require_moderator(moderator, user.department_id)
    user.suspended_until = None
    user.save(update_fields=["suspended_until"])
    _log(moderator, user.department_id, ModerationAction.Action.UNSUSPEND, TargetType.USER, user)
    audit(moderator, "user.unsuspended", user)


@transaction.atomic
def ban_user(*, moderator, user, note=""):
    from apps.accounts.models import RoleAssignment

    if not moderator.has_role(RoleAssignment.Role.DEPARTMENT_ADMIN, department=user.department):
        raise PermissionDenied("Only department admins can ban accounts.")
    if user.pk == moderator.pk or not _outranks(moderator, user):
        raise PermissionDenied("You can't ban this account.")
    user.is_active = False
    user.save(update_fields=["is_active"])
    from django.contrib.sessions.models import Session

    for session in Session.objects.filter(expire_date__gt=timezone.now()):
        if session.get_decoded().get("_auth_user_id") == str(user.pk):
            session.delete()
    _close_reports(moderator, TargetType.USER, user, Report.Status.ACTIONED)
    _log(moderator, user.department_id, ModerationAction.Action.BAN, TargetType.USER, user, note)
    audit(moderator, "user.banned", user)


def reveal_anonymous_author(*, moderator, post):
    """Show a moderator who wrote an anonymous post. Every lookup is logged and audited."""
    require_moderator(moderator, post.department_id)
    if not post.is_anonymous:
        return post.author
    _log(moderator, post.department_id, ModerationAction.Action.REVEAL, TargetType.POST, post)
    audit(moderator, "anonymous.revealed", post)
    return post.author

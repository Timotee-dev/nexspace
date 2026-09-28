from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.db.models import F
from django.utils.text import slugify

from .models import Space, SpaceJoinRequest, SpaceMembership

JOIN_REQUEST_RATE = (20, 60 * 60)


def can_create_space(user) -> bool:
    """Course reps and higher (moderators, department admins, super admins) can create Spaces."""
    from apps.accounts.models import RoleAssignment

    if not user.is_authenticated or not user.department_id:
        return False
    return user.has_role(RoleAssignment.Role.COURSE_REP) or user.has_role(
        RoleAssignment.Role.MODERATOR, department=user.department
    )


def is_member(user, space) -> bool:
    return SpaceMembership.objects.filter(space=space, user=user).exists()


def can_see_inside(user, space) -> bool:
    """Open Spaces are readable by the whole department; approval-only Spaces by members and managers."""
    if space.department_id != user.department_id:
        return False
    if not space.requires_approval:
        return True
    return is_member(user, space) or can_manage(user, space) or user.can_moderate(space.department)


def ensure_course_space(course):
    """Create (or fetch) the official Space for a course."""
    space, _ = Space.objects.get_or_create(
        course=course,
        defaults={
            "department": course.department, "kind": Space.Kind.COURSE, "name": f"{course.code} — {course.title}",
            "slug": course.slug, "description": f"Discussion, questions and materials for {course.code}.",
            "is_official": True, "level": course.level, "requires_approval": False,
        },
    )
    return space


@transaction.atomic
def add_member(space, user, role=SpaceMembership.Role.MEMBER):
    """Add someone directly (after approval, or for open Spaces). Returns False if already a member."""
    try:
        with transaction.atomic():
            SpaceMembership.objects.create(space=space, user=user, role=role)
    except IntegrityError:
        return False
    Space.objects.filter(pk=space.pk).update(member_count=F("member_count") + 1)
    return True


def join(user, space):
    """Join an open Space. Approval-only Spaces need request_to_join() instead."""
    if space.department_id != user.department_id:
        raise PermissionDenied("You can only join Spaces in your department.")
    if space.requires_approval and not can_manage(user, space):
        raise PermissionDenied("This Space needs approval. Send a request to join instead.")
    return add_member(space, user)


def request_to_join(user, space, message=""):
    """Ask to join an approval-only Space. Returns 'joined', 'member', 'pending' or 'requested'."""
    from apps.posts.services import _limit, require_verified

    if space.department_id != user.department_id:
        raise PermissionDenied("You can only join Spaces in your department.")
    if not space.requires_approval or can_manage(user, space):
        return "joined" if add_member(space, user) else "member"
    if is_member(user, space):
        return "member"
    require_verified(user)
    existing = SpaceJoinRequest.objects.filter(space=space, user=user).first()
    if existing and existing.status == SpaceJoinRequest.Status.PENDING:
        return "pending"
    _limit(user, "space-request", JOIN_REQUEST_RATE)
    if existing:
        existing.status, existing.message = SpaceJoinRequest.Status.PENDING, (message or "")[:200]
        existing.decided_by = existing.decided_at = None
        existing.save()
    else:
        SpaceJoinRequest.objects.create(space=space, user=user, message=(message or "")[:200])
    from apps.notifications import services as notifications

    notifications.join_requested(space, user)
    return "requested"


def cancel_request(user, space):
    return SpaceJoinRequest.objects.filter(space=space, user=user, status=SpaceJoinRequest.Status.PENDING).delete()[0] > 0


def pending_request_ids(user) -> set[int]:
    return set(SpaceJoinRequest.objects.filter(user=user, status=SpaceJoinRequest.Status.PENDING)
               .values_list("space_id", flat=True))


@transaction.atomic
def decide_request(*, manager, join_request, approve: bool):
    from django.utils import timezone

    space = join_request.space
    if not can_manage(manager, space):
        raise PermissionDenied("Only this Space's managers can approve members.")
    if join_request.status != SpaceJoinRequest.Status.PENDING:
        raise ValidationError("This request has already been handled.")
    join_request.status = SpaceJoinRequest.Status.APPROVED if approve else SpaceJoinRequest.Status.DECLINED
    join_request.decided_by, join_request.decided_at = manager, timezone.now()
    join_request.save(update_fields=["status", "decided_by", "decided_at"])
    if approve:
        add_member(space, join_request.user)
    from apps.notifications import services as notifications

    notifications.join_decided(join_request)
    return join_request


@transaction.atomic
def remove_member(*, manager, space, user):
    if not can_manage(manager, space):
        raise PermissionDenied("Only this Space's managers can remove members.")
    if user.pk == manager.pk:
        raise ValidationError("Use Leave to leave a Space yourself.")
    membership = SpaceMembership.objects.filter(space=space, user=user).first()
    if membership is None:
        return False
    if membership.role == SpaceMembership.Role.MODERATOR and not can_manage_moderators(manager, space):
        raise PermissionDenied("Only department admins can remove a Space's moderators.")
    return leave(user, space)


def can_manage_moderators(user, space) -> bool:
    from apps.accounts.models import RoleAssignment

    return user.has_role(RoleAssignment.Role.DEPARTMENT_ADMIN, department=space.department)


@transaction.atomic
def leave(user, space):
    deleted, _ = SpaceMembership.objects.filter(space=space, user=user).delete()
    if deleted:
        Space.objects.filter(pk=space.pk, member_count__gt=0).update(member_count=F("member_count") - 1)
    return bool(deleted)


def set_muted(user, space, muted: bool):
    updated = SpaceMembership.objects.filter(space=space, user=user).update(is_muted=muted)
    if not updated:
        raise ValidationError("Join this Space first.")
    return muted


def joined_space_ids(user, *, include_muted=True) -> set[int]:
    qs = SpaceMembership.objects.filter(user=user)
    if not include_muted:
        qs = qs.filter(is_muted=False)
    return set(qs.values_list("space_id", flat=True))


def muted_space_ids(user) -> set[int]:
    return set(SpaceMembership.objects.filter(user=user, is_muted=True).values_list("space_id", flat=True))


def joined_course_ids(user) -> set[int]:
    return set(
        SpaceMembership.objects.filter(user=user, space__course__isnull=False).values_list("space__course_id", flat=True)
    )


def can_manage(user, space) -> bool:
    """Space moderators, course reps (course Spaces) and department admins."""
    from apps.accounts.models import RoleAssignment

    if space.course_id and user.has_role(RoleAssignment.Role.COURSE_REP, course=space.course):
        return True
    if user.has_role(RoleAssignment.Role.DEPARTMENT_ADMIN, department=space.department):
        return True
    return SpaceMembership.objects.filter(space=space, user=user, role=SpaceMembership.Role.MODERATOR).exists()


@transaction.atomic
def create_space(*, user, name, description="", rules="", icon="", requires_approval=True):
    from apps.posts.services import require_verified

    require_verified(user)
    if not can_create_space(user):
        raise PermissionDenied("Only course reps, moderators and department admins can create Spaces.")
    name = " ".join((name or "").split())
    if len(name) < 3:
        raise ValidationError("Give the Space a name of at least 3 characters.")
    base = slugify(name)[:50] or "space"
    slug, n = base, 1
    while Space.objects.filter(department_id=user.department_id, slug=slug).exists() or slug in {"new", "courses"}:
        n += 1
        slug = f"{base}-{n}"
    space = Space.objects.create(
        department_id=user.department_id, name=name, slug=slug, description=description[:300],
        rules=rules, icon=(icon or "")[:4], created_by=user, requires_approval=requires_approval,
    )
    SpaceMembership.objects.create(space=space, user=user, role=SpaceMembership.Role.MODERATOR)
    Space.objects.filter(pk=space.pk).update(member_count=1)
    space.refresh_from_db()
    return space


def sync_course_rep_moderation(user, course, *, is_rep: bool):
    space = ensure_course_space(course)
    membership = SpaceMembership.objects.filter(space=space, user=user).first()
    if is_rep:
        if membership is None and user.department_id == space.department_id:
            add_member(space, user)
            membership = SpaceMembership.objects.filter(space=space, user=user).first()
        if membership:
            membership.role = SpaceMembership.Role.MODERATOR
            membership.save(update_fields=["role"])
    elif membership:
        membership.role = SpaceMembership.Role.MEMBER
        membership.save(update_fields=["role"])

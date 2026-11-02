from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.db.models import F
from django.utils.text import slugify

from .models import Space, SpaceJoinRequest, SpaceMembership

JOIN_REQUEST_RATE = (20, 60 * 60)
CREATE_SPACE_RATE = (3, 24 * 60 * 60)  # three new clubs a day per person keeps spam out


def can_create_space(user) -> bool:
    """Spaces are clubs and activities (sports, games, music...): any verified, active member can start one."""
    return bool(user.is_authenticated and user.department_id and user.can_write)


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
            "is_official": True, "level": course.level, "requires_approval": True,
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
    own_level_course = bool(space.course_id and user.level and space.course.level == user.level)
    if space.requires_approval and not own_level_course and not can_manage(user, space):
        raise PermissionDenied("This Space needs approval. Send a request to join instead.")
    return add_member(space, user)


def request_to_join(user, space, message=""):
    """Ask to join an approval-only Space. Returns 'joined', 'member', 'pending' or 'requested'."""
    from apps.posts.services import _limit, require_verified

    if space.department_id != user.department_id:
        raise PermissionDenied("You can only join Spaces in your department.")
    own_level_course = bool(space.course_id and user.level and space.course.level == user.level)
    if not space.requires_approval or own_level_course or can_manage(user, space):
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
        return True  # course reps and the course's lecturers
    if space.kind == Space.Kind.LEVEL and space.level and (
            user.has_role(RoleAssignment.Role.LEVEL_ADVISER, department=space.department, level=space.level)
            or user.role_assignments.filter(role=RoleAssignment.Role.COURSE_REP, department=space.department,
                                            level=space.level).exists()):
        return True
    if user.has_role(RoleAssignment.Role.DEPARTMENT_ADMIN, department=space.department):
        return True
    return SpaceMembership.objects.filter(space=space, user=user, role=SpaceMembership.Role.MODERATOR).exists()


@transaction.atomic
def create_space(*, user, name, description="", rules="", icon="", requires_approval=False, category="other"):
    from apps.posts.services import _limit, require_verified

    require_verified(user)
    if not can_create_space(user):
        raise PermissionDenied("Verify your email to start a Space.")
    if category not in Space.Category.values:
        raise ValidationError("Choose what kind of Space it is.")
    name = " ".join((name or "").split())
    if len(name) < 3:
        raise ValidationError("Give the Space a name of at least 3 characters.")
    base = slugify(name)[:50] or "space"
    slug, n = base, 1
    while Space.objects.filter(department_id=user.department_id, slug=slug).exists() or slug in {"new", "courses"}:
        n += 1
        slug = f"{base}-{n}"
    _limit(user, "space-create", CREATE_SPACE_RATE)
    space = Space.objects.create(
        department_id=user.department_id, name=name[:80], slug=slug, description=(description or "")[:300],
        rules=(rules or "")[:2000], icon=(icon or "")[:4], created_by=user, requires_approval=requires_approval,
        kind=Space.Kind.COMMUNITY, category=category,
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


def space_managers(space):
    """Everyone who can approve requests for this Space, so they can be notified:
    its moderators, and for a course Space that level's course reps and the course's lecturers;
    for a level Space that level's reps and advisers; always the department admins."""
    from django.db.models import Q

    from apps.accounts.models import RoleAssignment, User

    R = RoleAssignment.Role
    scope = Q(role_assignments__role=R.DEPARTMENT_ADMIN, role_assignments__department=space.department)
    scope |= Q(space_memberships__space=space, space_memberships__role=SpaceMembership.Role.MODERATOR)
    if space.course_id:
        scope |= Q(role_assignments__role=R.COURSE_REP, role_assignments__department=space.department,
                   role_assignments__level=space.course.level)
        scope |= Q(role_assignments__role=R.LECTURER, role_assignments__course=space.course)
    elif space.kind == Space.Kind.LEVEL and space.level:
        scope |= Q(role_assignments__role__in=[R.COURSE_REP, R.LEVEL_ADVISER],
                   role_assignments__department=space.department, role_assignments__level=space.level)
    return User.objects.filter(scope, is_active=True).distinct()


@transaction.atomic
def delete_space(space):
    """Delete a Space safely. Its posts are deleted with it: without this they would fall out into the
    general department feed (posts keep existing when their Space goes), exposing private posts."""
    from django.utils import timezone

    from apps.posts.models import Post

    Post.objects.filter(space=space).update(is_deleted=True, deleted_at=timezone.now())
    space.delete()


def delete_course(course):
    """Delete a course: its Space and discussions, materials (and their stored files), calendar dates
    and lecturer roles. Course reps are per level, so they are kept."""
    from apps.resources.models import Resource

    files = [r.file for r in Resource.objects.filter(course=course).exclude(file="")]
    space = Space.objects.filter(course=course).first()
    with transaction.atomic():
        if space is not None:
            delete_space(space)
        course.delete()
    for fieldfile in files:  # storage isn't part of the transaction: tidy up afterwards
        try:
            fieldfile.storage.delete(fieldfile.name)
        except Exception:
            import logging

            logging.getLogger(__name__).warning("Couldn't delete stored file %s", fieldfile.name)

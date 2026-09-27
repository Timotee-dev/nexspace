from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.db.models import F
from django.utils.text import slugify

from .models import Space, SpaceMembership


def ensure_course_space(course):
    """Create (or fetch) the official Space for a course."""
    space, _ = Space.objects.get_or_create(
        course=course,
        defaults={
            "department": course.department, "kind": Space.Kind.COURSE, "name": f"{course.code} — {course.title}",
            "slug": course.slug, "description": f"Discussion, questions and materials for {course.code}.",
            "is_official": True, "level": course.level,
        },
    )
    return space


@transaction.atomic
def join(user, space):
    if space.department_id != user.department_id:
        raise PermissionDenied("You can only join Spaces in your department.")
    try:
        with transaction.atomic():
            SpaceMembership.objects.create(space=space, user=user)
    except IntegrityError:
        return False
    Space.objects.filter(pk=space.pk).update(member_count=F("member_count") + 1)
    return True


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
def create_space(*, user, name, description="", rules="", icon=""):
    from apps.posts.services import require_verified

    require_verified(user)
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
        rules=rules, icon=(icon or "")[:4], created_by=user,
    )
    SpaceMembership.objects.create(space=space, user=user, role=SpaceMembership.Role.MODERATOR)
    Space.objects.filter(pk=space.pk).update(member_count=1)
    space.refresh_from_db()
    return space


def sync_course_rep_moderation(user, course, *, is_rep: bool):
    space = ensure_course_space(course)
    membership = SpaceMembership.objects.filter(space=space, user=user).first()
    if is_rep:
        if membership is None:
            join(user, space) if user.department_id == space.department_id else None
            membership = SpaceMembership.objects.filter(space=space, user=user).first()
        if membership:
            membership.role = SpaceMembership.Role.MODERATOR
            membership.save(update_fields=["role"])
    elif membership:
        membership.role = SpaceMembership.Role.MEMBER
        membership.save(update_fields=["role"])

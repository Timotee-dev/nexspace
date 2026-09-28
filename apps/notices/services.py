from datetime import timedelta

from django.core.exceptions import PermissionDenied, ValidationError
from django.utils import timezone

from apps.accounts.models import RoleAssignment
from apps.posts.services import require_verified

from .models import AcademicEvent, Announcement, Audience


def publish_scope(user):
    """What a user may publish announcements and calendar dates to.

    HOD / department admin: anything. Exam officer: the department, any level, any course.
    Level adviser: their level(s). Lecturer / course rep: their courses.
    """
    from apps.academics.models import Course, Level

    R = RoleAssignment.Role
    dept = user.department
    assignments = user.role_assignments.all()
    is_admin = user.has_role(R.DEPARTMENT_ADMIN, department=dept)
    is_exam = is_admin or assignments.filter(role=R.EXAM_OFFICER, department=dept).exists()
    if is_exam:
        courses = set(Course.objects.filter(department=dept, is_active=True).values_list("pk", flat=True))
    else:
        courses = set(assignments.filter(role__in=[R.COURSE_REP, R.LECTURER], course__isnull=False)
                      .values_list("course_id", flat=True))
    if is_exam:
        levels = {value for value, _ in Level.choices}
    else:
        levels = set(assignments.filter(role=R.LEVEL_ADVISER, department=dept).values_list("level", flat=True))
    return {"admin": is_admin, "department": is_exam, "spaces": is_admin, "courses": courses, "levels": levels}


def can_publish(user, *, audience, target_course=None, target_level=None) -> bool:
    scope = publish_scope(user)
    if scope["admin"]:
        return True
    if audience == Audience.DEPARTMENT:
        return scope["department"]
    if audience == Audience.LEVEL:
        return target_level in scope["levels"]
    if audience == Audience.COURSE:
        return target_course is not None and target_course.pk in scope["courses"]
    return False


def is_publisher(user) -> bool:
    scope = publish_scope(user)
    return bool(scope["admin"] or scope["department"] or scope["courses"] or scope["levels"])


def _validate_target(user, audience, target_level, target_course, target_space):
    if audience not in Audience.values:
        raise ValidationError("Choose who this is for.")
    if audience == Audience.LEVEL and not target_level:
        raise ValidationError("Choose a level.")
    if audience == Audience.COURSE and (target_course is None or target_course.department_id != user.department_id):
        raise ValidationError("Choose a course in your department.")
    if audience == Audience.SPACE and (target_space is None or target_space.department_id != user.department_id):
        raise ValidationError("Choose a Space in your department.")
    if not can_publish(user, audience=audience, target_course=target_course, target_level=target_level):
        raise PermissionDenied("You don't have permission to publish to that audience.")
    return {
        "department_id": user.department_id, "audience": audience,
        "target_level": target_level if audience == Audience.LEVEL else None,
        "target_course": target_course if audience == Audience.COURSE else None,
        "target_space": target_space if audience == Audience.SPACE else None,
        "created_by": user,
    }


def publish_announcement(*, user, title, body, priority=Announcement.Priority.NORMAL, audience=Audience.DEPARTMENT,
                         target_level=None, target_course=None, target_space=None, expires_at=None, attachment=None):
    require_verified(user)
    target = _validate_target(user, audience, target_level, target_course, target_space)
    if not (title or "").strip() or not (body or "").strip():
        raise ValidationError("Add a title and a message.")
    if expires_at and expires_at <= timezone.now():
        raise ValidationError("The expiry date must be in the future.")
    extra = {}
    if attachment is not None:
        from apps.core.uploads import validate_document_upload

        validate_document_upload(attachment)
        extra = {"attachment": attachment, "attachment_name": attachment.name[:150]}
    announcement = Announcement.objects.create(
        title=title.strip()[:150], body=body.strip()[:3000], priority=priority, expires_at=expires_at, **target, **extra
    )
    from apps.notifications import services as notifications

    notifications.announcement_published(announcement)
    return announcement


def create_event(*, user, title, kind, starts_at, ends_at=None, all_day=False, location="", description="",
                 audience=Audience.DEPARTMENT, target_level=None, target_course=None, target_space=None):
    require_verified(user)
    target = _validate_target(user, audience, target_level, target_course, target_space)
    if kind not in AcademicEvent.Kind.values:
        raise ValidationError("Choose what kind of date this is.")
    if not (title or "").strip() or starts_at is None:
        raise ValidationError("Add a title and a date.")
    if ends_at and ends_at < starts_at:
        raise ValidationError("The end must be after the start.")
    return AcademicEvent.objects.create(
        title=title.strip()[:150], kind=kind, starts_at=starts_at, ends_at=ends_at, all_day=all_day,
        location=location[:150], description=description[:1500], **target,
    )


def upcoming_for(user, *, limit=None, kinds=None):
    qs = (AcademicEvent.objects.relevant_to(user).filter(starts_at__gte=timezone.now() - timedelta(hours=12))
          .select_related("target_course", "target_space").order_by("starts_at"))
    if kinds:
        qs = qs.filter(kind__in=kinds)
    return list(qs[:limit] if limit else qs)


def countdowns_for(user, limit=3):
    return upcoming_for(user, limit=limit, kinds=AcademicEvent.COUNTDOWN_KINDS)


def active_announcements_for(user, limit=None):
    qs = (Announcement.objects.active().relevant_to(user)
          .select_related("target_course", "target_space", "created_by").order_by("-created_at"))
    items = list(qs[:limit] if limit else qs)
    rank = {"urgent": 0, "important": 1, "normal": 2}
    items.sort(key=lambda a: (rank[a.priority], -a.created_at.timestamp()))
    return items

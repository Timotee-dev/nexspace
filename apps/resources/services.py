from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Case, ExpressionWrapper, F, FloatField, Q, Value, When
from django.utils import timezone

from apps.accounts.models import RoleAssignment
from apps.core.uploads import validate_document_upload
from apps.posts.services import RateLimited, _limit, require_verified  # noqa: F401
from apps.reputation import rules
from apps.reputation import services as nexscore

from .models import Resource, ResourceDownload, ResourceRating

UPLOAD_RATE = (20, 60 * 60)
SORTS = {
    "useful": "Most useful",
    "downloads": "Most downloaded",
    "recent": "Most recent",
    "rated": "Highest rated",
}


def can_view(user, resource) -> bool:
    if resource.is_removed or resource.course.department_id != user.department_id:
        return False
    return not resource.is_hidden or resource.uploaded_by_id == user.pk or user.can_moderate(resource.course.department)


def is_verified_uploader(user, course) -> bool:
    return user.has_role(RoleAssignment.Role.COURSE_REP, course=course)


@transaction.atomic
def upload(*, user, course, title, file, resource_type, description="", exam_type="", session=None, semester=None):
    """Publish immediately — there is no approval queue (spec Section 14)."""
    require_verified(user)
    if course.department_id != user.department_id:
        raise PermissionDenied("You can only share resources for courses in your department.")
    _limit(user, "resource-upload", UPLOAD_RATE)
    title = " ".join((title or "").split())
    if not title:
        raise ValidationError("Give the resource a title.")
    if resource_type not in Resource.Type.values:
        raise ValidationError("Choose a resource type.")
    if exam_type and resource_type != Resource.Type.PAST_QUESTION:
        exam_type = ""
    if file is None:
        raise ValidationError("Choose a file to upload.")
    content_type = validate_document_upload(file)
    resource = Resource.objects.create(
        course=course, title=title[:150], description=(description or "")[:1000], resource_type=resource_type,
        exam_type=exam_type or "", session=session, semester=semester or course.semester,
        file=file, original_name=file.name[:150], size=file.size, content_type=content_type,
        uploaded_by=user, is_verified_upload=is_verified_uploader(user, course),
    )
    from apps.notifications import services as notifications

    notifications.resource_uploaded(resource)
    if resource.size <= 5 * 1024 * 1024:  # small files are read straight away; the rest by run_scheduled
        from apps.nexai.indexing import index_resource

        transaction.on_commit(lambda: _index_quietly(index_resource, resource))
    return resource


def _index_quietly(fn, resource):
    try:
        fn(resource)
    except Exception:  # indexing must never break an upload
        import logging

        logging.getLogger(__name__).exception("Indexing resource %s failed", resource.pk)


@transaction.atomic
def remove(*, user, resource):
    manager = user.has_role(RoleAssignment.Role.COURSE_REP, course=resource.course)
    if resource.uploaded_by_id != user.pk and not manager:
        raise PermissionDenied("You can only remove resources you uploaded.")
    resource.is_removed = True
    resource.save(update_fields=["is_removed"])
    nexscore.reverse(source=resource)


def record_download(*, user, resource) -> bool:
    """Count one download per user per day. First-ever download by a user earns the uploader +1."""
    try:
        with transaction.atomic():
            ResourceDownload.objects.create(resource=resource, user=user, day=timezone.localdate())
    except IntegrityError:
        return False
    Resource.objects.filter(pk=resource.pk).update(download_count=F("download_count") + 1)
    first_time = not ResourceDownload.objects.filter(resource=resource, user=user).exclude(day=timezone.localdate()).exists()
    if first_time and resource.uploaded_by_id and user.can_write:
        nexscore.award(user=resource.uploaded_by, actor=user, reason="resource_downloaded",
                       amount=rules.RESOURCE_DOWNLOADED, source=resource)
    return True


@transaction.atomic
def rate(*, user, resource, stars, review=""):
    require_verified(user)
    if not can_view(user, resource):
        raise PermissionDenied("You can't rate this resource.")
    if resource.uploaded_by_id == user.pk:
        raise PermissionDenied("You can't rate your own upload.")
    try:
        stars = int(stars)
    except (TypeError, ValueError):
        raise ValidationError("Pick between 1 and 5 stars.")
    if not 1 <= stars <= 5:
        raise ValidationError("Pick between 1 and 5 stars.")
    Resource.objects.select_for_update().filter(pk=resource.pk).first()
    existing = ResourceRating.objects.filter(resource=resource, user=user).first()
    old = existing.stars if existing else 0
    if existing:
        existing.stars, existing.review = stars, (review or "")[:500]
        existing.save(update_fields=["stars", "review", "updated_at"])
    else:
        ResourceRating.objects.create(resource=resource, user=user, stars=stars, review=(review or "")[:500])
        Resource.objects.filter(pk=resource.pk).update(rating_count=F("rating_count") + 1)
    Resource.objects.filter(pk=resource.pk).update(rating_sum=F("rating_sum") + stars - old)

    # +5 when a rating reaches 4+ stars; taken back if it drops below 4.
    if resource.uploaded_by_id:
        was_high, is_high = old >= 4, stars >= 4
        if is_high and not was_high:
            nexscore.award(user=resource.uploaded_by, actor=user, reason="resource_rated",
                           amount=rules.RESOURCE_RATED_HIGH, source=resource)
        elif was_high and not is_high:
            nexscore.reverse(source=resource, reason="resource_rated", actor=user)
    resource.refresh_from_db()
    return resource


def search(queryset, *, q="", course=None, level=None, session=None, semester=None, resource_type=None, sort="useful"):
    if q:
        for word in q.split()[:6]:
            queryset = queryset.filter(
                Q(title__icontains=word) | Q(description__icontains=word) | Q(course__code__icontains=word)
                | Q(course__title__icontains=word) | Q(course__code__icontains=f"{word[:3]} {word[3:]}")
            )
    if course:
        queryset = queryset.filter(course__slug=course)
    if level:
        queryset = queryset.filter(course__level=level)
    if session:
        queryset = queryset.filter(session_id=session)
    if semester:
        queryset = queryset.filter(semester=semester)
    if resource_type:
        queryset = queryset.filter(resource_type=resource_type)

    # Bayesian average (prior of 3 stars over 3 ratings) plus a small download signal.
    bayes = ExpressionWrapper(
        (F("rating_sum") + Value(9.0)) / (F("rating_count") + Value(3.0)), output_field=FloatField()
    )
    avg = Case(When(rating_count=0, then=Value(0.0)),
               default=ExpressionWrapper(F("rating_sum") * 1.0 / F("rating_count"), output_field=FloatField()),
               output_field=FloatField())
    queryset = queryset.annotate(
        usefulness=ExpressionWrapper(bayes + F("download_count") * Value(0.02), output_field=FloatField()),
        avg_rating=avg,
    )
    order = {
        "downloads": ["-download_count", "-created_at"],
        "recent": ["-created_at"],
        "rated": ["-avg_rating", "-rating_count", "-created_at"],
    }.get(sort, ["-usefulness", "-created_at"])
    return queryset.order_by(*order)

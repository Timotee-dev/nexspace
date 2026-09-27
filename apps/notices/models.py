from django.conf import settings
from django.db import models
from django.db.models import Q
from django.utils import timezone

from apps.core.uploads import announcement_upload_path


class Audience(models.TextChoices):
    DEPARTMENT = "department", "Entire department"
    LEVEL = "level", "A level"
    COURSE = "course", "A course"
    SPACE = "space", "A Space"


class TargetedQuerySet(models.QuerySet):
    def relevant_to(self, user):
        """Items aimed at the user's department, level, joined courses or joined Spaces."""
        from apps.spaces.services import joined_course_ids, joined_space_ids

        return self.filter(department_id=user.department_id).filter(
            Q(audience=Audience.DEPARTMENT)
            | Q(audience=Audience.LEVEL, target_level=user.level)
            | Q(audience=Audience.COURSE, target_course_id__in=joined_course_ids(user))
            | Q(audience=Audience.SPACE, target_space_id__in=joined_space_ids(user))
            | Q(created_by=user)
        )


class Targeted(models.Model):
    department = models.ForeignKey("academics.Department", on_delete=models.CASCADE, related_name="+")
    audience = models.CharField(max_length=12, choices=Audience.choices, default=Audience.DEPARTMENT)
    target_level = models.PositiveSmallIntegerField(null=True, blank=True)
    target_course = models.ForeignKey("academics.Course", on_delete=models.CASCADE, null=True, blank=True, related_name="+")
    target_space = models.ForeignKey("spaces.Space", on_delete=models.CASCADE, null=True, blank=True, related_name="+")
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        abstract = True

    @property
    def audience_label(self):
        if self.audience == Audience.LEVEL:
            return f"{self.target_level} Level"
        if self.audience == Audience.COURSE and self.target_course_id:
            return self.target_course.code
        if self.audience == Audience.SPACE and self.target_space_id:
            return self.target_space.name
        return "Department"


class AnnouncementQuerySet(TargetedQuerySet):
    def active(self):
        now = timezone.now()
        return self.filter(is_removed=False).filter(Q(expires_at__isnull=True) | Q(expires_at__gt=now))

    def archived(self):
        return self.filter(is_removed=False, expires_at__lte=timezone.now())


class Announcement(Targeted):
    class Priority(models.TextChoices):
        NORMAL = "normal", "Normal"
        IMPORTANT = "important", "Important"
        URGENT = "urgent", "Urgent"

    title = models.CharField(max_length=150)
    body = models.TextField(max_length=3000)
    priority = models.CharField(max_length=10, choices=Priority.choices, default=Priority.NORMAL)
    attachment = models.FileField(upload_to=announcement_upload_path, blank=True)
    attachment_name = models.CharField(max_length=150, blank=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    is_removed = models.BooleanField(default=False)

    objects = AnnouncementQuerySet.as_manager()

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["department", "expires_at"])]

    def __str__(self):
        return self.title

    @property
    def is_expired(self):
        return self.expires_at is not None and self.expires_at <= timezone.now()


class AcademicEvent(Targeted):
    class Kind(models.TextChoices):
        RESUMPTION = "resumption", "Resumption"
        EXAM = "exam", "Exam"
        TEST = "test", "Test"
        ASSIGNMENT = "assignment", "Assignment due"
        REGISTRATION = "registration", "Registration deadline"
        EVENT = "event", "Department event"
        HOLIDAY = "holiday", "Holiday"
        MEETING = "meeting", "Meeting"

    COUNTDOWN_KINDS = {Kind.EXAM, Kind.TEST, Kind.ASSIGNMENT, Kind.REGISTRATION}

    title = models.CharField(max_length=150)
    kind = models.CharField(max_length=14, choices=Kind.choices)
    starts_at = models.DateTimeField()
    ends_at = models.DateTimeField(null=True, blank=True)
    all_day = models.BooleanField(default=False)
    location = models.CharField(max_length=150, blank=True)
    description = models.TextField(max_length=1500, blank=True)

    objects = TargetedQuerySet.as_manager()

    class Meta:
        ordering = ["starts_at"]
        indexes = [models.Index(fields=["department", "starts_at"])]

    def __str__(self):
        return self.title

    @property
    def days_left(self):
        return (timezone.localtime(self.starts_at).date() - timezone.localdate()).days

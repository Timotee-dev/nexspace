from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.urls import reverse

from apps.academics.models import Semester
from apps.core.uploads import resource_upload_path


class ResourceQuerySet(models.QuerySet):
    def visible(self):
        return self.filter(is_removed=False, is_hidden=False)

    def for_viewer(self, user):
        return self.visible().filter(course__department_id=user.department_id)


class Resource(models.Model):
    class Type(models.TextChoices):
        PAST_QUESTION = "past_question", "Past question"
        LECTURE_NOTE = "lecture_note", "Lecture note"
        SLIDES = "slides", "Slides"
        ASSIGNMENT = "assignment", "Assignment"
        OTHER = "other", "Other"

    class ExamType(models.TextChoices):
        EXAM = "exam", "Exam"
        TEST = "test", "Test"
        QUIZ = "quiz", "Quiz"

    course = models.ForeignKey("academics.Course", on_delete=models.CASCADE, related_name="resources")
    title = models.CharField(max_length=150)
    description = models.TextField(max_length=1000, blank=True)
    resource_type = models.CharField(max_length=16, choices=Type.choices)
    exam_type = models.CharField(max_length=8, choices=ExamType.choices, blank=True)
    session = models.ForeignKey(
        "academics.AcademicSession", on_delete=models.SET_NULL, null=True, blank=True, related_name="resources"
    )
    semester = models.PositiveSmallIntegerField(choices=Semester.choices, null=True, blank=True)

    file = models.FileField(upload_to=resource_upload_path)
    original_name = models.CharField(max_length=150)
    size = models.PositiveIntegerField()
    content_type = models.CharField(max_length=100)

    uploaded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="resources")
    is_verified_upload = models.BooleanField(default=False, help_text="Uploaded by a course rep or department admin")
    download_count = models.PositiveIntegerField(default=0)
    rating_count = models.PositiveIntegerField(default=0)
    rating_sum = models.PositiveIntegerField(default=0)

    is_hidden = models.BooleanField(default=False)
    is_removed = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    # NexAI text index (filled by apps.nexai.indexing)
    indexed_at = models.DateTimeField(null=True, blank=True)
    index_error = models.CharField(max_length=200, blank=True)

    objects = ResourceQuerySet.as_manager()

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["course", "resource_type"]),
            models.Index(fields=["-download_count"]),
        ]

    def __str__(self):
        return f"{self.course.code}: {self.title}"

    def get_absolute_url(self):
        return reverse("resources:detail", args=[self.pk])

    @property
    def rating_avg(self):
        return round(self.rating_sum / self.rating_count, 1) if self.rating_count else None

    @property
    def extension(self):
        return self.original_name.rsplit(".", 1)[-1].upper() if "." in self.original_name else "FILE"


class ResourceRating(models.Model):
    resource = models.ForeignKey(Resource, on_delete=models.CASCADE, related_name="ratings")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="resource_ratings")
    stars = models.PositiveSmallIntegerField(validators=[MinValueValidator(1), MaxValueValidator(5)])
    review = models.CharField(max_length=500, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at"]
        constraints = [
            models.UniqueConstraint(fields=["resource", "user"], name="unique_resource_rating"),
            models.CheckConstraint(condition=models.Q(stars__gte=1, stars__lte=5), name="stars_1_to_5"),
        ]


class ResourceDownload(models.Model):
    """One row per user per resource per day; drives download_count."""

    resource = models.ForeignKey(Resource, on_delete=models.CASCADE, related_name="downloads")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="+")
    day = models.DateField()

    class Meta:
        constraints = [models.UniqueConstraint(fields=["resource", "user", "day"], name="unique_daily_download")]

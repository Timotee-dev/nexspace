"""Institution hierarchy: University → Faculty → Department.

Every department-level entity in later phases hangs off Department, so adding a
second department, faculty or university needs no schema change.
"""
from django.db import models


class Level(models.IntegerChoices):
    L100 = 100, "100 Level"
    L200 = 200, "200 Level"
    L300 = 300, "300 Level"
    L400 = 400, "400 Level"
    L500 = 500, "500 Level"
    L600 = 600, "600 Level"


class University(models.Model):
    name = models.CharField(max_length=200, unique=True)
    short_name = models.CharField(max_length=30)
    slug = models.SlugField(unique=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]
        verbose_name_plural = "universities"

    def __str__(self):
        return self.short_name or self.name


class Faculty(models.Model):
    university = models.ForeignKey(University, on_delete=models.PROTECT, related_name="faculties")
    name = models.CharField(max_length=200)
    slug = models.SlugField()

    class Meta:
        ordering = ["name"]
        verbose_name_plural = "faculties"
        constraints = [models.UniqueConstraint(fields=["university", "slug"], name="unique_faculty_slug")]

    def __str__(self):
        return f"{self.name} ({self.university})"


class Department(models.Model):
    faculty = models.ForeignKey(Faculty, on_delete=models.PROTECT, related_name="departments")
    name = models.CharField(max_length=200)
    code = models.CharField(max_length=10, help_text="Short code used in course codes, e.g. CSC")
    slug = models.SlugField()
    is_active = models.BooleanField(default=True, help_text="Inactive departments are hidden from sign-up.")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]
        constraints = [models.UniqueConstraint(fields=["faculty", "slug"], name="unique_department_slug")]

    def __str__(self):
        return f"{self.name} — {self.faculty.university}"

    @property
    def university(self):
        return self.faculty.university


class Semester(models.IntegerChoices):
    FIRST = 1, "First semester"
    SECOND = 2, "Second semester"


class AcademicSession(models.Model):
    """An academic year, e.g. 2025/2026. One per university."""

    university = models.ForeignKey(University, on_delete=models.CASCADE, related_name="sessions")
    name = models.CharField(max_length=20, help_text="e.g. 2025/2026")
    starts_on = models.DateField(null=True, blank=True)
    is_current = models.BooleanField(default=False)

    class Meta:
        ordering = ["-name"]
        constraints = [models.UniqueConstraint(fields=["university", "name"], name="unique_session_name")]

    def __str__(self):
        return self.name


class Course(models.Model):
    department = models.ForeignKey(Department, on_delete=models.CASCADE, related_name="courses")
    code = models.CharField(max_length=12, help_text="e.g. CSC 301")
    title = models.CharField(max_length=150)
    slug = models.SlugField(max_length=40)
    units = models.PositiveSmallIntegerField(default=3)
    level = models.PositiveSmallIntegerField(choices=Level.choices)
    semester = models.PositiveSmallIntegerField(choices=Semester.choices)
    lecturer = models.CharField(max_length=120, blank=True)
    description = models.TextField(blank=True, max_length=1500)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["level", "code"]
        constraints = [
            models.UniqueConstraint(fields=["department", "slug"], name="unique_course_slug"),
            models.UniqueConstraint(fields=["department", "code"], name="unique_course_code"),
        ]
        indexes = [models.Index(fields=["department", "level"])]

    def __str__(self):
        return f"{self.code} — {self.title}"

    def save(self, *args, **kwargs):
        from django.utils.text import slugify

        self.code = " ".join(self.code.upper().split())
        if not self.slug:
            self.slug = slugify(self.code)
        super().save(*args, **kwargs)

    def get_absolute_url(self):
        from django.urls import reverse

        return reverse("spaces:course", args=[self.slug])

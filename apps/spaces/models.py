"""Spaces: the community system. Every course gets one official Space automatically.

Joining a course's Space is how a student takes/follows that course — Space
membership doubles as course enrollment, so the two can never disagree.
"""
from django.conf import settings
from django.db import models
from django.urls import reverse


class Space(models.Model):
    class Kind(models.TextChoices):
        COMMUNITY = "community", "Community"
        COURSE = "course", "Course"
        LEVEL = "level", "Level"
        DEPARTMENT = "department", "Department"

    department = models.ForeignKey("academics.Department", on_delete=models.CASCADE, related_name="spaces")
    kind = models.CharField(max_length=12, choices=Kind.choices, default=Kind.COMMUNITY)
    course = models.OneToOneField(
        "academics.Course", on_delete=models.CASCADE, null=True, blank=True, related_name="space"
    )
    level = models.PositiveSmallIntegerField(null=True, blank=True, help_text="For level Spaces")
    name = models.CharField(max_length=80)
    slug = models.SlugField(max_length=60)
    description = models.CharField(max_length=300, blank=True)
    rules = models.TextField(max_length=2000, blank=True)
    icon = models.CharField(max_length=4, blank=True, help_text="An emoji or short symbol")
    is_official = models.BooleanField(default=False)
    member_count = models.PositiveIntegerField(default=0)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-is_official", "name"]
        constraints = [models.UniqueConstraint(fields=["department", "slug"], name="unique_space_slug")]

    def __str__(self):
        return self.name

    def get_absolute_url(self):
        if self.course_id:
            return reverse("spaces:course", args=[self.course.slug])
        return reverse("spaces:detail", args=[self.slug])

    @property
    def glyph(self):
        return self.icon or self.name[:1].upper()


class SpaceMembership(models.Model):
    class Role(models.TextChoices):
        MEMBER = "member", "Member"
        MODERATOR = "moderator", "Moderator"

    space = models.ForeignKey(Space, on_delete=models.CASCADE, related_name="memberships")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="space_memberships")
    role = models.CharField(max_length=10, choices=Role.choices, default=Role.MEMBER)
    is_muted = models.BooleanField(default=False, help_text="Hide this Space's posts from my feeds")
    joined_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["space", "user"], name="unique_space_membership")]
        indexes = [models.Index(fields=["user", "is_muted"])]

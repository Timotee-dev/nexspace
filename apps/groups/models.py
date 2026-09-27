import secrets

from django.conf import settings
from django.db import models
from django.urls import reverse


def _invite_code():
    return secrets.token_urlsafe(8)


class StudyGroup(models.Model):
    department = models.ForeignKey("academics.Department", on_delete=models.CASCADE, related_name="study_groups")
    name = models.CharField(max_length=80)
    description = models.CharField(max_length=300, blank=True)
    course = models.ForeignKey("academics.Course", on_delete=models.SET_NULL, null=True, blank=True, related_name="study_groups")
    is_open = models.BooleanField(default=True, help_text="Open groups can be joined by anyone in the department")
    invite_code = models.CharField(max_length=20, unique=True, default=_invite_code)
    next_meeting_at = models.DateTimeField(null=True, blank=True)
    meeting_location = models.CharField(max_length=150, blank=True)
    meeting_link = models.URLField(blank=True)
    member_limit = models.PositiveSmallIntegerField(default=30)
    member_count = models.PositiveIntegerField(default=0)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["next_meeting_at", "-created_at"]

    def __str__(self):
        return self.name

    def get_absolute_url(self):
        return reverse("groups:detail", args=[self.pk])


class StudyGroupMembership(models.Model):
    group = models.ForeignKey(StudyGroup, on_delete=models.CASCADE, related_name="memberships")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="study_group_memberships")
    is_admin = models.BooleanField(default=False)
    joined_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["group", "user"], name="unique_group_member")]


class StudyGroupMessage(models.Model):
    group = models.ForeignKey(StudyGroup, on_delete=models.CASCADE, related_name="messages")
    author = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="+")
    body = models.TextField(max_length=1000)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at"]

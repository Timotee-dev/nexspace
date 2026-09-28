from django.conf import settings
from django.db import models


class Category(models.TextChoices):
    SOCIAL = "social", "Social"
    ACADEMIC = "academic", "Academic"
    DEPARTMENT = "department", "Department"
    OPPORTUNITIES = "opportunities", "Opportunities"


class Notification(models.Model):
    class Kind(models.TextChoices):
        REPLY = "reply", "Reply"
        MENTION = "mention", "Mention"
        FOLLOW = "follow", "New follower"
        VOTES = "votes", "Upvote milestone"
        ACCEPTED = "accepted", "Answer accepted"
        RESOURCE = "resource", "New course resource"
        PAST_QUESTION = "past_question", "New past question"
        EVENT_REMINDER = "event_reminder", "Academic reminder"
        ANNOUNCEMENT = "announcement", "Announcement"
        OPPORTUNITY = "opportunity", "New opportunity"
        OPPORTUNITY_REMINDER = "opportunity_reminder", "Deadline reminder"
        STUDY_GROUP = "study_group", "Study group"
        SPACE_REQUEST = "space_request", "Space join request"
        STAFF = "staff", "Staff verification"

    class PushStatus(models.TextChoices):
        NONE = "none", "Not sent"
        PENDING = "pending", "Waiting to send"
        SENT = "sent", "Sent"

    recipient = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="notifications")
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    category = models.CharField(max_length=14, choices=Category.choices)
    kind = models.CharField(max_length=22, choices=Kind.choices)
    text = models.CharField(max_length=240)
    url = models.CharField(max_length=300)
    is_critical = models.BooleanField(default=False)
    is_read = models.BooleanField(default=False)
    push_status = models.CharField(max_length=8, choices=PushStatus.choices, default=PushStatus.NONE)
    # Prevents duplicates (e.g. the same reminder twice) — unique per recipient when set.
    dedupe_key = models.CharField(max_length=120, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["recipient", "is_read", "-created_at"]),
            models.Index(fields=["recipient", "category", "-created_at"]),
            models.Index(fields=["push_status"]),
        ]
        constraints = [
            models.UniqueConstraint(fields=["recipient", "dedupe_key"], condition=~models.Q(dedupe_key=""),
                                    name="unique_notification_dedupe"),
        ]

    def __str__(self):
        return self.text


class NotificationPreference(models.Model):
    """Per-category in-app and push switches. Critical notifications ignore the in-app switch."""

    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="notification_prefs")
    social_in_app = models.BooleanField(default=True)
    social_push = models.BooleanField(default=False)
    academic_in_app = models.BooleanField(default=True)
    academic_push = models.BooleanField(default=True)
    department_in_app = models.BooleanField(default=True)
    department_push = models.BooleanField(default=True)
    opportunities_in_app = models.BooleanField(default=True)
    opportunities_push = models.BooleanField(default=False)

    def allows(self, category, channel):
        return getattr(self, f"{category}_{channel}")


class PushSubscription(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="push_subscriptions")
    endpoint = models.URLField(max_length=600, unique=True)
    p256dh = models.CharField(max_length=200)
    auth = models.CharField(max_length=60)
    user_agent = models.CharField(max_length=200, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    last_success_at = models.DateTimeField(null=True, blank=True)

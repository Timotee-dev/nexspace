from django.conf import settings
from django.db import models


class TargetType(models.TextChoices):
    POST = "post", "Post"
    COMMENT = "comment", "Comment"
    RESOURCE = "resource", "Resource"
    USER = "user", "User"
    SPACE = "space", "Space"


class Report(models.Model):
    class Reason(models.TextChoices):
        SPAM = "spam", "Spam"
        HARASSMENT = "harassment", "Harassment or bullying"
        ABUSE = "abuse", "Abuse or threats"
        INAPPROPRIATE = "inappropriate", "Inappropriate content"
        MISLEADING = "misleading", "Misleading information"
        COPYRIGHT = "copyright", "Copyright concern"
        OTHER = "other", "Something else"

    class Status(models.TextChoices):
        OPEN = "open", "Open"
        DISMISSED = "dismissed", "Dismissed"
        ACTIONED = "actioned", "Action taken"

    reporter = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="reports_made")
    department = models.ForeignKey("academics.Department", on_delete=models.CASCADE, related_name="+")
    target_type = models.CharField(max_length=10, choices=TargetType.choices)
    target_id = models.PositiveBigIntegerField()
    reason = models.CharField(max_length=16, choices=Reason.choices)
    details = models.CharField(max_length=500, blank=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.OPEN)
    resolved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    resolved_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(fields=["reporter", "target_type", "target_id"], name="one_report_per_target")
        ]
        indexes = [models.Index(fields=["department", "status", "-created_at"]),
                   models.Index(fields=["target_type", "target_id"])]


class ModerationAction(models.Model):
    """Permanent moderation log. Never shown to ordinary users."""

    class Action(models.TextChoices):
        DISMISS = "dismiss", "Dismissed reports"
        REMOVE = "remove", "Removed content"
        RESTORE = "restore", "Restored content"
        AUTO_HIDE = "auto_hide", "Auto-hidden after reports"
        SUSPEND = "suspend", "Suspended user"
        UNSUSPEND = "unsuspend", "Lifted suspension"
        BAN = "ban", "Banned user"
        UNBAN = "unban", "Unbanned user"
        REVEAL = "reveal", "Viewed anonymous author"
        DELETE_SPACE = "delete_space", "Deleted Space"

    moderator = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    department = models.ForeignKey("academics.Department", on_delete=models.CASCADE, related_name="+")
    action = models.CharField(max_length=14, choices=Action.choices)
    target_type = models.CharField(max_length=10, choices=TargetType.choices)
    target_id = models.PositiveBigIntegerField()
    target_label = models.CharField(max_length=200, blank=True)
    note = models.CharField(max_length=500, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["department", "-created_at"])]

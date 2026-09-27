from django.conf import settings
from django.db import models


class TrendingSnapshot(models.Model):
    """Latest trending posts/topics per department, recalculated by the scheduled job."""

    department = models.OneToOneField("academics.Department", on_delete=models.CASCADE, related_name="+")
    post_ids = models.JSONField(default=list)
    topic_ids = models.JSONField(default=list)
    computed_at = models.DateTimeField()


class OpportunityReminder(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="opportunity_reminders")
    post = models.ForeignKey("posts.Post", on_delete=models.CASCADE, related_name="reminders")
    days_before = models.PositiveSmallIntegerField(default=3)
    remind_on = models.DateField()
    sent_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["user", "post"], name="one_reminder_per_opportunity")]
        indexes = [models.Index(fields=["remind_on", "sent_at"])]

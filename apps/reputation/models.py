from django.conf import settings
from django.db import models


class NexScoreEvent(models.Model):
    """Append-only NexScore ledger. Profile.nexscore is a cached sum of `amount`.

    Reversals are new rows with the opposite amount, never edits or deletes.
    """

    class Reason(models.TextChoices):
        UPVOTE_RECEIVED = "upvote_received", "Upvote received"
        DOWNVOTE_RECEIVED = "downvote_received", "Downvote received"
        ANSWER_ACCEPTED = "answer_accepted", "Answer accepted"
        RESOURCE_RATED = "resource_rated", "Resource rated 4+ stars"
        RESOURCE_DOWNLOADED = "resource_downloaded", "Resource downloaded"
        CONTENT_REMOVED = "content_removed", "Content removed by a moderator"

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="nexscore_events")
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    reason = models.CharField(max_length=32, choices=Reason.choices)
    amount = models.IntegerField()
    source_type = models.CharField(max_length=40)
    source_id = models.PositiveBigIntegerField()
    is_reversal = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["user", "created_at"]),
            models.Index(fields=["source_type", "source_id"]),
        ]

    def __str__(self):
        return f"{self.user_id} {self.amount:+d} ({self.reason})"

from django.conf import settings
from django.db import models


class ResourceChunk(models.Model):
    """A passage of text extracted from a resource, used for grounded answers."""

    resource = models.ForeignKey("resources.Resource", on_delete=models.CASCADE, related_name="chunks")
    position = models.PositiveIntegerField()
    page = models.PositiveIntegerField(null=True, blank=True, help_text="PDF page or slide number")
    text = models.TextField()

    class Meta:
        ordering = ["resource", "position"]
        indexes = [models.Index(fields=["resource", "position"])]


class Usage(models.Model):
    """One row per NexAI request, for daily limits and cost tracking. The question text is not stored."""

    class Mode(models.TextChoices):
        ASK = "ask", "Ask"
        SUMMARY = "summary", "Summary"
        QUIZ = "quiz", "Quiz"
        INSIGHTS = "insights", "Past question insights"

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="nexai_usage")
    department = models.ForeignKey("academics.Department", on_delete=models.CASCADE, related_name="+", null=True)
    mode = models.CharField(max_length=10, choices=Mode.choices)
    used_model = models.BooleanField(default=False, help_text="False when answered from search only")
    input_tokens = models.PositiveIntegerField(default=0)
    output_tokens = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        indexes = [models.Index(fields=["user", "created_at"])]

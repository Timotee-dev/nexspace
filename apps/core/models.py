from django.conf import settings
from django.db import models


class AuditLog(models.Model):
    """Append-only record of sensitive actions (role changes, bans, identity lookups...)."""

    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="audit_actions"
    )
    action = models.CharField(max_length=64, db_index=True)
    target_type = models.CharField(max_length=64)
    target_id = models.CharField(max_length=64)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.action} on {self.target_type}:{self.target_id}"


def audit(actor, action: str, target, **metadata) -> AuditLog:
    return AuditLog.objects.create(
        actor=actor if getattr(actor, "is_authenticated", False) else None,
        action=action,
        target_type=target._meta.label_lower,
        target_id=str(target.pk),
        metadata=metadata,
    )

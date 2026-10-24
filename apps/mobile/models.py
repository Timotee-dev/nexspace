import hashlib
import secrets

from django.conf import settings
from django.db import models
from django.utils import timezone


def hash_key(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


class DeviceToken(models.Model):
    """Sign-in for the mobile app: one per phone. Only a hash is stored, so a database leak can't be used to
    sign in. Signing out, changing your password or being banned removes the token."""

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="device_tokens")
    key_hash = models.CharField(max_length=64, unique=True)
    name = models.CharField(max_length=80, blank=True, help_text="e.g. the phone model")
    created_at = models.DateTimeField(auto_now_add=True)
    last_used_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    @classmethod
    def issue(cls, user, name=""):
        key = secrets.token_urlsafe(32)
        cls.objects.create(user=user, key_hash=hash_key(key), name=(name or "")[:80])
        return key

    def touch(self):
        now = timezone.now()
        if not self.last_used_at or (now - self.last_used_at).total_seconds() > 300:
            DeviceToken.objects.filter(pk=self.pk).update(last_used_at=now)

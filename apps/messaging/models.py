from django.conf import settings
from django.db import models

MESSAGE_MAX = 2000


class Conversation(models.Model):
    """A one-to-one conversation. `key` ("<low id>:<high id>") guarantees one conversation per pair."""

    key = models.CharField(max_length=40, unique=True)
    department = models.ForeignKey("academics.Department", on_delete=models.CASCADE, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)
    last_message_at = models.DateTimeField(null=True, blank=True, db_index=True)

    class Meta:
        ordering = ["-last_message_at"]

    def __str__(self):
        return f"Conversation {self.key}"

    def get_absolute_url(self):
        return f"/messages/{self.pk}/"


class ConversationMember(models.Model):
    conversation = models.ForeignKey(Conversation, on_delete=models.CASCADE, related_name="members")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="conversation_memberships")
    last_read_at = models.DateTimeField(null=True, blank=True)
    is_muted = models.BooleanField(default=False)
    hidden_at = models.DateTimeField(null=True, blank=True, help_text="Removed from the inbox until a new message")

    class Meta:
        constraints = [models.UniqueConstraint(fields=["conversation", "user"], name="one_membership_per_conversation")]
        indexes = [models.Index(fields=["user", "conversation"])]


class Message(models.Model):
    conversation = models.ForeignKey(Conversation, on_delete=models.CASCADE, related_name="messages")
    sender = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="sent_messages")
    body = models.TextField(max_length=MESSAGE_MAX)
    is_deleted = models.BooleanField(default=False)
    is_hidden = models.BooleanField(default=False, help_text="Hidden by a moderator after a report")
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["created_at", "id"]
        indexes = [models.Index(fields=["conversation", "id"])]


class Block(models.Model):
    """`blocker` blocked `blocked`: neither can message the other."""

    blocker = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="blocks_made")
    blocked = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="blocks_received")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["blocker", "blocked"], name="one_block_per_pair")]

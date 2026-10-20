from django.conf import settings
from django.db import models

MESSAGE_MAX = 2000


GROUP_MAX_MEMBERS = 50
MAX_MESSAGE_ATTACHMENTS = 4


class Conversation(models.Model):
    """A one-to-one chat (`key` = "<low id>:<high id>", one per pair) or a group chat (`is_group`, no key)."""

    key = models.CharField(max_length=40, unique=True, null=True, blank=True)
    is_group = models.BooleanField(default=False)
    title = models.CharField(max_length=80, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
                                   related_name="+")
    department = models.ForeignKey("academics.Department", on_delete=models.CASCADE, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)
    last_message_at = models.DateTimeField(null=True, blank=True, db_index=True)

    class Meta:
        ordering = ["-last_message_at"]

    def __str__(self):
        return self.title if self.is_group else f"Conversation {self.key}"

    def get_absolute_url(self):
        return f"/messages/{self.pk}/"


class ConversationMember(models.Model):
    conversation = models.ForeignKey(Conversation, on_delete=models.CASCADE, related_name="members")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="conversation_memberships")
    last_read_at = models.DateTimeField(null=True, blank=True)
    is_muted = models.BooleanField(default=False)
    hidden_at = models.DateTimeField(null=True, blank=True, help_text="Removed from the inbox until a new message")
    is_admin = models.BooleanField(default=False, help_text="Group admins can rename the group and add or remove people")
    joined_at = models.DateTimeField(auto_now_add=True, null=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["conversation", "user"], name="one_membership_per_conversation")]
        indexes = [models.Index(fields=["user", "conversation"])]


class Message(models.Model):
    conversation = models.ForeignKey(Conversation, on_delete=models.CASCADE, related_name="messages")
    sender = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="sent_messages")
    body = models.TextField(max_length=MESSAGE_MAX, blank=True)
    is_system = models.BooleanField(default=False, help_text='Group events like "Ada added Bayo"')
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


def message_upload_path(instance, filename):
    import posixpath
    import uuid

    ext = posixpath.splitext(filename)[1].lower()[:10]
    return f"messages/{instance.message.conversation_id}/{uuid.uuid4().hex}{ext}"


class MessageAttachment(models.Model):
    class Kind(models.TextChoices):
        IMAGE = "image", "Image"
        FILE = "file", "File"

    message = models.ForeignKey(Message, on_delete=models.CASCADE, related_name="attachments")
    kind = models.CharField(max_length=5, choices=Kind.choices)
    file = models.FileField(upload_to=message_upload_path, max_length=255)
    original_name = models.CharField(max_length=150)
    size = models.PositiveIntegerField(default=0)
    content_type = models.CharField(max_length=100, blank=True)

    class Meta:
        ordering = ["id"]

    @property
    def extension(self):
        return self.original_name.rsplit(".", 1)[-1].upper()[:5] if "." in self.original_name else "FILE"

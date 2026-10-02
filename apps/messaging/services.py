"""Direct messages: one-to-one conversations inside a department.

Who can message whom: both people must be in the same department, the sender must be verified and not
suspended, neither may have blocked the other, and the recipient's "Allow messages from" setting must
allow it (anyone in my department / only people I follow / nobody). Department admins and platform
admins can always reach people in their department (unless blocked) for official matters.
"""
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Exists, F, OuterRef, Q
from django.utils import timezone

from apps.accounts.models import RoleAssignment
from apps.posts.services import _limit, require_verified

from .models import MESSAGE_MAX, Block, Conversation, ConversationMember, Message

SEND_RATE = (30, 60)  # 30 messages a minute
NEW_CONVERSATION_RATE = (20, 60 * 60)  # 20 new conversations an hour


def is_blocked(a, b) -> bool:
    return Block.objects.filter(Q(blocker=a, blocked=b) | Q(blocker=b, blocked=a)).exists()


def can_message(sender, recipient):
    """Returns (allowed, reason shown to the sender)."""
    if sender.pk == recipient.pk:
        return False, "You can't message yourself."
    if not recipient.is_active:
        return False, "This account isn't available."
    if sender.department_id is None or sender.department_id != recipient.department_id:
        return False, "You can only message people in your department."
    if is_blocked(sender, recipient):
        return False, "You can't message this person."
    official = sender.is_platform_admin or sender.has_role(RoleAssignment.Role.DEPARTMENT_ADMIN,
                                                            department=recipient.department)
    setting = recipient.profile.allow_messages_from
    if setting == "nobody" and not official:
        return False, f"{recipient.full_name} isn't accepting messages."
    if setting == "following" and not official:
        from apps.social.models import UserFollow

        if not UserFollow.objects.filter(follower=recipient, following=sender).exists():
            return False, f"{recipient.full_name} only accepts messages from people they follow."
    return True, ""


def _key(a, b):
    low, high = sorted([a.pk, b.pk])
    return f"{low}:{high}"


def other_member(conversation, user):
    return next((m.user for m in conversation.members.select_related("user__profile") if m.user_id != user.pk), None)


@transaction.atomic
def start_conversation(sender, recipient):
    """Get or create the conversation between two people (checks permission first)."""
    require_verified(sender)
    allowed, reason = can_message(sender, recipient)
    if not allowed:
        raise PermissionDenied(reason)
    existing = Conversation.objects.filter(key=_key(sender, recipient)).first()
    if existing:
        return existing
    _limit(sender, "dm-new", NEW_CONVERSATION_RATE)
    try:
        with transaction.atomic():
            conversation = Conversation.objects.create(key=_key(sender, recipient), department_id=sender.department_id)
            ConversationMember.objects.bulk_create([ConversationMember(conversation=conversation, user=sender),
                                                    ConversationMember(conversation=conversation, user=recipient)])
    except IntegrityError:  # created by the other person at the same moment
        conversation = Conversation.objects.get(key=_key(sender, recipient))
    return conversation


def membership(user, conversation):
    member = ConversationMember.objects.filter(conversation=conversation, user=user).first()
    if member is None:
        raise PermissionDenied("You're not part of this conversation.")
    return member


@transaction.atomic
def send_message(*, sender, conversation, body):
    require_verified(sender)
    membership(sender, conversation)
    recipient = other_member(conversation, sender)
    if recipient is None:
        raise ValidationError("This conversation has ended.")
    allowed, reason = can_message(sender, recipient)
    if not allowed:
        raise PermissionDenied(reason)
    body = (body or "").strip()
    if not body:
        raise ValidationError("Write a message first.")
    if len(body) > MESSAGE_MAX:
        raise ValidationError(f"Messages can be up to {MESSAGE_MAX} characters.")
    _limit(sender, "dm", SEND_RATE)
    message = Message.objects.create(conversation=conversation, sender=sender, body=body)
    now = message.created_at
    Conversation.objects.filter(pk=conversation.pk).update(last_message_at=now)
    ConversationMember.objects.filter(conversation=conversation).update(hidden_at=None)
    ConversationMember.objects.filter(conversation=conversation, user=sender).update(last_read_at=now)
    from apps.notifications import services as notifications

    recipient_member = ConversationMember.objects.get(conversation=conversation, user=recipient)
    if not recipient_member.is_muted:
        notifications.message_received(message, recipient)
    return message


def mark_read(user, conversation):
    ConversationMember.objects.filter(conversation=conversation, user=user).update(last_read_at=timezone.now())


def _unread_exists(user):
    return Exists(Message.objects.filter(
        conversation=OuterRef("conversation"), is_deleted=False, created_at__gt=OuterRef("last_read_at"),
    ).exclude(sender=user))


def _any_from_others(user):
    return Exists(Message.objects.filter(conversation=OuterRef("conversation"), is_deleted=False).exclude(sender=user))


def unread_count(user) -> int:
    """Conversations with at least one message the user hasn't read."""
    members = ConversationMember.objects.filter(user=user, hidden_at__isnull=True)
    never_read = members.filter(last_read_at__isnull=True).filter(_any_from_others(user)).count()
    return never_read + members.filter(last_read_at__isnull=False).filter(_unread_exists(user)).count()


def inbox(user, limit=50):
    members = (ConversationMember.objects.filter(user=user, hidden_at__isnull=True,
                                                 conversation__last_message_at__isnull=False)
               .select_related("conversation").order_by("-conversation__last_message_at")[:limit])
    rows = []
    for m in members:
        conv = m.conversation
        last = conv.messages.order_by("-id").first()
        other = other_member(conv, user)
        unread = conv.messages.filter(is_deleted=False).exclude(sender=user).filter(
            created_at__gt=m.last_read_at) .exists() if m.last_read_at else \
            conv.messages.filter(is_deleted=False).exclude(sender=user).exists()
        rows.append({"conversation": conv, "other": other, "last": last, "unread": unread, "muted": m.is_muted})
    return rows


def messages_after(conversation, after_id=0, limit=200):
    return list(conversation.messages.filter(id__gt=after_id).select_related("sender").order_by("id")[:limit])


@transaction.atomic
def delete_message(*, user, message):
    if message.sender_id != user.pk:
        raise PermissionDenied("You can only delete your own messages.")
    message.is_deleted = True
    message.body = ""
    message.save(update_fields=["is_deleted", "body"])


def hide_conversation(user, conversation):
    ConversationMember.objects.filter(conversation=conversation, user=user).update(hidden_at=timezone.now())


def toggle_mute(user, conversation) -> bool:
    member = membership(user, conversation)
    member.is_muted = not member.is_muted
    member.save(update_fields=["is_muted"])
    return member.is_muted


def block(user, other):
    if user.pk == other.pk:
        raise ValidationError("You can't block yourself.")
    Block.objects.get_or_create(blocker=user, blocked=other)


def unblock(user, other):
    Block.objects.filter(blocker=user, blocked=other).delete()


def has_blocked(user, other) -> bool:
    return Block.objects.filter(blocker=user, blocked=other).exists()

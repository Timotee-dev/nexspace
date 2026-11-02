"""Fresh start: wipe all test activity before launching, keeping the setup.

KEPT: the platform owner's account (and their roles, settings, signed-in phones), universities, faculties,
departments, academic sessions, courses, topics, and the official Spaces (department, level and course
Spaces) with the owner's memberships.

DELETED: every other account (students, staff, test accounts) and everything anyone created — posts,
comments, polls, votes, bookmarks, messages and group chats, course materials, announcements, calendar
events, study groups, community Spaces, reports, moderation history, notifications, NexScore history,
NexAI usage, trending data — plus the uploaded files (photos, attachments, materials, avatars).
The audit log is kept, with an entry recording the fresh start.
"""
import logging

from django.core.cache import cache
from django.db import transaction
from django.db.models import Count

logger = logging.getLogger(__name__)
CONFIRM_PHRASE = "DELETE ALL TEST DATA"


def _plan(owner):
    from apps.accounts.models import User
    from apps.messaging.models import Conversation, Message
    from apps.notices.models import AcademicEvent, Announcement
    from apps.posts.models import Comment, Post
    from apps.resources.models import Resource
    from apps.spaces.models import Space
    from apps.groups.models import StudyGroup

    return [
        ("Accounts (everyone except you)", User.objects.exclude(pk=owner.pk)),
        ("Posts", Post.objects.all()),
        ("Comments", Comment.objects.all()),
        ("Chats", Conversation.objects.all()),
        ("Messages", Message.objects.all()),
        ("Course materials", Resource.objects.all()),
        ("Announcements", Announcement.objects.all()),
        ("Calendar events", AcademicEvent.objects.all()),
        ("Study groups", StudyGroup.objects.all()),
        ("Community Spaces", Space.objects.filter(kind=Space.Kind.COMMUNITY)),
    ]


def preview(owner):
    """What a fresh start would delete, as (label, count) rows."""
    return [(label, qs.count()) for label, qs in _plan(owner)]


def _stored_files(owner):
    """Every uploaded file that will lose its database row (deleted from storage after the wipe)."""
    from apps.accounts.models import Profile
    from apps.messaging.models import MessageAttachment
    from apps.notices.models import Announcement
    from apps.posts.models import Attachment
    from apps.resources.models import Resource

    files = []
    for model, field in ((Attachment, "file"), (MessageAttachment, "file"), (Resource, "file"),
                         (Announcement, "attachment")):
        files += [getattr(obj, field) for obj in model.objects.exclude(**{field: ""}).only("pk", field)]
    files += [p.avatar for p in Profile.objects.exclude(user=owner).exclude(avatar="").only("pk", "avatar")]
    return files


def fresh_start(owner):
    """Wipe everything listed above. Returns the counts that were deleted."""
    from apps.accounts.models import Profile, User
    from apps.core.models import audit
    from apps.discover.models import OpportunityReminder, TrendingSnapshot
    from apps.groups.models import StudyGroup
    from apps.messaging.models import Block, Conversation
    from apps.moderation.models import ModerationAction, Report
    from apps.nexai.models import Usage
    from apps.notices.models import AcademicEvent, Announcement
    from apps.notifications.models import DigestLog, Notification
    from apps.posts.models import Bookmark, Comment, Post
    from apps.reputation.models import NexScoreEvent
    from apps.resources.models import Resource
    from apps.social.models import TopicFollow, UserFollow
    from apps.spaces.models import Space, SpaceJoinRequest

    if not owner.is_owner:
        raise PermissionError("Only the platform owner can do a fresh start.")
    counts = dict(preview(owner))
    files = _stored_files(owner)

    with transaction.atomic():
        # Content first (some of it would otherwise survive with "Deleted user" as the author).
        for qs in (Report.objects.all(), ModerationAction.objects.all(), Notification.objects.all(),
                   DigestLog.objects.all(), OpportunityReminder.objects.all(), TrendingSnapshot.objects.all(),
                   Bookmark.objects.all(), Comment.objects.all(), Post.objects.all(), Conversation.objects.all(),
                   Block.objects.all(), Resource.objects.all(), Announcement.objects.all(), AcademicEvent.objects.all(),
                   StudyGroup.objects.all(), SpaceJoinRequest.objects.all(), Usage.objects.all(),
                   NexScoreEvent.objects.all(), UserFollow.objects.all(), TopicFollow.objects.exclude(user=owner),
                   Space.objects.filter(kind=Space.Kind.COMMUNITY)):
            qs.delete()
        # Then every account except the owner (their roles, devices, codes and memberships go with them).
        User.objects.exclude(pk=owner.pk).delete()
        # Tidy what's left so counters match reality.
        for space in Space.objects.annotate(n=Count("memberships")):
            if space.member_count != space.n:
                Space.objects.filter(pk=space.pk).update(member_count=space.n)
        Profile.objects.filter(user=owner).update(nexscore=0)
        audit(owner, "platform.fresh_start", owner, **{k.split(" (")[0].lower().replace(" ", "_"): v
                                                        for k, v in counts.items()})

    removed = 0
    for fieldfile in files:  # storage isn't part of the database transaction: tidy it afterwards
        try:
            fieldfile.storage.delete(fieldfile.name)
            removed += 1
        except Exception:
            logger.warning("Fresh start: couldn't delete stored file %s", getattr(fieldfile, "name", "?"))
    cache.clear()  # counters, badges, rate limits and cached feeds from the test period
    counts["Files deleted from storage"] = removed
    return counts

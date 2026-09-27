"""Post, comment, vote, poll and bookmark business logic.

Shared by the template views and the API so rules live in exactly one place.
Every function here assumes the caller already checked the user is signed in;
the verified-email gate and visibility checks are enforced here too.
"""
import re

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.db.models import F
from django.utils import timezone

from apps.accounts.models import RoleAssignment, User
from apps.core import ratelimit
from apps.reputation import rules
from apps.reputation import services as nexscore

from .models import (
    MAX_COMMENT_DEPTH, MAX_TOPICS, Attachment, Bookmark, Comment, CommentVote, EventDetails,
    OpportunityDetails, Poll, PollOption, PollVote, Post, PostVote,
)

MENTION_RE = re.compile(r"(?<![\w@])@([a-z0-9_]{3,30})\b", re.IGNORECASE)
MAX_IMAGES = 4
MAX_FILES = 3

POST_RATE = (10, 10 * 60)      # 10 posts per 10 minutes
COMMENT_RATE = (30, 10 * 60)   # 30 comments per 10 minutes
VOTE_RATE = (60, 60)           # 60 votes per minute


class RateLimited(Exception):
    pass


def require_verified(user):
    """Write gate: verified email, active, not suspended."""
    if not user.email_verified:
        raise PermissionDenied("Verify your email address to do this.")
    if user.is_suspended:
        raise PermissionDenied(
            f"Your account is suspended until {user.suspended_until:%d %b %Y}. You can still read NexSpace."
        )


def _limit(user, action, rate):
    if not ratelimit.allow(f"{action}:{user.pk}", *rate):
        raise RateLimited("You're doing that too fast. Wait a moment and try again.")


def can_view(user, post) -> bool:
    if post.is_deleted or user.department_id is None or post.department_id != user.department_id:
        return False
    return not post.is_hidden or post.author_id == user.pk or user.can_moderate(post.department)


def can_post_official(user) -> bool:
    return user.has_role(RoleAssignment.Role.DEPARTMENT_ADMIN, department=user.department)


def extract_mentions(text, exclude=None):
    usernames = {m.lower() for m in MENTION_RE.findall(text or "")}
    users = User.objects.filter(username__in=usernames, is_active=True)
    if exclude is not None:
        users = users.exclude(pk=exclude.pk)
    return list(users)


# --- Posts ------------------------------------------------------------------
@transaction.atomic
def create_post(
    *, author, kind, body="", title="", topics=(), is_anonymous=False, is_official=False,
    images=(), files=(), poll_options=(), poll_multiple=False, poll_closes_at=None,
    event=None, opportunity=None, space=None,
):
    require_verified(author)
    _limit(author, "post", POST_RATE)
    if author.department_id is None:
        raise ValidationError("Join a department before posting.")
    body, title = (body or "").strip(), (title or "").strip()

    if kind not in Post.Kind.values:
        raise ValidationError("Choose a post type.")
    if is_anonymous and kind not in Post.ANONYMOUS_KINDS:
        raise ValidationError("Only posts and questions can be anonymous.")
    if is_official and not can_post_official(author):
        raise PermissionDenied("Only department admins can post official updates.")
    if is_official and is_anonymous:
        raise ValidationError("Official updates can't be anonymous.")
    if space is not None:
        from apps.spaces.models import SpaceMembership

        if space.department_id != author.department_id or not SpaceMembership.objects.filter(
            space=space, user=author
        ).exists():
            raise PermissionDenied("Join this Space before posting in it.")
    topics = list(topics)
    if len(topics) > MAX_TOPICS:
        raise ValidationError(f"Pick up to {MAX_TOPICS} topics.")
    if len(images) > MAX_IMAGES:
        raise ValidationError(f"Add up to {MAX_IMAGES} images.")
    if len(files) > MAX_FILES:
        raise ValidationError(f"Add up to {MAX_FILES} files.")

    if kind in (Post.Kind.POST, Post.Kind.QUESTION) and not body and not images and not files:
        raise ValidationError("Write something before posting.")
    if kind == Post.Kind.POLL:
        options = [o.strip() for o in poll_options if o and o.strip()]
        if not body:
            raise ValidationError("Ask your poll question.")
        if not 2 <= len(options) <= 6:
            raise ValidationError("Polls need between 2 and 6 options.")
        if len({o.lower() for o in options}) != len(options):
            raise ValidationError("Poll options must be different from each other.")
        if poll_closes_at and poll_closes_at <= timezone.now():
            raise ValidationError("The closing time must be in the future.")
    if kind in (Post.Kind.EVENT, Post.Kind.OPPORTUNITY) and not title:
        raise ValidationError("Give it a title.")
    if kind == Post.Kind.EVENT and not (event and event.get("starts_at")):
        raise ValidationError("Add when the event starts.")
    if kind == Post.Kind.OPPORTUNITY and not (opportunity and opportunity.get("organization")
                                              and opportunity.get("category")):
        raise ValidationError("Add the organization and category.")

    post = Post.objects.create(
        author=author, department_id=author.department_id, kind=kind, title=title, body=body,
        is_anonymous=is_anonymous, is_official=is_official, space=space,
    )
    post.topics.set(topics)
    post.mentions.set(extract_mentions(f"{title} {body}", exclude=author))

    from apps.core.uploads import image_content_type, optimize_image, validate_document_upload

    for i, image in enumerate(images):
        content_type = image_content_type(image)
        image = optimize_image(image)
        Attachment.objects.create(post=post, kind=Attachment.Kind.IMAGE, file=image, position=i,
                                  original_name=image.name[:150], size=image.size, content_type=content_type)
    for i, upload in enumerate(files):
        content_type = validate_document_upload(upload)
        Attachment.objects.create(post=post, kind=Attachment.Kind.FILE, file=upload, position=i,
                                  original_name=upload.name[:150], size=upload.size, content_type=content_type)

    if kind == Post.Kind.POLL:
        poll = Poll.objects.create(post=post, allows_multiple=poll_multiple, closes_at=poll_closes_at)
        PollOption.objects.bulk_create(
            [PollOption(poll=poll, text=text[:80], position=i) for i, text in enumerate(options)]
        )
    elif kind == Post.Kind.EVENT:
        EventDetails.objects.create(post=post, **event)
    elif kind == Post.Kind.OPPORTUNITY:
        OpportunityDetails.objects.create(post=post, **opportunity)
    from apps.notifications import services as notifications

    notifications.post_created(post)
    return post


@transaction.atomic
def delete_post(*, user, post):
    if post.author_id != user.pk:
        raise PermissionDenied("You can only delete your own posts.")
    if post.is_deleted:
        return
    post.is_deleted = True
    post.deleted_at = timezone.now()
    post.save(update_fields=["is_deleted", "deleted_at"])
    nexscore.reverse(source=post)
    for comment in post.comments.all():
        nexscore.reverse(source=comment)


# --- Votes ------------------------------------------------------------------
def _apply_vote(*, user, target, vote_model, fk_name, value, author, anonymous):
    """Shared vote logic. value: 1, -1 or 0 (remove). Returns (score, my_vote)."""
    if value not in (1, -1, 0):
        raise ValidationError("Invalid vote.")
    require_verified(user)
    if author.pk == user.pk:
        raise PermissionDenied("You can't vote on your own content.")
    _limit(user, "vote", VOTE_RATE)

    with transaction.atomic():
        type(target).objects.select_for_update().filter(pk=target.pk).first()
        existing = vote_model.objects.filter(user=user, **{fk_name: target}).first()
        old = existing.value if existing else 0
        if old == value:
            return type(target).objects.get(pk=target.pk).score, value
        if existing and value == 0:
            existing.delete()
        elif existing:
            existing.value = value
            existing.save(update_fields=["value"])
        else:
            vote_model.objects.create(user=user, value=value, **{fk_name: target})
        type(target).objects.filter(pk=target.pk).update(score=F("score") + (value - old))

        # NexScore: undo whatever this voter's previous vote earned, then award the new one.
        if old:
            nexscore.reverse(source=target, actor=user)
        if value == 1:
            nexscore.award(user=author, actor=user, reason="upvote_received",
                           amount=rules.UPVOTE_RECEIVED, source=target, anonymous=anonymous)
        elif value == -1:
            nexscore.award(user=author, actor=user, reason="downvote_received",
                           amount=rules.DOWNVOTE_RECEIVED, source=target, anonymous=anonymous)
    new_score = type(target).objects.get(pk=target.pk).score
    if value - old > 0:
        from apps.notifications import services as notifications

        target.author = author
        notifications.score_changed(target, new_score, new_score - (value - old))
    return new_score, value


def vote_post(*, user, post, value):
    if not can_view(user, post):
        raise PermissionDenied("You can't vote on this post.")
    return _apply_vote(user=user, target=post, vote_model=PostVote, fk_name="post", value=value,
                       author=post.author, anonymous=post.is_anonymous)


def vote_comment(*, user, comment, value):
    if comment.is_deleted or not can_view(user, comment.post):
        raise PermissionDenied("You can't vote on this comment.")
    return _apply_vote(user=user, target=comment, vote_model=CommentVote, fk_name="comment", value=value,
                       author=comment.author, anonymous=False)


# --- Comments ---------------------------------------------------------------
@transaction.atomic
def add_comment(*, user, post, body, parent=None):
    require_verified(user)
    if not can_view(user, post):
        raise PermissionDenied("You can't comment on this post.")
    _limit(user, "comment", COMMENT_RATE)
    body = (body or "").strip()
    if not body:
        raise ValidationError("Write a comment first.")
    reply_to = None
    depth = 1
    if parent is not None:
        if parent.post_id != post.pk or parent.is_deleted:
            raise ValidationError("You can't reply to that comment.")
        if parent.depth >= MAX_COMMENT_DEPTH:
            # Flatten: attach to the depth-3 comment's parent and note who we're replying to.
            reply_to = parent.author
            parent = parent.parent
        depth = parent.depth + 1
    comment = Comment.objects.create(
        post=post, author=user, parent=parent, depth=depth, reply_to=reply_to, body=body
    )
    comment.mentions.set(extract_mentions(body, exclude=user))
    Post.objects.filter(pk=post.pk).update(comment_count=F("comment_count") + 1)
    from apps.notifications import services as notifications

    notifications.comment_created(comment)
    return comment


@transaction.atomic
def delete_comment(*, user, comment):
    if comment.author_id != user.pk:
        raise PermissionDenied("You can only delete your own comments.")
    if comment.is_deleted:
        return
    comment.is_deleted = True
    comment.body = ""
    comment.save(update_fields=["is_deleted", "body"])
    Post.objects.filter(pk=comment.post_id, comment_count__gt=0).update(comment_count=F("comment_count") - 1)
    nexscore.reverse(source=comment)
    post = comment.post
    if post.accepted_comment_id == comment.pk:
        post.accepted_comment = None
        post.save(update_fields=["accepted_comment"])


@transaction.atomic
def accept_answer(*, user, comment):
    """Question author marks (or unmarks) one answer as accepted."""
    require_verified(user)
    post = Post.objects.select_for_update().get(pk=comment.post_id)
    if post.kind != Post.Kind.QUESTION or post.author_id != user.pk:
        raise PermissionDenied("Only the person who asked can accept an answer.")
    if comment.is_deleted or comment.author_id == user.pk:
        raise ValidationError("You can't accept that comment.")
    previous = post.accepted_comment
    if previous is not None:
        nexscore.reverse(source=previous, reason="answer_accepted")
    if previous is not None and previous.pk == comment.pk:
        post.accepted_comment = None
    else:
        post.accepted_comment = comment
        nexscore.award(user=comment.author, actor=user, reason="answer_accepted",
                       amount=rules.ANSWER_ACCEPTED, source=comment)
        from apps.notifications import services as notifications

        notifications.answer_accepted(comment, user)
    post.save(update_fields=["accepted_comment"])
    return post.accepted_comment


# --- Polls ------------------------------------------------------------------
@transaction.atomic
def vote_poll(*, user, post, option_ids):
    require_verified(user)
    if not can_view(user, post) or post.kind != Post.Kind.POLL:
        raise PermissionDenied("You can't vote in this poll.")
    poll = Poll.objects.select_for_update().get(post=post)
    if poll.is_closed:
        raise ValidationError("This poll has closed.")
    if PollVote.objects.filter(poll=poll, user=user).exists():
        raise ValidationError("You've already voted in this poll.")
    option_ids = {int(i) for i in option_ids}
    if not option_ids:
        raise ValidationError("Pick an option.")
    if len(option_ids) > 1 and not poll.allows_multiple:
        raise ValidationError("Pick only one option.")
    options = list(poll.options.filter(pk__in=option_ids))
    if len(options) != len(option_ids):
        raise ValidationError("That option isn't part of this poll.")
    try:
        PollVote.objects.bulk_create([PollVote(poll=poll, option=o, user=user) for o in options])
    except IntegrityError:
        raise ValidationError("You've already voted in this poll.")
    PollOption.objects.filter(pk__in=option_ids).update(vote_count=F("vote_count") + 1)
    return poll


# --- Bookmarks --------------------------------------------------------------
def toggle_bookmark(*, user, post) -> bool:
    if not can_view(user, post):
        raise PermissionDenied("You can't save this post.")
    deleted, _ = Bookmark.objects.filter(user=user, post=post).delete()
    if deleted:
        return False
    try:
        with transaction.atomic():
            Bookmark.objects.create(user=user, post=post)
    except IntegrityError:
        pass
    return True

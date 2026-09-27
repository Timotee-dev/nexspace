from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.db.models import F
from django.utils import timezone

from apps.posts.services import COMMENT_RATE, _limit, require_verified

from .models import StudyGroup, StudyGroupMembership, StudyGroupMessage


def is_member(user, group):
    return StudyGroupMembership.objects.filter(group=group, user=user).exists()


def is_admin(user, group):
    return StudyGroupMembership.objects.filter(group=group, user=user, is_admin=True).exists()


@transaction.atomic
def create_group(*, user, name, description="", course=None, is_open=True, next_meeting_at=None,
                 meeting_location="", meeting_link=""):
    require_verified(user)
    name = " ".join((name or "").split())
    if len(name) < 3:
        raise ValidationError("Give the group a name of at least 3 characters.")
    if course is not None and course.department_id != user.department_id:
        raise ValidationError("Choose a course in your department.")
    if next_meeting_at and next_meeting_at < timezone.now():
        raise ValidationError("The next meeting must be in the future.")
    group = StudyGroup.objects.create(
        department_id=user.department_id, name=name[:80], description=(description or "")[:300], course=course,
        is_open=is_open, next_meeting_at=next_meeting_at, meeting_location=meeting_location[:150],
        meeting_link=meeting_link, created_by=user, member_count=1,
    )
    StudyGroupMembership.objects.create(group=group, user=user, is_admin=True)
    return group


@transaction.atomic
def join(*, user, group, invite_code=None):
    require_verified(user)
    if group.department_id != user.department_id:
        raise PermissionDenied("This group is for another department.")
    if not group.is_open and invite_code != group.invite_code:
        raise PermissionDenied("This group is invite-only. Ask a member for the invite link.")
    group = StudyGroup.objects.select_for_update().get(pk=group.pk)
    if group.member_count >= group.member_limit:
        raise ValidationError("This group is full.")
    try:
        with transaction.atomic():
            StudyGroupMembership.objects.create(group=group, user=user)
    except IntegrityError:
        return False
    StudyGroup.objects.filter(pk=group.pk).update(member_count=F("member_count") + 1)
    return True


@transaction.atomic
def leave(*, user, group):
    membership = StudyGroupMembership.objects.filter(group=group, user=user).first()
    if membership is None:
        return False
    membership.delete()
    StudyGroup.objects.filter(pk=group.pk, member_count__gt=0).update(member_count=F("member_count") - 1)
    remaining = StudyGroupMembership.objects.filter(group=group)
    if not remaining.exists():
        group.delete()
    elif membership.is_admin and not remaining.filter(is_admin=True).exists():
        first = remaining.order_by("joined_at").first()
        first.is_admin = True
        first.save(update_fields=["is_admin"])
    return True


def update_meeting(*, user, group, next_meeting_at, meeting_location="", meeting_link=""):
    if not is_admin(user, group):
        raise PermissionDenied("Only group admins can change the meeting.")
    if next_meeting_at and next_meeting_at < timezone.now():
        raise ValidationError("The next meeting must be in the future.")
    group.next_meeting_at, group.meeting_location, group.meeting_link = next_meeting_at, meeting_location[:150], meeting_link
    group.save(update_fields=["next_meeting_at", "meeting_location", "meeting_link"])
    from apps.notifications import services as notifications
    from apps.notifications.models import Category, Notification

    if next_meeting_at:
        members = [m.user for m in group.memberships.select_related("user")]
        notifications.notify(members, category=Category.ACADEMIC, kind=Notification.Kind.STUDY_GROUP,
                             text=f"{group.name}: next meeting {timezone.localtime(next_meeting_at):%a %d %b, %H:%M}",
                             url=group.get_absolute_url(), actor=user)
    return group


def post_message(*, user, group, body):
    require_verified(user)
    if not is_member(user, group):
        raise PermissionDenied("Join the group to post.")
    _limit(user, "comment", COMMENT_RATE)
    body = (body or "").strip()
    if not body:
        raise ValidationError("Write a message first.")
    return StudyGroupMessage.objects.create(group=group, author=user, body=body[:1000])

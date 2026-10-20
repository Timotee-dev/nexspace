"""NexScore ledger operations with anti-gaming caps."""
from django.db import transaction
from django.db.models import F, Sum
from django.utils import timezone

from apps.accounts.models import Profile

from . import rules
from .models import NexScoreEvent


def _source_key(source):
    return source._meta.label_lower, source.pk


def _today_start():
    now = timezone.localtime()
    return now.replace(hour=0, minute=0, second=0, microsecond=0)


def _apply(user, actor, reason, amount, source, is_reversal=False):
    source_type, source_id = _source_key(source)
    event = NexScoreEvent.objects.create(
        user=user, actor=actor, reason=reason, amount=amount,
        source_type=source_type, source_id=source_id, is_reversal=is_reversal,
    )
    Profile.objects.filter(user=user).update(nexscore=F("nexscore") + amount)
    return event


@transaction.atomic
def award(*, user, reason, amount, source, actor=None, anonymous=False):
    """Give (or take) points. Returns the event, or None if nothing was recorded."""
    if anonymous or amount == 0:
        return None
    if actor is not None and (actor.pk == user.pk or not actor.email_verified):
        return None
    if amount > 0:
        earned_today = (
            NexScoreEvent.objects.filter(user=user, created_at__gte=_today_start(), amount__gt=0, is_reversal=False)
            .aggregate(total=Sum("amount"))["total"] or 0
        )
        amount = min(amount, rules.DAILY_CAP - earned_today)
        if actor is not None and reason in rules.VOTE_REASONS:
            from_actor = (
                NexScoreEvent.objects.filter(
                    user=user, actor=actor, created_at__gte=_today_start(),
                    reason__in=rules.VOTE_REASONS, amount__gt=0, is_reversal=False,
                ).aggregate(total=Sum("amount"))["total"] or 0
            )
            amount = min(amount, rules.PER_ACTOR_DAILY_CAP - from_actor)
        if amount <= 0:
            return None
    return _apply(user, actor, reason, amount, source)


@transaction.atomic
def reverse(*, source, reason=None, actor=None):
    """Undo the net points a source earned (optionally only for one reason/actor)."""
    source_type, source_id = _source_key(source)
    events = NexScoreEvent.objects.filter(source_type=source_type, source_id=source_id)
    if reason:
        events = events.filter(reason=reason)
    if actor is not None:
        events = events.filter(actor=actor)
    totals = events.values("user", "reason", "actor").annotate(net=Sum("amount"))
    from apps.accounts.models import User

    for row in totals:
        if row["net"]:
            _apply(
                User.objects.get(pk=row["user"]),
                User.objects.filter(pk=row["actor"]).first() if row["actor"] else None,
                row["reason"], -row["net"], source, is_reversal=True,
            )

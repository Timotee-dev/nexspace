from django.db import IntegrityError, transaction

from .models import TopicFollow, UserFollow


def toggle_user_follow(follower, target) -> bool:
    """Returns True if now following."""
    if follower.pk == target.pk:
        raise ValueError("You can't follow yourself.")
    deleted, _ = UserFollow.objects.filter(follower=follower, following=target).delete()
    if deleted:
        return False
    try:
        with transaction.atomic():
            UserFollow.objects.create(follower=follower, following=target)
    except IntegrityError:
        return True
    from apps.notifications import services as notifications

    notifications.followed(follower, target)
    return True


def toggle_topic_follow(user, topic) -> bool:
    deleted, _ = TopicFollow.objects.filter(user=user, topic=topic).delete()
    if deleted:
        return False
    try:
        with transaction.atomic():
            TopicFollow.objects.create(user=user, topic=topic)
    except IntegrityError:
        pass
    return True


def followed_topic_ids(user) -> set[int]:
    """Topics a user follows explicitly or picked as interests during onboarding."""
    ids = set(TopicFollow.objects.filter(user=user).values_list("topic_id", flat=True))
    ids |= set(user.profile.interests.values_list("id", flat=True))
    return ids


def followed_user_ids(user) -> set[int]:
    return set(UserFollow.objects.filter(follower=user).values_list("following_id", flat=True))

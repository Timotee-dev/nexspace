"""Feed building (spec Section 53). No ML: a transparent weighted score.

For You   ranked: recency decay x engagement x relevance boosts, last 14 days
Following chronological: followed people (non-anonymous only), followed topics, joined Spaces
Department chronological: official department updates

Anonymous posts never match "followed people" and never get the followed-author
boost — otherwise the feed itself would reveal who wrote them.
"""
from datetime import timedelta

from django.db.models import Exists, OuterRef, Q
from django.utils import timezone

from apps.social.services import followed_topic_ids, followed_user_ids
from apps.spaces.services import joined_space_ids, muted_space_ids

from .models import Bookmark, PollVote, Post, PostVote

PAGE_SIZE = 15
FOR_YOU_WINDOW = timedelta(days=14)
FOR_YOU_CANDIDATES = 400

W_SCORE = 1.0
W_COMMENTS = 1.5
BOOST_FOLLOWED_AUTHOR = 1.8
BOOST_TOPIC = 1.4
BOOST_SAME_LEVEL = 1.2
BOOST_OFFICIAL = 1.5
BOOST_JOINED_SPACE = 1.6
DECAY = 1.3

TABS = ("for-you", "following", "department")


def base_queryset(user):
    """Visible posts minus anything from Spaces the user has muted."""
    return Post.objects.for_viewer(user).with_related().exclude(space_id__in=muted_space_ids(user))


def annotate_for_user(queryset, user):
    return queryset.annotate(
        my_vote_up=Exists(PostVote.objects.filter(post=OuterRef("pk"), user=user, value=1)),
        my_vote_down=Exists(PostVote.objects.filter(post=OuterRef("pk"), user=user, value=-1)),
        is_bookmarked=Exists(Bookmark.objects.filter(post=OuterRef("pk"), user=user)),
        has_voted_poll=Exists(PollVote.objects.filter(poll__post=OuterRef("pk"), user=user)),
    )


def expand_reposts(items, user):
    """Show plain reposts as the original post with a "<name> reposted" header (skipping originals the
    viewer can't see or that already appear), and mark which posts the viewer has reposted."""
    import copy

    from .services import reposted_ids

    plain = [p for p in items if p.kind == Post.Kind.REPOST]
    originals = {}
    if plain:
        ids = {p.repost_of_id for p in plain if p.repost_of_id}
        originals = {o.pk: o for o in annotate_for_user(
            Post.objects.for_viewer(user).with_related().filter(pk__in=ids), user)}
    out, seen = [], set()
    for p in items:
        if p.kind == Post.Kind.REPOST:
            original = originals.get(p.repost_of_id)
            if original is None or original.pk in seen:
                continue
            original = copy.copy(original)
            original.reposted_by, original.repost_row_id = p.author, p.pk
            out.append(original)
            seen.add(original.pk)
        elif p.pk not in seen:
            out.append(p)
            seen.add(p.pk)
    mine = reposted_ids(user, out) if out else set()
    for p in out:
        p.is_reposted = p.pk in mine
    return out


def rank(post, *, now, followed_users, followed_topics, level, joined_spaces=frozenset()):
    hours = max((now - post.created_at).total_seconds() / 3600, 0)
    engagement = max(1 + W_SCORE * post.score + W_COMMENTS * post.comment_count, 0.2)
    boost = 1.0
    if not post.is_anonymous and post.author_id in followed_users:
        boost *= BOOST_FOLLOWED_AUTHOR
    if followed_topics and any(t.id in followed_topics for t in post.topics.all()):
        boost *= BOOST_TOPIC
    if not post.is_anonymous and level and post.author.level == level:
        boost *= BOOST_SAME_LEVEL
    if post.is_official:
        boost *= BOOST_OFFICIAL
    if post.space_id and post.space_id in joined_spaces:
        boost *= BOOST_JOINED_SPACE
    return engagement * boost / (hours + 2) ** DECAY


def for_you(user, page: int = 1):
    now = timezone.now()
    candidates = list(
        annotate_for_user(base_queryset(user).exclude(kind=Post.Kind.REPOST), user)  # originals rank on their own
        .filter(created_at__gte=now - FOR_YOU_WINDOW)
        .order_by("-created_at")[:FOR_YOU_CANDIDATES]
    )
    context = {
        "now": now,
        "followed_users": followed_user_ids(user),
        "followed_topics": followed_topic_ids(user),
        "level": user.level,
        "joined_spaces": joined_space_ids(user, include_muted=False),
    }
    candidates.sort(key=lambda p: rank(p, **context), reverse=True)
    start = (page - 1) * PAGE_SIZE
    items = candidates[start:start + PAGE_SIZE]
    has_more = len(candidates) > start + PAGE_SIZE
    return items, ({"page": page + 1} if has_more else None)


def _chronological(queryset, before_id):
    if before_id:
        queryset = queryset.filter(id__lt=before_id)
    items = list(queryset.order_by("-id")[: PAGE_SIZE + 1])
    has_more = len(items) > PAGE_SIZE
    items = items[:PAGE_SIZE]
    return items, ({"before": items[-1].id} if has_more and items else None)


def _following_q(user):
    users, topics = followed_user_ids(user), followed_topic_ids(user)
    spaces = joined_space_ids(user, include_muted=False)
    matches = Q(pk__in=[])
    if spaces:
        matches |= Q(space_id__in=spaces)
    if users:
        matches |= Q(author_id__in=users, is_anonymous=False)
    if topics:
        matches |= Q(topics__in=topics)
    return matches


def new_count(user, tab, after_id):
    """How many posts appeared in this feed tab since `after_id` (for the "Show new posts" bar)."""
    qs = base_queryset(user).filter(id__gt=after_id).exclude(author=user)
    if tab == "following":
        qs = qs.filter(_following_q(user)).distinct()
    elif tab == "department":
        qs = qs.filter(is_official=True)
    else:
        qs = qs.exclude(kind=Post.Kind.REPOST)
    return qs.count()


def following(user, before_id=None):
    qs = annotate_for_user(base_queryset(user), user).filter(_following_q(user)).distinct()
    return _chronological(qs, before_id)


def department(user, before_id=None):
    qs = annotate_for_user(base_queryset(user).filter(is_official=True), user)
    return _chronological(qs, before_id)


def get_feed(user, tab, *, page=1, before=None):
    items, cursor = _get_feed(user, tab, page=page, before=before)
    return expand_reposts(items, user), cursor


def _get_feed(user, tab, *, page=1, before=None):
    if tab == "following":
        return following(user, before)
    if tab == "department":
        return department(user, before)
    return for_you(user, page)

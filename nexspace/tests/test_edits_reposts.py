import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from django.urls import reverse

from apps.accounts.models import RoleAssignment
from apps.accounts.services import assign_role
from apps.notifications.models import Notification
from apps.posts import feed
from apps.posts import services as posts
from apps.posts.models import Post, PostRevision
from apps.social.services import toggle_user_follow
from apps.spaces import services as spaces

pytestmark = pytest.mark.django_db


@pytest.fixture
def run_hooks(django_capture_on_commit_callbacks):
    def _run(fn, *args, **kwargs):
        with django_capture_on_commit_callbacks(execute=True):
            return fn(*args, **kwargs)
    return _run


@pytest.fixture
def alice(make_user):
    return make_user(email="alice@example.com", full_name="Alice Ade", username="alice")


@pytest.fixture
def bob(make_user):
    return make_user(email="bob@example.com", full_name="Bob Bello", username="bob")


# --- Editing -------------------------------------------------------------------------
def test_edit_post_keeps_history_and_marks_edited(alice, bob):
    post = posts.create_post(author=alice, kind="post", body="first version")
    posts.edit_post(user=alice, post=post, body="second version")
    post.refresh_from_db()
    assert post.body == "second version" and post.edited_at is not None
    assert list(PostRevision.objects.filter(post=post).values_list("body", flat=True)) == ["first version"]
    with pytest.raises(PermissionDenied):
        posts.edit_post(user=bob, post=post, body="hijack")
    with pytest.raises(ValidationError):
        posts.edit_post(user=alice, post=post, body="   ")


def test_edit_notifies_only_newly_mentioned(run_hooks, alice, bob, make_user):
    carol = make_user(email="carol@example.com", username="carol")
    post = run_hooks(posts.create_post, author=alice, kind="post", body="hi @bob")
    run_hooks(posts.edit_post, user=alice, post=post, body="hi @bob and @carol")
    assert Notification.objects.filter(recipient=bob, kind="mention").count() == 1
    assert Notification.objects.filter(recipient=carol, kind="mention").count() == 1


def test_poll_question_locks_after_votes(alice, bob):
    poll = posts.create_post(author=alice, kind="poll", body="Best language?", poll_options=["Python", "Java"])
    posts.edit_post(user=alice, post=poll, body="Best programming language?")  # no votes yet: fine
    posts.vote_poll(user=bob, post=poll, option_ids=[poll.poll.options.first().pk])
    with pytest.raises(ValidationError):
        posts.edit_post(user=alice, post=poll, body="Something else?")


def test_removed_posts_cannot_be_edited(alice):
    post = posts.create_post(author=alice, kind="post", body="x")
    Post.objects.filter(pk=post.pk).update(removed_by_moderator=True)
    post.refresh_from_db()
    with pytest.raises(PermissionDenied):
        posts.edit_post(user=alice, post=post, body="y")


def test_edit_comment(alice, bob):
    post = posts.create_post(author=alice, kind="post", body="x")
    comment = posts.add_comment(user=bob, post=post, body="nice")
    posts.edit_comment(user=bob, comment=comment, body="very nice")
    comment.refresh_from_db()
    assert comment.body == "very nice" and comment.edited_at and comment.revisions.first().body == "nice"
    with pytest.raises(PermissionDenied):
        posts.edit_comment(user=alice, comment=comment, body="no")


def test_edit_pages_and_history_access(client, alice, bob, make_user, department):
    post = posts.create_post(author=alice, kind="post", body="before")
    client.force_login(alice)
    r = client.post(reverse("posts:edit", args=[post.pk]), {"body": "after"})
    assert r.status_code == 302
    page = client.get(post.get_absolute_url()).content.decode()
    assert "after" in page and "· Edited" in page and "Edit history" in page
    assert "before" in client.get(reverse("posts:history", args=[post.pk])).content.decode()
    client.force_login(bob)
    assert client.get(reverse("posts:edit", args=[post.pk])).status_code == 403
    assert client.get(reverse("posts:history", args=[post.pk])).status_code == 403
    mod = make_user(email="mod@example.com")
    assign_role(user=mod, role=RoleAssignment.Role.MODERATOR, department=department)
    client.force_login(mod)
    assert client.get(reverse("posts:history", args=[post.pk])).status_code == 200


# --- Reposts -------------------------------------------------------------------------
def test_repost_and_undo(run_hooks, alice, bob):
    post = posts.create_post(author=alice, kind="post", body="worth sharing")
    run_hooks(posts.repost, user=bob, post=post)
    post.refresh_from_db()
    assert post.repost_count == 1
    assert Notification.objects.filter(recipient=alice, kind="repost").exists()
    with pytest.raises(ValidationError):
        posts.repost(user=bob, post=post)
    assert posts.undo_repost(user=bob, post=post)
    post.refresh_from_db()
    assert post.repost_count == 0


def test_reposting_a_repost_reposts_the_original(alice, bob, make_user):
    carol = make_user(email="carol@example.com")
    post = posts.create_post(author=alice, kind="post", body="original")
    bobs = posts.repost(user=bob, post=post)
    carols = posts.repost(user=carol, post=bobs)
    assert carols.repost_of_id == post.pk
    post.refresh_from_db()
    assert post.repost_count == 2


def test_private_space_posts_cannot_be_reposted(alice, bob, make_user):
    from apps.academics.models import Course
    from apps.spaces.models import SpaceJoinRequest

    course = Course.objects.create(department=alice.department, code="CSC 9", title="T", level=300, semester=1)
    assign_role(user=alice, role=RoleAssignment.Role.COURSE_REP, course=course)
    club = spaces.create_space(user=alice, name="Inner Circle")
    spaces.request_to_join(bob, club)
    spaces.decide_request(manager=alice, join_request=SpaceJoinRequest.objects.get(user=bob), approve=True)
    secret = posts.create_post(author=alice, kind="post", body="members only", space=club)
    assert not posts.can_repost(bob, secret)
    with pytest.raises(PermissionDenied):
        posts.repost(user=bob, post=secret, quote="look at this")


def test_following_feed_shows_reposts_with_header(client, alice, bob, make_user):
    carol = make_user(email="carol@example.com", full_name="Carol Chi")
    toggle_user_follow(carol, bob)
    original = posts.create_post(author=alice, kind="post", body="from alice")
    posts.repost(user=bob, post=original)
    items, _ = feed.get_feed(carol, "following")
    assert [p.pk for p in items] == [original.pk] and items[0].reposted_by == bob
    for_you, _ = feed.get_feed(carol, "for-you")
    assert [p.pk for p in for_you].count(original.pk) == 1  # no duplicate
    client.force_login(carol)
    html = client.get(reverse("core:home") + "?tab=following").content.decode()
    assert "Bob Bello</a> reposted" in html and "from alice" in html


def test_anonymous_original_stays_anonymous_when_reposted(client, alice, bob):
    secret = posts.create_post(author=alice, kind="question", body="anon worry", is_anonymous=True)
    quote = posts.repost(user=bob, post=secret, quote="I wonder this too")
    client.force_login(bob)
    html = client.get(quote.get_absolute_url()).content.decode()
    assert "I wonder this too" in html and "anon worry" in html and "Anonymous Student" in html
    assert "Alice Ade" not in html


def test_deleted_original(client, alice, bob, make_user):
    original = posts.create_post(author=alice, kind="post", body="soon gone")
    quote = posts.repost(user=bob, post=original, quote="my take")
    posts.repost(user=bob, post=original)
    posts.delete_post(user=alice, post=original)
    viewer = make_user(email="v@example.com")
    toggle_user_follow(viewer, bob)
    items, _ = feed.get_feed(viewer, "following")
    assert original.pk not in [p.pk for p in items]  # plain repost disappears with the original
    client.force_login(viewer)
    assert "This post isn't available" in client.get(quote.get_absolute_url()).content.decode()


def test_repost_endpoints(client, alice, bob):
    post = posts.create_post(author=alice, kind="post", body="share me")
    client.force_login(bob)
    r = client.post(reverse("posts:repost", args=[post.pk]), {"action": "repost"}, HTTP_ACCEPT="application/json")
    assert r.json() == {"reposted": True, "count": 1}
    r = client.post(reverse("posts:repost", args=[post.pk]), {"action": "undo"}, HTTP_ACCEPT="application/json")
    assert r.json() == {"reposted": False, "count": 0}
    r = client.post(reverse("posts:quote", args=[post.pk]), {"body": "my thoughts"})
    quote = Post.objects.get(author=bob, body="my thoughts")
    assert r.url == quote.get_absolute_url() and quote.repost_of_id == post.pk
    plain = posts.repost(user=bob, post=post)
    assert client.get(plain.get_absolute_url()).url == post.get_absolute_url()  # plain repost opens the original


# --- Live feed / comments ---------------------------------------------------------------
def test_live_counts(client, alice, bob):
    client.force_login(bob)
    first = posts.create_post(author=bob, kind="post", body="mine")
    r = client.get(reverse("posts:live-feed") + f"?tab=for-you&after={first.pk}")
    assert r.json()["new"] == 0
    newer = posts.create_post(author=alice, kind="post", body="new from alice")
    assert client.get(reverse("posts:live-feed") + f"?tab=for-you&after={first.pk}").json()["new"] == 1
    posts.add_comment(user=alice, post=first, body="hello")
    assert client.get(reverse("posts:live-comments", args=[first.pk]) + "?after=0").json()["new"] == 1
    assert newer

import io
from datetime import timedelta

import pytest
from django.core.cache import cache
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from django.utils import timezone
from PIL import Image
from rest_framework.test import APIClient

from apps.accounts.models import RoleAssignment
from apps.accounts.services import assign_role
from apps.posts import services
from apps.posts.models import Comment, Post
from apps.reputation.models import NexScoreEvent
from apps.social.services import toggle_topic_follow, toggle_user_follow
from apps.topics.models import Topic

pytestmark = pytest.mark.django_db


def png_file(name="pic.png"):
    buf = io.BytesIO()
    Image.new("RGB", (8, 8), "orange").save(buf, "PNG")
    return SimpleUploadedFile(name, buf.getvalue(), content_type="image/png")


@pytest.fixture
def alice(make_user):
    return make_user(email="alice@example.com", full_name="Alice Author")


@pytest.fixture
def bob(make_user):
    return make_user(email="bob@example.com", full_name="Bob Reader")


@pytest.fixture
def outsider(make_user, other_department):
    return make_user(email="out@example.com", full_name="Out Sider", department=other_department)


def score(user):
    user.profile.refresh_from_db()
    return user.profile.nexscore


# --- Creating posts ------------------------------------------------------------
def test_compose_each_kind(client, alice):
    client.force_login(alice)
    url = reverse("posts:compose")
    future = (timezone.localtime() + timedelta(days=2)).strftime("%Y-%m-%dT%H:%M")
    cases = [
        {"kind": "post", "body": "Hello department"},
        {"kind": "question", "body": "How do I start?"},
        {"kind": "poll", "body": "Which day?", "poll_option": ["Mon", "Tue", "Wed"]},
        {"kind": "event", "title": "Workshop", "event_starts_at": future},
        {"kind": "opportunity", "title": "Internship", "opp_organization": "Acme", "opp_category": "internship"},
    ]
    for data in cases:
        cache.clear()
        r = client.post(url, data)
        assert r.status_code == 302, (data, r.content[:500])
    assert Post.objects.count() == 5
    poll = Post.objects.get(kind="poll").poll
    assert [o.text for o in poll.options.all()] == ["Mon", "Tue", "Wed"]


@pytest.mark.parametrize("data,message", [
    ({"kind": "post", "body": ""}, "Write something"),
    ({"kind": "poll", "body": "Q?", "poll_option": ["Only one"]}, "between 2 and 6"),
    ({"kind": "poll", "body": "Q?", "poll_option": ["Same", "same"]}, "different"),
    ({"kind": "event", "title": "No date"}, "starts"),
    ({"kind": "opportunity", "title": "X"}, "organization"),
])
def test_compose_validation(client, alice, data, message):
    client.force_login(alice)
    r = client.post(reverse("posts:compose"), data)
    assert r.status_code == 200 and message in r.content.decode()
    assert not Post.objects.exists()


def test_anonymous_only_for_posts_and_questions(alice):
    with pytest.raises(ValidationError):
        services.create_post(author=alice, kind="poll", body="Q", poll_options=["a", "b"], is_anonymous=True)


def test_only_department_admins_post_official(alice, department):
    with pytest.raises(PermissionDenied):
        services.create_post(author=alice, kind="post", body="x", is_official=True)
    assign_role(user=alice, role=RoleAssignment.Role.DEPARTMENT_ADMIN, department=department)
    assert services.create_post(author=alice, kind="post", body="x", is_official=True).is_official


def test_topics_limited_to_three(alice):
    with pytest.raises(ValidationError):
        services.create_post(author=alice, kind="post", body="x", topics=list(Topic.objects.all()[:4]))


def test_unverified_users_cannot_write(client, make_user, alice):
    newbie = make_user(email="new@example.com", verified=False)
    post = services.create_post(author=alice, kind="post", body="hi")
    client.force_login(newbie)
    client.post(reverse("posts:compose"), {"kind": "post", "body": "sneaky"})
    assert Post.objects.count() == 1
    client.post(reverse("posts:comment", args=[post.pk]), {"body": "sneaky"})
    assert not Comment.objects.exists()
    api = APIClient()
    api.force_authenticate(newbie)
    assert api.post(reverse("api-post-vote", args=[post.pk]), {"value": 1}, format="json").status_code == 403
    assert api.post(reverse("api-posts"), {"kind": "post", "body": "x"}, format="json").status_code == 403
    # Bookmarking is personal, so it's allowed before verification
    assert api.post(reverse("api-post-bookmark", args=[post.pk])).data["bookmarked"] is True


def test_post_rate_limit(alice):
    for i in range(10):
        services.create_post(author=alice, kind="post", body=f"post {i}")
    with pytest.raises(services.RateLimited):
        services.create_post(author=alice, kind="post", body="one too many")


def test_body_is_escaped_and_mentions_linked(client, alice, bob):
    post = services.create_post(author=alice, kind="post", body=f"<script>alert(1)</script> hi @{bob.username}")
    assert list(post.mentions.all()) == [bob]
    client.force_login(bob)
    html = client.get(post.get_absolute_url()).content.decode()
    assert "<script>alert(1)" not in html and "&lt;script&gt;" in html
    assert f'href="/u/{bob.username}/"' in html


# --- Anonymous posts never leak their author -----------------------------------
def test_anonymous_author_hidden_everywhere(client, alice, bob):
    post = services.create_post(author=alice, kind="question", body="Secret question", is_anonymous=True)
    toggle_user_follow(bob, alice)
    client.force_login(bob)
    for url in [reverse("core:home"), post.get_absolute_url(), reverse("core:home") + "?tab=following"]:
        html = client.get(url).content.decode()
        assert "Alice Author" not in html and alice.username not in html, url
    profile_html = client.get(reverse("accounts:profile", args=[alice.username])).content.decode()
    assert "Secret question" not in profile_html

    api = APIClient()
    api.force_authenticate(bob)
    data = api.get(reverse("api-post", args=[post.pk])).data
    assert data["author"] == {"display_name": "Anonymous Student", "username": None, "avatar_url": None, "level": None}
    assert data["is_mine"] is False
    assert "Alice Author" not in str(data) and alice.username not in str(data)
    feed = api.get(reverse("api-posts") + "?tab=following").data["results"]
    assert all(p["id"] != post.pk for p in feed), "following an author must not surface their anonymous posts"


def test_anonymous_post_visible_through_topic_follow(alice, bob):
    topic = Topic.objects.first()
    post = services.create_post(author=alice, kind="post", body="anon", is_anonymous=True, topics=[topic])
    toggle_topic_follow(bob, topic)
    from apps.posts.feed import following

    items, _ = following(bob)
    assert post in items


def test_owner_sees_own_anonymous_post_on_profile(client, alice):
    services.create_post(author=alice, kind="post", body="my anon", is_anonymous=True)
    client.force_login(alice)
    assert "my anon" in client.get(reverse("accounts:profile", args=[alice.username])).content.decode()


# --- Voting & NexScore -----------------------------------------------------------
def test_vote_toggle_switch_and_score(alice, bob):
    post = services.create_post(author=alice, kind="post", body="vote me")
    assert services.vote_post(user=bob, post=post, value=1) == (1, 1)
    assert score(alice) == 2
    assert services.vote_post(user=bob, post=post, value=-1) == (-1, -1)
    assert score(alice) == -1
    assert services.vote_post(user=bob, post=post, value=0) == (0, 0)
    assert score(alice) == 0
    post.refresh_from_db()
    assert post.score == 0 and not post.votes.exists()


def test_no_self_voting(alice):
    post = services.create_post(author=alice, kind="post", body="mine")
    with pytest.raises(PermissionDenied):
        services.vote_post(user=alice, post=post, value=1)


def test_per_voter_daily_cap(alice, bob):
    for i in range(8):
        cache.clear()
        post = services.create_post(author=alice, kind="post", body=f"p{i}")
        services.vote_post(user=bob, post=post, value=1)
    assert score(alice) == 10  # capped at +10 per voter per day


def test_daily_cap(make_user, alice):
    voters = [make_user(email=f"v{i}@example.com") for i in range(12)]
    posts = [services.create_post(author=alice, kind="post", body=f"p{i}") for i in range(5)]
    for voter in voters:
        for post in posts:
            services.vote_post(user=voter, post=post, value=1)
    assert score(alice) == 100


def test_anonymous_posts_earn_nothing(alice, bob):
    post = services.create_post(author=alice, kind="post", body="anon", is_anonymous=True)
    services.vote_post(user=bob, post=post, value=1)
    assert score(alice) == 0 and not NexScoreEvent.objects.exists()


def test_revoting_cannot_farm_points(alice, bob):
    post = services.create_post(author=alice, kind="post", body="p")
    for _ in range(10):
        services.vote_post(user=bob, post=post, value=1)
        services.vote_post(user=bob, post=post, value=0)
    services.vote_post(user=bob, post=post, value=1)
    assert score(alice) <= 2


def test_deleting_post_reverses_points(alice, bob):
    post = services.create_post(author=alice, kind="post", body="p")
    services.vote_post(user=bob, post=post, value=1)
    services.delete_post(user=alice, post=post)
    assert score(alice) == 0
    with pytest.raises(PermissionDenied):
        services.delete_post(user=bob, post=post)


def test_accept_answer(alice, bob, make_user):
    question = services.create_post(author=alice, kind="question", body="Q?")
    answer = services.add_comment(user=bob, post=question, body="A!")
    services.accept_answer(user=alice, comment=answer)
    question.refresh_from_db()
    assert question.accepted_comment == answer and score(bob) == 15
    services.accept_answer(user=alice, comment=answer)  # toggle off
    question.refresh_from_db()
    assert question.accepted_comment is None and score(bob) == 0
    with pytest.raises(PermissionDenied):
        services.accept_answer(user=bob, comment=answer)


# --- Comments --------------------------------------------------------------------
def test_comment_depth_is_capped_at_three(alice, bob):
    post = services.create_post(author=alice, kind="post", body="p")
    c1 = services.add_comment(user=bob, post=post, body="1")
    c2 = services.add_comment(user=alice, post=post, body="2", parent=c1)
    c3 = services.add_comment(user=bob, post=post, body="3", parent=c2)
    c4 = services.add_comment(user=alice, post=post, body="4", parent=c3)
    assert (c1.depth, c2.depth, c3.depth, c4.depth) == (1, 2, 3, 3)
    assert c4.parent == c2 and c4.reply_to == bob
    post.refresh_from_db()
    assert post.comment_count == 4


def test_delete_comment(client, alice, bob):
    post = services.create_post(author=alice, kind="post", body="p")
    parent = services.add_comment(user=bob, post=post, body="parent text")
    services.add_comment(user=alice, post=post, body="child", parent=parent)
    services.delete_comment(user=bob, comment=parent)
    client.force_login(alice)
    html = client.get(post.get_absolute_url()).content.decode()
    assert "[deleted]" in html and "parent text" not in html and "child" in html


def test_comment_via_view_and_api(client, alice, bob):
    post = services.create_post(author=alice, kind="post", body="p")
    client.force_login(bob)
    r = client.post(reverse("posts:comment", args=[post.pk]), {"body": "nice one"})
    assert r.status_code == 302 and "#c-" in r.url
    api = APIClient()
    api.force_authenticate(alice)
    r = api.post(reverse("api-comments", args=[post.pk]), {"body": "thanks"}, format="json")
    assert r.status_code == 201
    assert len(api.get(reverse("api-comments", args=[post.pk])).data) == 2


# --- Polls -------------------------------------------------------------------------
def test_single_choice_poll(alice, bob):
    post = services.create_post(author=alice, kind="poll", body="Q", poll_options=["a", "b"])
    a, b = post.poll.options.all()
    with pytest.raises(ValidationError):
        services.vote_poll(user=bob, post=post, option_ids=[a.pk, b.pk])
    services.vote_poll(user=bob, post=post, option_ids=[a.pk])
    with pytest.raises(ValidationError):
        services.vote_poll(user=bob, post=post, option_ids=[b.pk])
    a.refresh_from_db()
    assert a.vote_count == 1


def test_multiple_choice_and_closed_poll(alice, bob, make_user):
    post = services.create_post(author=alice, kind="poll", body="Q", poll_options=["a", "b", "c"], poll_multiple=True)
    ids = [o.pk for o in post.poll.options.all()]
    services.vote_poll(user=bob, post=post, option_ids=ids[:2])
    assert sum(o.vote_count for o in post.poll.options.all()) == 2
    post.poll.closes_at = timezone.now() - timedelta(minutes=1)
    post.poll.save()
    with pytest.raises(ValidationError):
        services.vote_poll(user=make_user(email="late@example.com"), post=post, option_ids=[ids[0]])


def test_poll_results_hidden_until_voted(alice, bob):
    post = services.create_post(author=alice, kind="poll", body="Q", poll_options=["a", "b"])
    api = APIClient()
    api.force_authenticate(bob)
    poll = api.get(reverse("api-post", args=[post.pk])).data["poll"]
    assert poll["results_visible"] is False and poll["options"][0]["vote_count"] is None
    option = poll["options"][0]["id"]
    poll = api.post(reverse("api-poll-vote", args=[post.pk]), {"options": [option]}, format="json").data["poll"]
    assert poll["results_visible"] is True and poll["total_votes"] == 1


# --- Visibility, bookmarks, follows, feeds -----------------------------------------
def test_other_departments_cannot_see_posts(client, alice, outsider):
    post = services.create_post(author=alice, kind="post", body="dept only")
    client.force_login(outsider)
    assert client.get(post.get_absolute_url()).status_code == 404
    assert "dept only" not in client.get(reverse("core:home")).content.decode()
    api = APIClient()
    api.force_authenticate(outsider)
    assert api.post(reverse("api-post-vote", args=[post.pk]), {"value": 1}, format="json").status_code == 404


def test_bookmarks_and_saved_page(client, alice, bob):
    post = services.create_post(author=alice, kind="post", body="save me")
    assert services.toggle_bookmark(user=bob, post=post) is True
    client.force_login(bob)
    assert "save me" in client.get(reverse("posts:saved")).content.decode()
    assert services.toggle_bookmark(user=bob, post=post) is False


def test_follow_rules(client, alice, bob):
    with pytest.raises(ValueError):
        toggle_user_follow(alice, alice)
    client.force_login(bob)
    r = client.post(reverse("social:follow-user", args=[alice.username]))
    assert r.status_code == 302 and alice.follower_set.count() == 1
    api = APIClient()
    api.force_authenticate(bob)
    assert api.post(reverse("api-user-follow", args=[alice.username])).data["following"] is False


def test_following_and_department_tabs(make_user, alice, bob, department):
    admin = make_user(email="admin2@example.com")
    assign_role(user=admin, role=RoleAssignment.Role.DEPARTMENT_ADMIN, department=department)
    normal = services.create_post(author=alice, kind="post", body="normal")
    official = services.create_post(author=admin, kind="post", body="official", is_official=True)
    from apps.posts import feed

    assert feed.following(bob)[0] == []
    toggle_user_follow(bob, alice)
    assert feed.following(bob)[0] == [normal]
    assert feed.department(bob)[0] == [official]
    for_you, _ = feed.for_you(bob)
    assert set(for_you) == {normal, official}


def test_feed_pagination_and_partial(client, make_user, alice, bob):
    authors = [make_user(email=f"a{i}@example.com") for i in range(2)]
    for i in range(20):
        services.create_post(author=authors[i % 2], kind="post", body=f"post number {i}")
    client.force_login(bob)
    html = client.get(reverse("core:home")).content.decode()
    assert html.count('class="post"') == 15 and "data-next=" in html
    partial = client.get(reverse("core:home") + "?tab=for-you&page=2&partial=1").content.decode()
    assert partial.count('class="post"') == 5 and "<html" not in partial
    toggle_user_follow(bob, authors[0])
    first = client.get(reverse("core:home") + "?tab=following").content.decode()
    assert first.count('class="post"') == 10


# --- Attachments -------------------------------------------------------------------
def test_image_and_file_uploads(client, alice, bob, outsider):
    client.force_login(alice)
    pdf = SimpleUploadedFile("notes.pdf", b"%PDF-1.4 fake but has magic", content_type="application/pdf")
    r = client.post(reverse("posts:compose"), {"kind": "post", "body": "files", "images": [png_file()], "files": [pdf]})
    assert r.status_code == 302
    post = Post.objects.get()
    image, doc = post.images[0], post.files[0]
    assert image.content_type == "image/png" and doc.content_type == "application/pdf"
    client.force_login(bob)
    assert client.get(reverse("posts:attachment", args=[post.pk, doc.pk])).status_code == 200
    client.force_login(outsider)
    assert client.get(reverse("posts:attachment", args=[post.pk, doc.pk])).status_code == 404


def test_disguised_file_rejected(client, alice):
    client.force_login(alice)
    fake = SimpleUploadedFile("notes.pdf", b"MZ\x90\x00 this is an exe", content_type="application/pdf")
    r = client.post(reverse("posts:compose"), {"kind": "post", "body": "x", "files": [fake]})
    assert r.status_code == 200 and not Post.objects.exists()


# --- API ---------------------------------------------------------------------------
def test_api_create_and_list(alice, bob):
    api = APIClient()
    api.force_authenticate(alice)
    r = api.post(reverse("api-posts"), {"kind": "question", "body": "API question?", "topics": ["programming"]}, format="json")
    assert r.status_code == 201 and r.data["topics"] == ["programming"] and r.data["is_mine"]
    api.force_authenticate(bob)
    results = api.get(reverse("api-posts")).data["results"]
    assert results[0]["body"] == "API question?" and results[0]["author"]["username"] == alice.username
    r = api.get(reverse("api-posts") + "?tab=nope")
    assert r.status_code == 400

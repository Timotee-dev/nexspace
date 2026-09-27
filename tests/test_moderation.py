import pytest
from django.core.cache import cache
from django.core.exceptions import PermissionDenied, ValidationError
from django.urls import reverse

from apps.accounts.models import RoleAssignment
from apps.accounts.services import assign_role
from apps.core.models import AuditLog
from apps.moderation import services as moderation
from apps.moderation.models import ModerationAction, Report
from apps.posts import feed
from apps.posts import services as posts
from apps.posts.models import Post

from .conftest import PASSWORD

pytestmark = pytest.mark.django_db


@pytest.fixture
def author(make_user):
    return make_user(email="author@example.com", full_name="Post Author")


@pytest.fixture
def moderator(make_user, department):
    user = make_user(email="mod@example.com", full_name="Mo Derator")
    assign_role(user=user, role=RoleAssignment.Role.MODERATOR, department=department)
    return user


@pytest.fixture
def admin(make_user, department):
    user = make_user(email="adm@example.com", full_name="Dee Admin")
    assign_role(user=user, role=RoleAssignment.Role.DEPARTMENT_ADMIN, department=department)
    return user


def reporters(make_user, n):
    return [make_user(email=f"r{i}@example.com") for i in range(n)]


def score(user):
    user.profile.refresh_from_db()
    return user.profile.nexscore


def test_report_rules(author, make_user):
    post = posts.create_post(author=author, kind="post", body="p")
    reporter = make_user(email="r@example.com")
    with pytest.raises(ValidationError):
        moderation.submit_report(reporter=author, target_type="post", target_id=post.pk, reason="spam")
    moderation.submit_report(reporter=reporter, target_type="post", target_id=post.pk, reason="spam")
    with pytest.raises(ValidationError):
        moderation.submit_report(reporter=reporter, target_type="post", target_id=post.pk, reason="spam")
    with pytest.raises(ValidationError):
        moderation.submit_report(reporter=make_user(email="r2@example.com"), target_type="post", target_id=post.pk,
                                 reason="other", details="")
    with pytest.raises(PermissionDenied):
        moderation.submit_report(reporter=make_user(email="nv@example.com", verified=False), target_type="post",
                                 target_id=post.pk, reason="spam")


def test_five_reports_auto_hide(author, make_user, client):
    post = posts.create_post(author=author, kind="post", body="bad post")
    for r in reporters(make_user, 4):
        moderation.submit_report(reporter=r, target_type="post", target_id=post.pk, reason="spam")
    post.refresh_from_db()
    assert not post.is_hidden
    moderation.submit_report(reporter=make_user(email="fifth@example.com"), target_type="post", target_id=post.pk,
                             reason="abuse")
    post.refresh_from_db()
    assert post.is_hidden
    viewer = make_user(email="viewer@example.com")
    assert post not in feed.for_you(viewer)[0]
    client.force_login(viewer)
    assert client.get(post.get_absolute_url()).status_code == 404
    client.force_login(author)
    assert client.get(post.get_absolute_url()).status_code == 200
    assert ModerationAction.objects.filter(action="auto_hide").exists()


def test_dismiss_restores(author, make_user, moderator):
    post = posts.create_post(author=author, kind="post", body="fine post")
    for r in reporters(make_user, 5):
        moderation.submit_report(reporter=r, target_type="post", target_id=post.pk, reason="spam")
    moderation.dismiss(moderator=moderator, target_type="post", target_id=post.pk)
    post.refresh_from_db()
    assert not post.is_hidden
    assert not Report.objects.filter(status="open").exists()


def test_remove_post_penalizes_and_reverses(author, make_user, moderator):
    post = posts.create_post(author=author, kind="post", body="p")
    posts.vote_post(user=make_user(email="v@example.com"), post=post, value=1)
    assert score(author) == 2
    moderation.remove_content(moderator=moderator, target_type="post", target_id=post.pk, note="spam")
    post.refresh_from_db()
    assert post.is_deleted and post.removed_by_moderator
    assert score(author) == -20
    assert ModerationAction.objects.filter(action="remove", note="spam").exists()


def test_remove_anonymous_post_has_no_penalty(author, moderator):
    post = posts.create_post(author=author, kind="post", body="anon", is_anonymous=True)
    moderation.remove_content(moderator=moderator, target_type="post", target_id=post.pk)
    assert score(author) == 0


def test_remove_comment(author, make_user, moderator, client):
    post = posts.create_post(author=author, kind="post", body="p")
    commenter = make_user(email="c@example.com")
    comment = posts.add_comment(user=commenter, post=post, body="rude comment")
    moderation.remove_content(moderator=moderator, target_type="comment", target_id=comment.pk)
    client.force_login(author)
    html = client.get(post.get_absolute_url()).content.decode()
    assert "rude comment" not in html  # removed comments without replies disappear entirely


def test_non_moderators_blocked(client, author, make_user):
    post = posts.create_post(author=author, kind="post", body="p")
    student = make_user(email="s@example.com")
    with pytest.raises(PermissionDenied):
        moderation.remove_content(moderator=student, target_type="post", target_id=post.pk)
    client.force_login(student)
    assert client.get(reverse("moderation:queue")).status_code == 403
    assert client.post(reverse("moderation:action"), {"action": "remove", "target_type": "post",
                                                      "target_id": post.pk}).status_code == 403


def test_moderators_scoped_to_department(make_user, other_department, moderator):
    outsider = make_user(email="x@example.com", department=other_department)
    post = posts.create_post(author=outsider, kind="post", body="elsewhere")
    with pytest.raises(PermissionDenied):
        moderation.remove_content(moderator=moderator, target_type="post", target_id=post.pk)


def test_queue_and_actions_via_views(client, author, make_user, moderator):
    post = posts.create_post(author=author, kind="post", body="reported thing")
    reporter = make_user(email="rr@example.com")
    client.force_login(reporter)
    r = client.post(reverse("moderation:report", args=["post", post.pk]), {"reason": "spam", "next": "/"})
    assert r.status_code == 302
    client.force_login(moderator)
    assert "reported thing" in client.get(reverse("moderation:queue")).content.decode()
    client.post(reverse("moderation:action"), {"action": "remove", "target_type": "post", "target_id": post.pk})
    assert Post.objects.get(pk=post.pk).is_deleted
    assert client.get(reverse("moderation:log")).status_code == 200


def test_suspension_blocks_writes_not_reads(client, author, moderator):
    moderation.suspend_user(moderator=moderator, user=author, days=3)
    author.refresh_from_db()
    assert author.is_suspended and not author.can_write
    with pytest.raises(PermissionDenied):
        posts.create_post(author=author, kind="post", body="x")
    client.force_login(author)
    assert client.get(reverse("core:home")).status_code == 200
    moderation.unsuspend_user(moderator=moderator, user=author)
    author.refresh_from_db()
    cache.clear()
    assert posts.create_post(author=author, kind="post", body="back")


def test_ban_requires_admin_and_blocks_login(client, author, moderator, admin):
    with pytest.raises(PermissionDenied):
        moderation.ban_user(moderator=moderator, user=author)
    client.force_login(author)
    moderation.ban_user(moderator=admin, user=author)
    author.refresh_from_db()
    assert not author.is_active
    assert client.get(reverse("accounts:settings-profile")).status_code == 302  # session killed
    r = client.post(reverse("accounts:login"), {"email": author.email, "password": PASSWORD})
    assert "_auth_user_id" not in client.session


def test_reveal_anonymous_author_is_logged(client, author, moderator, make_user):
    post = posts.create_post(author=author, kind="question", body="anon q", is_anonymous=True)
    student = make_user(email="nosy@example.com")
    client.force_login(student)
    assert client.post(reverse("moderation:reveal", args=[post.pk])).status_code == 403
    client.force_login(moderator)
    r = client.post(reverse("moderation:reveal", args=[post.pk]), follow=True)
    assert "Post Author" in r.content.decode()
    assert ModerationAction.objects.filter(action="reveal", target_id=post.pk).exists()
    assert AuditLog.objects.filter(action="anonymous.revealed").exists()


def test_report_resource_user_and_space(make_user, author, department):
    from django.core.files.uploadedfile import SimpleUploadedFile

    from apps.academics.models import Course
    from apps.resources import services as resources
    from apps.spaces import services as spaces

    course = Course.objects.create(department=department, code="CSC 999", title="T", level=300, semester=1)
    res = resources.upload(user=author, course=course, title="R", resource_type="other",
                           file=SimpleUploadedFile("r.pdf", b"%PDF-1.4"))
    space = spaces.create_space(user=author, name="Some Space")
    reporter = make_user(email="rep0@example.com")
    for kind, pk in (("resource", res.pk), ("user", author.pk), ("space", space.pk)):
        assert moderation.submit_report(reporter=reporter, target_type=kind, target_id=pk, reason="spam")

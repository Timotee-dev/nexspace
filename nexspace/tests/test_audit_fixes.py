"""Regression tests for issues found in the full audit."""
import pytest
from django.urls import reverse

from apps.academics.models import Course
from apps.accounts.models import RoleAssignment, User
from apps.accounts.services import assign_role, delete_account
from apps.notices import services as notices
from apps.notices.models import Audience
from apps.posts import services as posts
from apps.posts.models import Post
from apps.social.services import toggle_user_follow
from apps.spaces import services as spaces
from apps.spaces.models import SpaceJoinRequest

from .conftest import PASSWORD

pytestmark = pytest.mark.django_db


@pytest.fixture
def course(department):
    return Course.objects.create(department=department, code="CSC 301", title="Data Structures", level=300, semester=1)


@pytest.fixture
def rep(make_user, course):
    u = make_user(email="rep@example.com", full_name="Rep")
    assign_role(user=u, role=RoleAssignment.Role.COURSE_REP, course=course)
    return u


def test_profile_replies_hide_private_space_comments(client, rep, make_user):
    club = spaces.create_space(user=rep, name="Secret Club")
    member = make_user(email="m@example.com", full_name="Member")
    spaces.request_to_join(member, club)
    spaces.decide_request(manager=rep, join_request=SpaceJoinRequest.objects.get(user=member), approve=True)
    post = posts.create_post(author=rep, kind="post", body="inside", space=club)
    posts.add_comment(user=member, post=post, body="private reply text")
    outsider = make_user(email="o@example.com")
    client.force_login(outsider)
    page = client.get(reverse("accounts:profile", args=[member.username]) + "?tab=replies").content.decode()
    assert "private reply text" not in page
    client.force_login(rep)
    page = client.get(reverse("accounts:profile", args=[member.username]) + "?tab=replies").content.decode()
    assert "private reply text" in page


def test_department_admin_can_open_any_announcement(client, rep, course, make_user, department):
    item = notices.publish_announcement(user=rep, title="Course only", body="x", audience=Audience.COURSE,
                                        target_course=course)
    admin = make_user(email="adm@example.com")
    assign_role(user=admin, role=RoleAssignment.Role.DEPARTMENT_ADMIN, department=department)
    client.force_login(admin)
    assert client.get(reverse("notices:announcement", args=[item.pk])).status_code == 200
    client.force_login(make_user(email="stu@example.com"))  # not in the course
    assert client.get(reverse("notices:announcement", args=[item.pk])).status_code == 404


def test_account_without_department_is_asked_for_one(client, department):
    boss = User.objects.create_superuser(email="arifalotimothy@gmail.com", password=PASSWORD, full_name="Boss")
    client.force_login(boss)
    r = client.get(reverse("manage:overview"))
    assert r.status_code == 302 and "step=department" in r.url
    r = client.post(reverse("accounts:onboarding") + "?step=department", {"department": department.pk, "level": 400})
    boss.refresh_from_db()
    assert boss.department == department and boss.level == 400
    assert client.get(reverse("manage:overview")).status_code == 200


def test_forbidden_page_is_styled(client, make_user):
    client.force_login(make_user(email="s@example.com"))
    r = client.get(reverse("moderation:queue"))  # admin pages now redirect to the admin sign-in instead
    assert r.status_code == 403 and "You don't have access to this page" in r.content.decode()


def test_admin_shortcut_redirects(client):
    assert client.get("/admin/").url == "/manage/"


def test_cron_endpoint(client, settings):
    settings.CRON_SECRET = ""
    assert client.get("/internal/run-scheduled/").status_code == 404  # off unless configured
    settings.CRON_SECRET = "s3cret"
    assert client.get("/internal/run-scheduled/?token=wrong").status_code == 404
    r = client.get("/internal/run-scheduled/", HTTP_AUTHORIZATION="Bearer s3cret")
    assert r.status_code == 200 and r.json()["status"] == "ok"


def test_public_legal_pages(client):
    for name in ("core:privacy", "core:guidelines"):
        assert client.get(reverse(name)).status_code == 200
    assert "privacy notice" in client.get(reverse("accounts:signup")).content.decode()


def test_delete_account_erases_personal_data(client, make_user, rep):
    student = make_user(email="gone@example.com", full_name="Going Away", matric_number="CSC/9")
    toggle_user_follow(student, rep)
    kept = posts.create_post(author=student, kind="post", body="my old post")
    client.force_login(student)
    r = client.post(reverse("accounts:delete-account"), {"password": "wrong"})
    student.refresh_from_db()
    assert student.is_active  # wrong password: nothing happens
    client.post(reverse("accounts:delete-account"), {"password": PASSWORD})
    student.refresh_from_db()
    assert not student.is_active and student.full_name == "Deleted user" and student.matric_number is None
    assert student.email.endswith("@deleted.invalid") and not student.has_usable_password()
    assert not student.following_set.exists()
    assert "_auth_user_id" not in client.session
    assert not Post.objects.get(pk=kept.pk).is_deleted  # content kept under "Deleted user" by default
    viewer = make_user(email="v@example.com")
    client.force_login(viewer)
    html = client.get(kept.get_absolute_url()).content.decode()
    assert "Deleted user" in html and "Going Away" not in html


def test_delete_account_can_remove_content(make_user):
    student = make_user(email="gone2@example.com")
    post = posts.create_post(author=student, kind="post", body="remove me")
    delete_account(student, delete_content=True)
    assert Post.objects.get(pk=post.pk).is_deleted


def test_topic_chips_are_labelled(client, make_user):
    client.force_login(make_user(email="c@example.com"))
    html = client.get(reverse("posts:compose")).content.decode()
    assert '<label class="chip"><input type="checkbox" name="topics"' in html


def test_cloudinary_storage_keeps_extension_for_word_files(monkeypatch):
    from apps.core.storage import CloudinaryStorage

    calls = []

    class Uploader:
        @staticmethod
        def upload(content, **options):
            calls.append(options)
            if options["resource_type"] == "raw":
                return {"resource_type": "raw", "public_id": options["public_id"]}
            return {"resource_type": "image", "public_id": options["public_id"], "format": "pdf"}

    storage = CloudinaryStorage()
    monkeypatch.setattr(storage, "_uploader", lambda: Uploader)
    assert storage._save("post-files/abc.docx", b"x") == "raw/nexspace/post-files/abc.docx"
    assert storage._save("post-files/abc.pdf", b"x") == "image/nexspace/post-files/abc.pdf"

    class Failing:
        @staticmethod
        def upload(content, **options):
            raise RuntimeError("File size too large")

    monkeypatch.setattr(storage, "_uploader", lambda: Failing)
    from django.core.exceptions import ValidationError

    with pytest.raises(ValidationError):
        storage._save("post-files/big.pdf", b"x")

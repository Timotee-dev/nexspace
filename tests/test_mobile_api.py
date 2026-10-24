"""The Android app's API: device-token sign-in, and the endpoints it uses."""
import pytest
from django.core import mail
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from rest_framework.test import APIClient

from apps.academics.models import Course
from apps.accounts.models import RoleAssignment
from apps.accounts.services import assign_role, issue_code
from apps.messaging import services as dm
from apps.mobile.models import DeviceToken
from apps.posts import services as posts
from apps.resources import services as resources

from .conftest import PASSWORD

pytestmark = pytest.mark.django_db


@pytest.fixture
def api():
    return APIClient()


def sign_in(api, user):
    r = api.post("/api/m/auth/login/", {"email": user.email, "password": PASSWORD, "device": "Test phone"}, format="json")
    assert r.status_code == 200, r.content
    api.credentials(HTTP_AUTHORIZATION=f"Bearer {r.json()['token']}")
    return r.json()


def test_login_returns_a_token_that_works_without_cookies(api, make_user):
    user = make_user(email="a@example.com")
    data = sign_in(api, user)
    assert data["me"]["email"] == "a@example.com"
    stored = DeviceToken.objects.get(user=user)
    assert stored.key_hash != data["token"] and stored.name == "Test phone"  # only a hash is kept
    api.cookies.clear()
    assert api.get("/api/m/me/").json()["email"] == "a@example.com"
    assert api.get("/api/posts/?tab=for-you").status_code == 200  # existing API accepts the token too


def test_wrong_password_and_lockout(api, make_user):
    make_user(email="a@example.com")
    for _ in range(5):
        r = api.post("/api/m/auth/login/", {"email": "a@example.com", "password": "nope"}, format="json")
    assert r.status_code in (400, 429)
    r = api.post("/api/m/auth/login/", {"email": "a@example.com", "password": PASSWORD}, format="json")
    assert r.status_code == 429  # locked like the website


def test_bad_or_revoked_tokens(api, make_user):
    user = make_user(email="a@example.com")
    api.credentials(HTTP_AUTHORIZATION="Bearer not-a-real-token")
    assert api.get("/api/m/me/").status_code == 401
    sign_in(api, user)
    api.post("/api/m/auth/logout/")
    assert api.get("/api/m/me/").status_code == 401
    sign_in(api, user)
    user.set_password("A-new-password-2026")
    user.save()
    assert api.get("/api/m/me/").status_code == 401  # password change signs phones out
    user.set_password(PASSWORD)
    user.save()
    sign_in(api, user)
    user.is_active = False
    user.save(update_fields=["is_active"])
    assert api.get("/api/m/me/").status_code == 401  # banned


def test_signup_then_verify_with_code(api, department):
    deps = api.get("/api/m/departments/").json()
    assert deps["departments"][0]["id"] == department.pk
    r = api.post("/api/m/auth/signup/", {
        "full_name": "New Student", "email": "new@example.com", "password": PASSWORD, "confirm_password": PASSWORD,
        "department": department.pk, "level": 100}, format="json")
    assert r.status_code == 201 and not r.json()["me"]["email_verified"]
    assert mail.outbox
    api.credentials(HTTP_AUTHORIZATION=f"Bearer {r.json()['token']}")
    assert api.post("/api/m/auth/verify/", {"code": "000000"}, format="json").status_code in (400,)
    from apps.accounts.models import User

    code = issue_code(User.objects.get(email="new@example.com"), "verify")
    r = api.post("/api/m/auth/verify/", {"code": code}, format="json")
    assert r.status_code == 200 and r.json()["me"]["email_verified"] and r.json()["me"]["onboarding_completed"]


def test_courses_resources_and_signed_download(api, make_user, department):
    course = Course.objects.create(department=department, code="CSC 301", title="DS", level=300, semester=1)
    rep = make_user(email="rep@example.com")
    assign_role(user=rep, role=RoleAssignment.Role.COURSE_REP, course=course)
    resources.upload(user=rep, course=course, title="Exam 2024", resource_type="past_question",
                     file=SimpleUploadedFile("exam.pdf", b"%PDF-1.4\n%exam\n", content_type="application/pdf"))
    student = make_user(email="s@example.com")
    sign_in(api, student)
    assert api.get("/api/m/courses/").json()["courses"][0]["code"] == "CSC 301"
    detail = api.get("/api/m/courses/csc-301/").json()
    link = detail["resources"][0]["download_url"]
    plain = APIClient()  # the phone's file viewer has no token
    r = plain.get(link)
    assert r.status_code == 200 and b"%PDF" in b"".join(r.streaming_content)
    assert plain.get(link.replace("/files/", "/files/x")).status_code == 404  # tampered link


def test_post_attachments_get_signed_links_in_the_app(api, make_user):
    import io

    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (20, 20), "red").save(buf, "JPEG")
    author = make_user(email="a@example.com")
    post = posts.create_post(author=author, kind="post", body="pic",
                             images=[SimpleUploadedFile("p.jpg", buf.getvalue(), content_type="image/jpeg")])
    sign_in(api, make_user(email="b@example.com"))
    url = api.get(f"/api/posts/{post.pk}/").json()["attachments"][0]["url"]
    assert "/api/m/files/" in url
    assert APIClient().get(url).status_code == 200


def test_messages_through_the_app(api, make_user):
    a = make_user(email="a@example.com", username="ada")
    b = make_user(email="b@example.com", username="bayo")
    sign_in(api, a)
    pk = api.post("/api/m/conversations/start/bayo/").json()["id"]
    r = api.post(f"/api/m/conversations/{pk}/messages/", {"body": "hello from the app"})
    assert r.status_code == 201 and r.json()["message"]["mine"]
    other = APIClient()
    sign_in(other, b)
    assert other.get("/api/m/counts/").json()["messages"] == 1
    convs = other.get("/api/m/conversations/").json()["conversations"]
    assert convs[0]["unread"] and convs[0]["last"]["body"] == "hello from the app"
    msgs = other.get(f"/api/m/conversations/{pk}/messages/").json()["messages"]
    assert msgs[-1]["body"] == "hello from the app" and not msgs[-1]["mine"]
    outsider = APIClient()
    sign_in(outsider, make_user(email="c@example.com"))
    assert outsider.get(f"/api/m/conversations/{pk}/messages/").status_code == 404


def test_spaces_calendar_announcements_report(api, make_user, department):
    from apps.spaces.models import Space

    Space.objects.create(department=department, kind="community", name="General", slug="general",
                         requires_approval=False)
    user = make_user(email="a@example.com")
    sign_in(api, user)
    spaces = api.get("/api/m/spaces/").json()["spaces"]
    general = next(s for s in spaces if s["slug"] == "general")
    assert api.post(f"/api/m/spaces/{general['id']}/join/", {}, format="json").json()["joined"]
    assert api.get(f"/api/m/spaces/{general['id']}/posts/").json()["private"] is False
    assert api.get("/api/m/calendar/").status_code == 200
    assert api.get("/api/m/announcements/").status_code == 200
    other = make_user(email="b@example.com")
    post = posts.create_post(author=other, kind="post", body="spam")
    r = api.post("/api/m/reports/", {"target_type": "post", "target_id": post.pk, "reason": "spam"}, format="json")
    assert r.status_code == 201


def test_website_still_uses_sessions(client, make_user):
    client.force_login(make_user(email="a@example.com"))
    assert client.get("/api/m/me/").status_code == 200


def test_profile_follow_and_anonymous_posts_hidden(api, make_user):
    me = make_user(email="a@example.com")
    other = make_user(email="b@example.com")
    posts.create_post(author=other, kind="post", body="Hello from b")
    posts.create_post(author=other, kind="post", body="secret", is_anonymous=True)
    sign_in(api, me)
    data = api.get(f"/api/m/users/{other.username}/").json()
    assert data["is_me"] is False and data["is_following"] is False
    assert [p["body"] for p in data["posts"]] == ["Hello from b"]
    assert api.post(f"/api/users/{other.username}/follow/").json()["following"] is True
    assert api.get(f"/api/m/users/{other.username}/").json()["followers"] == 1


def test_repost_quote_and_edit_from_app(api, make_user):
    me = make_user(email="a@example.com")
    other = make_user(email="b@example.com")
    post = posts.create_post(author=other, kind="post", body="Original")
    sign_in(api, me)
    r = api.post(f"/api/m/posts/{post.pk}/repost/", {}, format="json")
    assert r.json() == {"reposted": True, "count": 1}
    assert api.get(f"/api/posts/{post.pk}/").json()["is_reposted"] is True
    api.post(f"/api/m/posts/{post.pk}/repost/", {"action": "undo"}, format="json")
    r = api.post(f"/api/m/posts/{post.pk}/repost/", {"quote": "So true"}, format="json")
    assert r.json()["count"] == 1
    feed = api.get("/api/posts/?tab=following").json()["results"] + api.get("/api/posts/?tab=for-you").json()["results"]
    quote = next(p for p in feed if p["body"] == "So true")
    assert quote["quoted"]["body"] == "Original"
    mine = posts.create_post(author=me, kind="post", body="typo")
    r = api.post(f"/api/m/posts/{mine.pk}/edit/", {"body": "fixed"}, format="json")
    assert r.status_code == 200 and r.json()["body"] == "fixed" and r.json()["edited_at"]
    assert api.post(f"/api/m/posts/{post.pk}/edit/", {"body": "hack"}, format="json").status_code == 403

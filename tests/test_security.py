"""Security audit (v0.13): every fix here has a test that tries the attack and checks it's blocked."""
import io
import os
import subprocess
import sys
import zipfile

import pytest
from django.core import mail
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import RequestFactory
from PIL import Image
from rest_framework.test import APIClient

from apps.accounts.models import RoleAssignment
from apps.accounts.services import assign_role, issue_code, check_code
from apps.core import net, ratelimit
from apps.messaging import services as dm
from apps.moderation import services as moderation
from apps.moderation.models import Report
from apps.posts import services as posts
from apps.spaces import services as spaces
from apps.spaces.models import Space

from .conftest import PASSWORD

pytestmark = pytest.mark.django_db
CF = "HTTP_CF_CONNECTING_IP"


# --- Real client address ---------------------------------------------------------------------------
def test_client_ip_trusts_cloudflare_not_the_visitor():
    rf = RequestFactory()
    req = rf.get("/", HTTP_X_FORWARDED_FOR="6.6.6.6, 104.22.17.40", **{CF: "99.61.165.29"})
    assert net.client_ip(req) == "99.61.165.29"
    req = rf.get("/", HTTP_X_FORWARDED_FOR="6.6.6.6, 10.0.0.9")  # no Cloudflare: the proxy-added (last) entry
    assert net.client_ip(req) == "10.0.0.9"
    req = rf.get("/", **{CF: "not-an-ip"}, REMOTE_ADDR="10.1.1.1")
    assert net.client_ip(req) == "10.1.1.1"


def test_faking_x_forwarded_for_does_not_reset_the_lockout(client, make_user):
    make_user(email="victim@example.com")
    for i in range(5):
        client.post("/login/", {"email": "victim@example.com", "password": "nope"},
                    HTTP_X_FORWARDED_FOR=f"1.2.3.{i}", **{CF: "5.5.5.5"})
    r = client.post("/login/", {"email": "victim@example.com", "password": PASSWORD},
                    HTTP_X_FORWARDED_FOR="9.9.9.9", **{CF: "5.5.5.5"})
    assert r.status_code == 429  # same real address, still locked


def test_a_stranger_cannot_lock_the_owner_out(client, make_user):
    make_user(email="owner@example.com")
    for _ in range(5):
        client.post("/login/", {"email": "owner@example.com", "password": "nope"}, **{CF: "5.5.5.5"})
    r = client.post("/login/", {"email": "owner@example.com", "password": PASSWORD}, **{CF: "8.8.4.4"})
    assert r.status_code == 302  # the real owner, from their own connection, still gets in


def test_api_throttles_key_on_the_real_address():
    from apps.core.throttles import AnonRateThrottle

    req = RequestFactory().get("/", HTTP_X_FORWARDED_FOR="1.1.1.1", **{CF: "7.7.7.7"})
    assert AnonRateThrottle().get_ident(req) == "7.7.7.7"


# --- Open redirects -----------------------------------------------------------------------------------
@pytest.mark.parametrize("target", ["https://evil.example/", "//evil.example/", "/\\evil.example/", "javascript:alert(1)"])
def test_next_links_never_leave_the_site(client, make_user, target):
    alice, bob = make_user(email="a@example.com"), make_user(email="b@example.com")
    post = posts.create_post(author=bob, kind="post", body="Hello there")
    client.force_login(alice)
    r = client.post(f"/report/post/{post.pk}/", {"reason": "spam", "next": target})
    assert r.status_code == 302 and r["Location"] == "/"
    page = client.get(f"/report/post/{post.pk}/?next={target}").content.decode()
    assert "evil.example" not in page and "javascript:" not in page
    r = client.post(f"/messages/block/{bob.username}/", {"next": target})
    assert "evil" not in r["Location"] and "javascript" not in r["Location"]


def test_admin_sign_in_next_is_safe(client, make_user):
    client.force_login(make_user(email="s@example.com"))
    page = client.get("/manage/sign-in/?next=/\\evil.example/").content.decode()
    assert "evil.example" not in page


def test_django_admin_login_uses_the_protected_sign_in(client):
    r = client.get("/django-admin/login/?next=/django-admin/")
    assert r.status_code == 302 and r["Location"].startswith("/login/?next=")


# --- Stored script attack through opportunity / event links -------------------------------------------
@pytest.mark.parametrize("link", ["javascript:alert(document.cookie)", "data:text/html,<script>alert(1)</script>",
                                  "JaVaScRiPt:alert(1)", " javascript:alert(1)", "ftp://files.example/x"])
def test_api_rejects_dangerous_apply_links(make_user, link):
    api = APIClient()
    api.force_authenticate(make_user())
    r = api.post("/api/posts/", {"kind": "opportunity", "title": "Internship", "body": "Apply now",
                                 "opportunity": {"organization": "Acme", "category": "internship", "apply_url": link}},
                 format="json")
    assert r.status_code == 400
    assert not posts.OpportunityDetails.objects.exists()


def test_api_opportunity_only_saves_known_checked_fields(make_user):
    api = APIClient()
    api.force_authenticate(make_user())
    r = api.post("/api/posts/", {"kind": "opportunity", "title": "Internship", "body": "Apply",
                                 "opportunity": {"organization": "Acme", "category": "internship",
                                                 "apply_url": "https://acme.example/apply", "id": 999,
                                                 "deadline": "2030-01-31"}}, format="json")
    assert r.status_code == 201, r.content
    opp = posts.OpportunityDetails.objects.get()
    assert opp.pk != 999 and str(opp.deadline) == "2030-01-31"
    r = api.post("/api/posts/", {"kind": "opportunity", "title": "x", "opportunity": {
        "organization": "Acme", "category": "not-a-category"}}, format="json")
    assert r.status_code == 400
    r = api.post("/api/posts/", {"kind": "event", "title": "Party", "event": {"starts_at": "tomorrow-ish"}}, format="json")
    assert r.status_code == 400


def test_study_group_meeting_links_are_checked(make_user):
    from apps.groups import services as groups

    with pytest.raises(ValidationError):
        groups.create_group(user=make_user(), name="Algo", meeting_link="javascript:alert(1)")


# --- Private content can't leak through reports or moderation -----------------------------------------
@pytest.fixture
def chat(make_user):
    alice, bob, eve = (make_user(email=f"{n}@example.com", full_name=n.title()) for n in ("alice", "bob", "eve"))
    convo = dm.start_conversation(alice, bob)
    message = dm.send_message(sender=alice, conversation=convo, body="my secret exam plan")
    return alice, bob, eve, message


def test_report_page_never_previews_other_peoples_messages(client, chat):
    alice, bob, eve, message = chat
    client.force_login(eve)
    r = client.get(f"/report/message/{message.pk}/")
    assert r.status_code == 404 and b"secret" not in r.content
    client.force_login(bob)
    assert client.get(f"/report/message/{message.pk}/").status_code == 200


def test_report_page_never_previews_private_space_posts(client, make_user, department):
    owner, outsider = make_user(email="o@example.com"), make_user(email="x@example.com")
    space = Space.objects.create(department=department, name="Exco", slug="exco", requires_approval=True)
    spaces.add_member(space, owner)
    post = posts.create_post(author=owner, kind="post", body="confidential minutes", space=space)
    client.force_login(outsider)
    r = client.get(f"/report/post/{post.pk}/")
    assert r.status_code == 404 and b"confidential" not in r.content
    with pytest.raises(ValidationError):
        moderation.submit_report(reporter=outsider, target_type="post", target_id=post.pk, reason="spam")


def test_moderators_only_touch_messages_someone_reported(chat, make_user, department):
    alice, bob, eve, message = chat
    mod = make_user(email="mod@example.com")
    assign_role(user=mod, role=RoleAssignment.Role.MODERATOR, department=department)
    with pytest.raises(PermissionDenied):
        moderation.remove_content(moderator=mod, target_type="message", target_id=message.pk)
    with pytest.raises(PermissionDenied):
        moderation.dismiss(moderator=mod, target_type="message", target_id=message.pk)
    assert not moderation.ModerationAction.objects.exists()  # nothing logged, so no message text leaked
    moderation.submit_report(reporter=bob, target_type="message", target_id=message.pk, reason="harassment")
    moderation.remove_content(moderator=mod, target_type="message", target_id=message.pk)
    message.refresh_from_db()
    assert message.is_deleted


def test_moderation_handles_missing_or_junk_targets(client, make_user, department):
    mod = make_user(email="mod@example.com")
    assign_role(user=mod, role=RoleAssignment.Role.MODERATOR, department=department)
    client.force_login(mod)
    for target_type, target_id in (("post", "999999"), ("nonsense", "1"), ("post", "abc")):
        r = client.post("/moderation/action/", {"action": "remove", "target_type": target_type, "target_id": target_id})
        assert r.status_code == 302


def test_moderators_cannot_suspend_admins_and_days_are_checked(make_user, department):
    mod = make_user(email="mod@example.com")
    assign_role(user=mod, role=RoleAssignment.Role.MODERATOR, department=department)
    other_mod = make_user(email="mod2@example.com")
    assign_role(user=other_mod, role=RoleAssignment.Role.MODERATOR, department=department)
    admin = make_user(email="admin@example.com")
    assign_role(user=admin, role=RoleAssignment.Role.DEPARTMENT_ADMIN, department=department)
    student = make_user(email="s@example.com")
    with pytest.raises(PermissionDenied):
        moderation.suspend_user(moderator=mod, user=admin)
    with pytest.raises(PermissionDenied):
        moderation.suspend_user(moderator=mod, user=other_mod)
    moderation.suspend_user(moderator=admin, user=other_mod, days=3)  # admins can act on moderators
    with pytest.raises(ValidationError):
        moderation.suspend_user(moderator=mod, user=student, days="lots")
    moderation.suspend_user(moderator=mod, user=student, days=100000)
    student.refresh_from_db()
    from django.utils import timezone

    assert (student.suspended_until - timezone.now()).days <= 365


# --- Verification codes ---------------------------------------------------------------------------------
def test_code_attempts_are_used_up_even_by_the_right_code(make_user):
    user = make_user(verified=False)
    code = issue_code(user, "verify")
    for _ in range(5):
        assert not check_code(user, "verify", "000000" if code != "000000" else "111111")
    assert not check_code(user, "verify", code)  # no attempts left, even for the right code
    code = issue_code(user, "verify")
    assert check_code(user, "verify", code) and not check_code(user, "verify", code)  # single use


# --- Email flooding ---------------------------------------------------------------------------------------
def _signup(client, n, ip="3.3.3.3"):
    from apps.academics.models import Department

    return client.post("/signup/", {
        "account_type": "student", "full_name": f"Person {n}", "email": f"p{n}@example.com",
        "password": PASSWORD, "confirm_password": PASSWORD, "department": Department.objects.first().pk,
        "level": 100, "terms": "on", "accept_terms": "on",
    }, **{CF: ip})


def test_signups_are_limited_per_connection(client, department):
    from apps.accounts.models import User

    for n in range(7):
        _signup(client, n)
        client.logout()
    assert User.objects.count() == 5
    _signup(client, 99, ip="4.4.4.4")
    assert User.objects.filter(email="p99@example.com").exists()  # other people are unaffected


def test_password_reset_emails_are_limited_and_use_site_url(client, make_user, settings):
    settings.SITE_URL = "https://nexspace.example"
    make_user(email="target@example.com")
    for i in range(5):
        r = client.post("/password-reset/", {"email": "target@example.com"}, HTTP_HOST="localhost",
                        **{CF: f"2.2.2.{i}"})
        assert r.status_code == 302  # always the same "sent" page
    assert len(mail.outbox) == 3  # per-email hourly limit
    assert "https://nexspace.example/password-reset/" in mail.outbox[0].body


def test_mobile_password_reset_is_limited(make_user):
    make_user(email="target@example.com")
    api = APIClient()
    for i in range(5):
        api.post("/api/m/auth/password-reset/", {"email": "target@example.com"}, format="json", **{CF: f"2.2.3.{i}"})
    assert len(mail.outbox) == 3


# --- Push notifications can't be aimed at internal addresses ----------------------------------------------
@pytest.mark.parametrize("endpoint", [
    "https://169.254.169.254/latest/meta-data", "https://127.0.0.1/x", "https://localhost/x", "http://fcm.googleapis.com/x",
    "https://evil.example/fcm.googleapis.com", "https://fcm.googleapis.com.evil.example/x",
    "https://user:pw@fcm.googleapis.com/x", "https://fcm.googleapis.com:8443/x",
])
def test_push_endpoints_must_be_real_push_services(make_user, endpoint):
    api = APIClient()
    api.force_authenticate(make_user())
    r = api.post("/api/push/subscriptions/", {"endpoint": endpoint, "keys": {"p256dh": "a", "auth": "b"}}, format="json")
    assert r.status_code == 400


def test_real_push_services_are_accepted():
    from apps.notifications.webpush import is_push_service

    for ok in ("https://fcm.googleapis.com/fcm/send/abc", "https://updates.push.services.mozilla.com/wpush/v2/x",
               "https://web.push.apple.com/QG3", "https://wns2-par02p.notify.windows.com/w/?token=x"):
        assert is_push_service(ok)


# --- Upload bombs ---------------------------------------------------------------------------------------------
def _docx(document_xml: bytes):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", "<Types/>")
        z.writestr("word/document.xml", document_xml)
    return buf.getvalue()


def test_zip_bomb_documents_are_refused_without_unpacking():
    from apps.nexai import extract

    bomb = _docx(b"<w:document>" + b"a" * (40 * 1024 * 1024) + b"</w:document>")  # ~40 KB on disk
    assert len(bomb) < 200_000
    with pytest.raises(Exception):
        extract.pages_from_file(io.BytesIO(bomb), "bomb.docx")


def test_xml_entity_tricks_are_refused():
    from apps.nexai import extract

    evil = _docx(b'<?xml version="1.0"?><!DOCTYPE d [<!ENTITY a "aaaaaaaaaa"><!ENTITY b "&a;&a;&a;&a;">]>'
                 b"<w:document>&b;</w:document>")
    with pytest.raises(extract.UnsupportedFile):
        extract.pages_from_file(io.BytesIO(evil), "evil.docx")


def test_image_bombs_are_refused():
    from apps.core.uploads import validate_image_upload

    buf = io.BytesIO()
    Image.new("1", (6000, 6000)).save(buf, "PNG")  # 36 megapixels in a few KB
    with pytest.raises(ValidationError, match="too large"):
        validate_image_upload(SimpleUploadedFile("bomb.png", buf.getvalue(), content_type="image/png"))


def test_unprocessable_images_are_refused_not_stored_with_location_data(monkeypatch):
    from apps.core import uploads

    buf = io.BytesIO()
    Image.new("RGB", (50, 50)).save(buf, "JPEG")
    monkeypatch.setattr(uploads.ImageOps if hasattr(uploads, "ImageOps") else __import__("PIL.ImageOps").ImageOps,
                        "exif_transpose", lambda *_: (_ for _ in ()).throw(OSError("broken")))
    with pytest.raises(ValidationError):
        uploads.optimize_image(SimpleUploadedFile("x.jpg", buf.getvalue(), content_type="image/jpeg"))


def test_huge_documents_are_refused_before_being_opened(settings):
    from apps.core.uploads import compress_document

    settings.COMPRESS_INPUT_MAX_MB = 1
    settings.STORAGE_FILE_LIMIT_BYTES = 1024 * 1024
    settings.STORAGE_FILE_LIMIT_MB = 1
    big = SimpleUploadedFile("big.pdf", b"%PDF-1.4\n" + b"0" * (3 * 1024 * 1024))
    with pytest.raises(ValidationError, match="can't be stored"):
        compress_document(big, "application/pdf")


# --- Browser protections ---------------------------------------------------------------------------------------
def test_security_headers(client, make_user):
    r = client.get("/login/")
    csp = r["Content-Security-Policy"]
    assert "script-src 'self'" in csp and "'unsafe-inline'" not in csp.split("script-src")[1].split(";")[0]
    assert "frame-ancestors 'none'" in csp and "object-src 'none'" in csp
    assert "camera=()" in r["Permissions-Policy"]
    client.force_login(make_user())
    assert "private" in client.get("/").headers.get("Cache-Control", "")


def test_pages_have_no_inline_scripts_or_handlers(client, make_user, department):
    import re

    client.force_login(make_user())
    for path in ("/", "/login/", "/messages/", "/spaces/", "/opportunities/", "/settings/", "/resources/upload/"):
        client.logout() if path == "/login/" else None
        html = client.get(path).content.decode()
        assert not re.search(r"<script(?![^>]*\bsrc=)[^>]*>", html), path
        assert not re.search(r"\son[a-z]+=\"", html), path
        client.force_login(make_user(email=f"u{abs(hash(path))}@example.com"))


def test_debug_is_off_on_render_even_without_the_variable():
    env = {k: v for k, v in os.environ.items() if k != "DEBUG"}
    env.update(RENDER="true", SECRET_KEY="x" * 50, DJANGO_SETTINGS_MODULE="config.settings")
    out = subprocess.run([sys.executable, "-c", "from django.conf import settings; print(settings.DEBUG)"],
                         env=env, capture_output=True, text=True, cwd=os.path.dirname(os.path.dirname(__file__)))
    assert out.stdout.strip() == "False", out.stderr


# --- Junk input never crashes a page ------------------------------------------------------------------------------
def test_junk_numbers_and_ids_never_cause_server_errors(client, chat, make_user):
    alice, bob, eve, message = chat
    client.force_login(alice)
    convo = message.conversation
    assert client.get(f"/messages/{convo.pk}/poll/?after=abc&since_id=x").status_code == 200
    assert client.post("/messages/groups/new/", {"title": "G", "members": ["abc", "1; DROP"]}).status_code == 200
    api = APIClient()
    api.force_authenticate(alice)
    assert api.get(f"/api/m/conversations/{convo.pk}/messages/?after=abc").status_code == 200
    assert api.post("/api/m/reports/", {"target_type": "post", "target_id": "abc", "reason": "spam"},
                    format="json").status_code == 400

import io
import json
from datetime import timedelta

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from django.core.cache import cache
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone
from PIL import Image
from rest_framework.test import APIClient

from apps.academics.models import Course
from apps.accounts.models import RoleAssignment
from apps.accounts.services import assign_role
from apps.discover import services as discover
from apps.groups import services as groups
from apps.groups.models import StudyGroup
from apps.notices import services as notices
from apps.notices.models import AcademicEvent, Audience
from apps.notifications import services as notifications
from apps.notifications import webpush
from apps.notifications.models import Notification, NotificationPreference, PushSubscription
from apps.posts import services as posts
from apps.posts.models import Post
from apps.resources import services as resources
from apps.search import services as search
from apps.social.services import toggle_user_follow
from apps.spaces import services as spaces

pytestmark = pytest.mark.django_db
PDF = b"%PDF-1.4 demo"


@pytest.fixture
def run_hooks(django_capture_on_commit_callbacks):
    """Notification hooks run after commit; execute them immediately in tests."""
    def _run(fn, *args, **kwargs):
        with django_capture_on_commit_callbacks(execute=True):
            return fn(*args, **kwargs)
    return _run


@pytest.fixture
def alice(make_user):
    return make_user(email="alice@example.com", full_name="Alice Author")


@pytest.fixture
def bob(make_user):
    return make_user(email="bob@example.com", full_name="Bob Reader")


@pytest.fixture
def admin(make_user, department):
    u = make_user(email="admin@example.com", full_name="Dept Admin")
    assign_role(user=u, role=RoleAssignment.Role.DEPARTMENT_ADMIN, department=department)
    return u


@pytest.fixture
def course(department):
    return Course.objects.create(department=department, code="CSC 301", title="Data Structures", level=300, semester=1)


def texts(user):
    return list(Notification.objects.filter(recipient=user).values_list("text", flat=True))


# --- Notifications ---------------------------------------------------------------
def test_reply_mention_and_no_self_notify(run_hooks, alice, bob, make_user):
    carol = make_user(email="carol@example.com", full_name="Carol")
    post = run_hooks(posts.create_post, author=alice, kind="question", body=f"Help @{carol.username}")
    assert texts(carol) == ["Alice Author mentioned you in a post"]
    answer = run_hooks(posts.add_comment, user=bob, post=post, body="Try this")
    assert texts(alice) == ["Bob Reader answered your question"]
    run_hooks(posts.add_comment, user=alice, post=post, body="Thanks!", parent=answer)
    assert "Alice Author replied to your comment" in texts(bob)
    assert len(texts(alice)) == 1  # nothing for her own reply


def test_anonymous_mention_hides_author(run_hooks, alice, bob):
    run_hooks(posts.create_post, author=alice, kind="post", body=f"@{bob.username} hi", is_anonymous=True)
    n = Notification.objects.get(recipient=bob)
    assert n.text == "Anonymous Student mentioned you in a post" and n.actor is None


def test_vote_milestone_once(run_hooks, make_user, alice):
    post = posts.create_post(author=alice, kind="post", body="p")
    voters = [make_user(email=f"v{i}@example.com") for i in range(6)]
    for v in voters:
        run_hooks(posts.vote_post, user=v, post=post, value=1)
    run_hooks(posts.vote_post, user=voters[0], post=post, value=0)
    run_hooks(posts.vote_post, user=voters[0], post=post, value=1)
    assert texts(alice).count("Your post reached 5 upvotes") == 1


def test_follow_and_accept(run_hooks, alice, bob):
    run_hooks(toggle_user_follow, bob, alice)
    assert texts(alice) == ["Bob Reader followed you"]
    q = posts.create_post(author=alice, kind="question", body="?")
    a = posts.add_comment(user=bob, post=q, body="!")
    run_hooks(posts.accept_answer, user=alice, comment=a)
    assert "Your answer was accepted (+15 NexScore)" in texts(bob)


def test_resource_and_announcement_audiences(run_hooks, admin, make_user, course):
    member = make_user(email="m@example.com", level=300)
    other = make_user(email="o@example.com", level=100)
    spaces.join(member, course.space)
    run_hooks(resources.upload, user=admin, course=course, title="Exam", resource_type="past_question",
              file=SimpleUploadedFile("e.pdf", PDF))
    assert texts(member) == ["New past question in CSC 301: Exam"] and texts(other) == []
    run_hooks(notices.publish_announcement, user=admin, title="Level only", body="x", audience=Audience.LEVEL,
              target_level=300, priority="urgent")
    n = Notification.objects.get(recipient=member, kind="announcement")
    assert n.is_critical and n.text == "Urgent: Level only"
    assert not Notification.objects.filter(recipient=other, kind="announcement").exists()


def test_preferences_and_critical_bypass(run_hooks, admin, alice, bob):
    NotificationPreference.objects.create(user=alice, social_in_app=False, department_in_app=False)
    run_hooks(toggle_user_follow, bob, alice)
    assert texts(alice) == []
    run_hooks(notices.publish_announcement, user=admin, title="Normal", body="x")
    run_hooks(notices.publish_announcement, user=admin, title="Big", body="x", priority="important")
    assert texts(alice) == ["Important: Big"]


def test_unread_count_mark_read_and_open(run_hooks, client, alice, bob):
    run_hooks(toggle_user_follow, bob, alice)
    api = APIClient()
    api.force_authenticate(alice)
    assert api.get(reverse("api-unread-count")).data["unread"] == 1
    n = Notification.objects.get(recipient=alice)
    client.force_login(alice)
    r = client.get(reverse("notifications:open", args=[n.pk]))
    assert r.status_code == 302 and r.url == f"/u/{bob.username}/"
    assert api.get(reverse("api-unread-count")).data["unread"] == 0
    client.force_login(bob)
    assert client.get(reverse("notifications:open", args=[n.pk])).status_code == 404  # not theirs
    assert client.get(reverse("notifications:list")).status_code == 200


def test_preferences_page_saves(client, alice):
    client.force_login(alice)
    client.post(reverse("notifications:preferences"), {"academic_in_app": "on", "department_in_app": "on"})
    prefs = NotificationPreference.objects.get(user=alice)
    assert not prefs.social_in_app and prefs.academic_in_app and not prefs.opportunities_in_app


def test_scheduled_reminders_are_sent_once(admin, make_user, course, alice):
    member = make_user(email="mem@example.com")
    spaces.join(member, course.space)
    tomorrow = timezone.now() + timedelta(days=1)
    notices.create_event(user=admin, title="Final exam", kind=AcademicEvent.Kind.EXAM, audience=Audience.COURSE,
                         target_course=course, starts_at=tomorrow)
    opp = posts.create_post(author=alice, kind="opportunity", title="Internship", body="",
                            opportunity={"organization": "Acme", "category": "internship",
                                         "deadline": timezone.localdate() + timedelta(days=2)})
    discover.set_reminder(user=member, post=opp, days_before=3)
    call_command("run_scheduled")
    call_command("run_scheduled")
    got = texts(member)
    assert got.count("CSC 301 Exam tomorrow: Final exam") == 1
    assert got.count("Internship: deadline in 2 days") == 1
    assert Notification.objects.get(recipient=member, kind="event_reminder").is_critical


# --- Web Push ----------------------------------------------------------------------
def _decrypt(body, ua_private, auth):
    salt, rs, idlen = body[:16], int.from_bytes(body[16:20], "big"), body[20]
    as_public = body[21:21 + idlen]
    ciphertext = body[21 + idlen:]
    ua_public = ua_private.public_key().public_bytes(serialization.Encoding.X962,
                                                     serialization.PublicFormat.UncompressedPoint)
    shared = ua_private.exchange(ec.ECDH(), ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), as_public))
    ikm = HKDF(hashes.SHA256(), 32, salt=auth, info=b"WebPush: info\x00" + ua_public + as_public).derive(shared)
    cek = HKDF(hashes.SHA256(), 16, salt=salt, info=b"Content-Encoding: aes128gcm\x00").derive(ikm)
    nonce = HKDF(hashes.SHA256(), 12, salt=salt, info=b"Content-Encoding: nonce\x00").derive(ikm)
    plain = AESGCM(cek).decrypt(nonce, ciphertext, None)
    assert rs == 4096 and plain.endswith(b"\x02")
    return plain[:-1]


def test_webpush_encryption_roundtrip():
    ua_private = ec.generate_private_key(ec.SECP256R1())
    ua_public = ua_private.public_key().public_bytes(serialization.Encoding.X962,
                                                     serialization.PublicFormat.UncompressedPoint)
    auth = b"0123456789abcdef"
    body = webpush.encrypt(b'{"title":"hi"}', webpush.b64u_encode(ua_public), webpush.b64u_encode(auth))
    assert _decrypt(body, ua_private, auth) == b'{"title":"hi"}'


def test_vapid_header_signature_verifies():
    from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature

    public, private = webpush.generate_vapid_keys()
    header = webpush.vapid_header("https://push.example.com/abc", public, private, "mailto:a@b.c")
    token = header.split("t=")[1].split(",")[0]
    h, c, sig = token.split(".")
    raw = webpush.b64u_decode(sig)
    key = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), webpush.b64u_decode(public))
    key.verify(encode_dss_signature(int.from_bytes(raw[:32], "big"), int.from_bytes(raw[32:], "big")),
               f"{h}.{c}".encode(), ec.ECDSA(hashes.SHA256()))
    assert json.loads(webpush.b64u_decode(c))["aud"] == "https://push.example.com"


def test_push_subscription_api_and_delivery(settings, monkeypatch, run_hooks, alice, bob):
    api = APIClient()
    api.force_authenticate(alice)
    bad = api.post(reverse("api-push-subscriptions"), {"endpoint": "http://x.test/1", "keys": {"p256dh": "a", "auth": "b"}},
                   format="json")
    assert bad.status_code == 400
    ok = api.post(reverse("api-push-subscriptions"),
                  {"endpoint": "https://push.test/1", "keys": {"p256dh": "a", "auth": "b"}}, format="json")
    assert ok.status_code == 201 and PushSubscription.objects.filter(user=alice).count() == 1

    settings.PUSH_ENABLED = True
    settings.VAPID_PUBLIC_KEY, settings.VAPID_PRIVATE_KEY = webpush.generate_vapid_keys()
    NotificationPreference.objects.create(user=alice, social_push=True)
    run_hooks(toggle_user_follow, bob, alice)
    assert Notification.objects.get(recipient=alice).push_status == "pending"
    monkeypatch.setattr(webpush, "send", lambda *a, **k: 410)
    stats = notifications.send_pending_push()
    assert stats["expired_subscriptions"] == 1 and not PushSubscription.objects.exists()
    assert Notification.objects.get(recipient=alice).push_status == "sent"


# --- Search -------------------------------------------------------------------------
def test_global_search(run_hooks, make_user, other_department, alice, course, admin):
    spaces.join(alice, course.space)
    resources.upload(user=admin, course=course, title="Sorting past questions", resource_type="past_question",
                     file=SimpleUploadedFile("s.pdf", PDF))
    posts.create_post(author=alice, kind="post", body="Anyone revising CSC 301 sorting tonight?")
    posts.create_post(author=alice, kind="post", body="secret CSC 301 worry", is_anonymous=True)
    outsider = make_user(email="x@example.com", department=other_department)
    Course.objects.create(department=other_department, code="CSC 301", title="Elsewhere", level=300, semester=1)
    results = search.search(alice, "CSC 301")
    assert [c.title for c in results["courses"]] == ["Data Structures"]
    assert results["spaces"] and results["resources"]
    assert alice in results["people"]  # members of CSC 301
    assert search.search(alice, "csc301")["courses"]
    assert search.search(alice, "x") == {}
    api = APIClient()
    api.force_authenticate(admin)
    data = api.get(reverse("api-search") + "?q=worry").data
    assert data["posts"][0]["author"] == "Anonymous Student"
    assert "Alice" not in json.dumps(data)
    assert "Elsewhere" not in json.dumps(api.get(reverse("api-search") + "?q=CSC 301").data)


def test_search_pages(client, alice):
    client.force_login(alice)
    assert client.get(reverse("search:search") + "?q=abc").status_code == 200
    partial = client.get(reverse("search:search") + "?q=abc&partial=1").content.decode()
    assert "<html" not in partial


# --- Explore, trending, opportunities ---------------------------------------------------
def test_trending_prefers_recent_engagement(make_user, alice, department):
    quiet = posts.create_post(author=alice, kind="post", body="quiet")
    busy = posts.create_post(author=alice, kind="post", body="busy")
    for i in range(3):
        u = make_user(email=f"t{i}@example.com")
        posts.vote_post(user=u, post=busy, value=1)
        posts.add_comment(user=u, post=busy, body="!")
    snap = discover.compute_trending(department.pk)
    assert snap.post_ids[0] == busy.pk and quiet.pk in snap.post_ids


def test_explore_and_opportunities_pages(client, alice, bob):
    today = timezone.localdate()
    open_opp = posts.create_post(author=alice, kind="opportunity", title="Open one", body="",
                                 opportunity={"organization": "A", "category": "internship", "deadline": today + timedelta(days=5)})
    closed = posts.create_post(author=alice, kind="opportunity", title="Closed one", body="",
                               opportunity={"organization": "B", "category": "scholarship", "deadline": today + timedelta(days=5)})
    closed.opportunity.deadline = today - timedelta(days=1)
    closed.opportunity.save()
    client.force_login(bob)
    assert client.get(reverse("discover:explore")).status_code == 200
    page = client.get(reverse("discover:opportunities")).content.decode()
    assert "Open one" in page and "Closed one" not in page
    assert "Closed one" in client.get(reverse("discover:opportunities") + "?closed=1").content.decode()
    client.post(reverse("discover:remind", args=[open_opp.pk]), {"days": "3"})
    r = open_opp.reminders.get(user=bob)
    assert r.remind_on == today + timedelta(days=2)
    with pytest.raises(ValidationError):
        discover.set_reminder(user=bob, post=open_opp, days_before=5)


# --- Study groups -------------------------------------------------------------------------
def test_study_groups(client, run_hooks, alice, bob, make_user):
    g = groups.create_group(user=alice, name="Revision crew")
    assert groups.join(user=bob, group=g) is True
    g.refresh_from_db()
    assert g.member_count == 2
    private = groups.create_group(user=alice, name="Private", is_open=False)
    with pytest.raises(PermissionDenied):
        groups.join(user=bob, group=private)
    assert groups.join(user=bob, group=private, invite_code=private.invite_code)
    client.force_login(make_user(email="z@example.com"))
    assert client.get(private.get_absolute_url()).status_code == 404
    assert client.get(f"{private.get_absolute_url()}?invite={private.invite_code}").status_code == 200
    with pytest.raises(PermissionDenied):
        groups.post_message(user=make_user(email="y@example.com"), group=g, body="hi")
    groups.post_message(user=bob, group=g, body="hello")
    groups.update_meeting(user=alice, group=g, next_meeting_at=timezone.now() + timedelta(days=2))
    assert any("next meeting" in t for t in texts(bob))
    with pytest.raises(PermissionDenied):
        groups.update_meeting(user=bob, group=g, next_meeting_at=None)
    groups.leave(user=alice, group=g)
    assert g.memberships.get(user=bob).is_admin  # admin role handed over
    groups.leave(user=bob, group=g)
    assert not StudyGroup.objects.filter(pk=g.pk).exists()


# --- Admin dashboard -----------------------------------------------------------------------
def test_admin_access_control(client, alice, admin):
    client.force_login(alice)
    assert client.get(reverse("manage:overview")).status_code == 403
    assert client.get(reverse("manage:users")).status_code == 403
    client.force_login(admin)
    assert client.get(reverse("manage:overview")).status_code == 200
    assert client.get(reverse("manage:departments")).status_code == 403  # super admins only
    assert client.get(reverse("manage:topics")).status_code == 403


def test_admin_manages_courses_roles_and_accounts(client, admin, alice, department):
    client.force_login(admin)
    r = client.post(reverse("manage:course-create"), {"code": "csc 401", "title": "Software Engineering", "units": 3,
                                                      "level": 400, "semester": 1, "is_active": "on"})
    assert r.status_code == 302
    course = Course.objects.get(code="CSC 401")
    assert course.space.is_official
    client.post(reverse("manage:user-action", args=[alice.pk]), {"action": "assign_role", "role": "course_rep",
                                                                 "course": course.pk})
    assert alice.has_role(RoleAssignment.Role.COURSE_REP, course=course)
    ra = alice.role_assignments.get()
    client.post(reverse("manage:user-action", args=[alice.pk]), {"action": "revoke_role", "assignment": ra.pk})
    assert not alice.role_assignments.exists()
    client.post(reverse("manage:user-action", args=[alice.pk]), {"action": "suspend", "days": 7})
    alice.refresh_from_db()
    assert alice.is_suspended
    client.post(reverse("manage:user-action", args=[alice.pk]), {"action": "ban"})
    alice.refresh_from_db()
    assert not alice.is_active
    client.post(reverse("manage:user-action", args=[alice.pk]), {"action": "unban"})
    alice.refresh_from_db()
    assert alice.is_active
    assert client.get(reverse("manage:users") + "?q=alice&status=suspended").status_code == 200


def test_admin_cannot_touch_other_departments(client, admin, make_user, other_department):
    outsider = make_user(email="far@example.com", department=other_department)
    client.force_login(admin)
    assert client.get(reverse("manage:user", args=[outsider.pk])).status_code == 404
    assert client.post(reverse("manage:user-action", args=[outsider.pk]), {"action": "ban"}).status_code == 404


def test_super_admin_switches_department(client, make_user, other_department, department):
    boss = make_user(email="arifalotimothy@gmail.com")  # only the owner can be a platform admin
    assign_role(user=boss, role=RoleAssignment.Role.SUPER_ADMIN)
    client.force_login(boss)
    page = client.get(reverse("manage:overview") + f"?dept={other_department.pk}").content.decode()
    assert other_department.name in page
    r = client.post(reverse("manage:departments"), {"university": "New U", "short_name": "NU", "faculty": "Arts",
                                                    "department": "History", "code": "HIS"})
    assert r.status_code == 302


def test_sessions_and_spaces_admin(client, admin, department):
    client.force_login(admin)
    client.post(reverse("manage:sessions"), {"name": "2025/2026", "is_current": "on"})
    client.post(reverse("manage:sessions"), {"name": "2026/2027", "is_current": "on"})
    from apps.academics.models import AcademicSession

    assert list(AcademicSession.objects.filter(is_current=True).values_list("name", flat=True)) == ["2026/2027"]
    client.post(reverse("manage:space-create"), {"name": "Final Year Projects", "kind": "community",
                                                 "is_official": "on", "level": ""})
    from apps.spaces.models import Space

    assert Space.objects.get(slug="final-year-projects").is_official


# --- PWA, performance, privacy --------------------------------------------------------------
def test_pwa_endpoints(client):
    manifest = json.loads(client.get("/manifest.webmanifest").content)
    assert manifest["display"] == "standalone" and len(manifest["icons"]) == 3
    sw = client.get("/sw.js")
    assert sw["Content-Type"] == "application/javascript" and sw["Service-Worker-Allowed"] == "/"
    assert "showNotification" in sw.content.decode()
    assert client.get("/offline/").status_code == 200


def test_feed_has_no_n_plus_one(client, make_user, alice, bob):
    def count():
        client.force_login(bob)
        with CaptureQueriesContext(connection) as ctx:
            assert client.get(reverse("core:home")).status_code == 200
        return len(ctx.captured_queries)

    for i in range(3):
        posts.create_post(author=alice, kind="post", body=f"p{i}", topics=[])
    cache.clear()
    few = count()
    authors = [make_user(email=f"a{i}@example.com") for i in range(12)]
    for i, a in enumerate(authors):
        cache.clear()
        posts.create_post(author=a, kind="poll", body=f"poll {i}", poll_options=["a", "b"])
    cache.clear()
    many = count()
    assert many - few <= 3, (few, many)


def test_uploaded_images_are_resized_and_stripped(alice):
    buf = io.BytesIO()
    img = Image.new("RGB", (3000, 2000), "red")
    exif = Image.Exif()
    exif[0x010F] = "SecretCam"  # camera make
    img.save(buf, "JPEG", exif=exif)
    upload = SimpleUploadedFile("big.jpg", buf.getvalue(), content_type="image/jpeg")
    post = posts.create_post(author=alice, kind="post", body="pic", images=[upload])
    stored = post.attachments.get()
    with Image.open(stored.file) as im:
        assert max(im.size) == 1600 and not im.getexif()


def test_last_seen_is_recorded(client, alice):
    client.force_login(alice)
    client.get(reverse("core:home"))
    alice.refresh_from_db()
    assert alice.last_seen_at is not None

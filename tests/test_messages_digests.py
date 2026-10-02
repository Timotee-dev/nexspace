from datetime import datetime, timedelta

import pytest
from django.core import mail
from django.core.exceptions import PermissionDenied, ValidationError
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import RoleAssignment
from apps.accounts.services import assign_role
from apps.messaging import services as dm
from apps.messaging.models import Message
from apps.moderation import services as moderation
from apps.notifications import digest
from apps.notifications.models import DigestLog, Notification, NotificationPreference
from apps.posts import services as posts
from apps.social.services import toggle_user_follow

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


# --- Direct messages --------------------------------------------------------------------
def test_conversation_flow_and_unread(run_hooks, alice, bob):
    conv = dm.start_conversation(alice, bob)
    assert dm.start_conversation(bob, alice) == conv  # one conversation per pair
    run_hooks(dm.send_message, sender=alice, conversation=conv, body="hi Bob")
    assert dm.unread_count(bob) == 1 and dm.unread_count(alice) == 0
    assert Notification.objects.filter(recipient=bob, kind="message").count() == 1
    run_hooks(dm.send_message, sender=alice, conversation=conv, body="you there?")
    assert Notification.objects.filter(recipient=bob, kind="message").count() == 1  # bursts don't spam
    dm.mark_read(bob, conv)
    assert dm.unread_count(bob) == 0
    rows = dm.inbox(bob)
    assert rows[0]["other"] == alice and rows[0]["last"].body == "you there?"


def test_who_can_message(alice, bob, make_user, other_department, department):
    outsider = make_user(email="far@example.com", department=other_department)
    assert not dm.can_message(alice, outsider)[0]
    assert not dm.can_message(alice, alice)[0]
    bob.profile.allow_messages_from = "nobody"
    bob.profile.save()
    with pytest.raises(PermissionDenied):
        dm.start_conversation(alice, bob)
    bob.profile.allow_messages_from = "following"
    bob.profile.save()
    assert not dm.can_message(alice, bob)[0]
    toggle_user_follow(bob, alice)
    assert dm.can_message(alice, bob)[0]
    bob.profile.allow_messages_from = "nobody"
    bob.profile.save()
    admin = make_user(email="hod@example.com")
    assign_role(user=admin, role=RoleAssignment.Role.DEPARTMENT_ADMIN, department=department)
    assert dm.can_message(admin, bob)[0]  # official contact still possible
    unverified = make_user(email="new@example.com", verified=False)
    with pytest.raises(PermissionDenied):
        dm.start_conversation(unverified, alice)


def test_blocking_stops_both_directions(alice, bob):
    conv = dm.start_conversation(alice, bob)
    dm.block(bob, alice)
    for sender in (alice, bob):
        with pytest.raises(PermissionDenied):
            dm.send_message(sender=sender, conversation=conv, body="x")
    dm.unblock(bob, alice)
    dm.send_message(sender=alice, conversation=conv, body="sorry")


def test_muted_conversations_do_not_notify(run_hooks, alice, bob):
    conv = dm.start_conversation(alice, bob)
    dm.toggle_mute(bob, conv)
    run_hooks(dm.send_message, sender=alice, conversation=conv, body="hello")
    assert not Notification.objects.filter(recipient=bob, kind="message").exists()
    assert dm.unread_count(bob) == 1  # still counted as unread


def test_message_pages_are_private(client, alice, bob, make_user):
    conv = dm.start_conversation(alice, bob)
    dm.send_message(sender=alice, conversation=conv, body="secret plans")
    client.force_login(bob)
    page = client.get(conv.get_absolute_url()).content.decode()
    assert "secret plans" in page
    stranger = make_user(email="s@example.com")
    client.force_login(stranger)
    assert client.get(conv.get_absolute_url()).status_code == 404
    assert client.get(reverse("messaging:poll", args=[conv.pk])).status_code == 404
    assert client.post(reverse("messaging:send", args=[conv.pk]), {"body": "x"}).status_code == 404


def test_live_chat_endpoints(client, alice, bob):
    conv = dm.start_conversation(alice, bob)
    first = dm.send_message(sender=alice, conversation=conv, body="one")
    client.force_login(bob)
    r = client.post(reverse("messaging:send", args=[conv.pk]), {"body": "<b>two</b>"}, HTTP_ACCEPT="application/json")
    sent = r.json()["message"]
    assert sent["mine"] and "&lt;b&gt;" in sent["html"]  # escaped, never raw HTML
    data = client.get(reverse("messaging:poll", args=[conv.pk]) + f"?after={first.pk}&since_id={first.pk}").json()
    assert [m["id"] for m in data["messages"]] == [sent["id"]]
    client.force_login(alice)
    client.post(reverse("messaging:delete", args=[conv.pk, first.pk]), HTTP_ACCEPT="application/json")
    client.force_login(bob)
    data = client.get(reverse("messaging:poll", args=[conv.pk]) + f"?after={sent['id']}&since_id={first.pk}").json()
    assert data["deleted"] == [first.pk]
    assert client.get(reverse("messaging:live")).json() == {"notifications": 0, "messages": 0}


def test_cannot_delete_someone_elses_message(alice, bob):
    conv = dm.start_conversation(alice, bob)
    message = dm.send_message(sender=alice, conversation=conv, body="mine")
    with pytest.raises(PermissionDenied):
        dm.delete_message(user=bob, message=message)


def test_reporting_messages(alice, bob, make_user, department):
    conv = dm.start_conversation(alice, bob)
    message = dm.send_message(sender=alice, conversation=conv, body="rude words")
    outsider = make_user(email="o@example.com")
    with pytest.raises(ValidationError):
        moderation.submit_report(reporter=outsider, target_type="message", target_id=message.pk, reason="harassment")
    moderation.submit_report(reporter=bob, target_type="message", target_id=message.pk, reason="harassment")
    mod = make_user(email="mod@example.com")
    assign_role(user=mod, role=RoleAssignment.Role.MODERATOR, department=department)
    queue = moderation.queue_for(mod)
    assert queue[0]["obj"] == message and queue[0]["owner"] == alice
    moderation.remove_content(moderator=mod, target_type="message", target_id=message.pk)
    message.refresh_from_db()
    assert message.is_deleted


def test_profile_message_button_and_privacy_setting(client, alice, bob):
    client.force_login(alice)
    html = client.get(reverse("accounts:profile", args=["bob"])).content.decode()
    assert "Message Bob Bello" in html
    r = client.post(reverse("messaging:start", args=["bob"]))
    assert r.status_code == 302 and "/messages/" in r.url
    client.force_login(bob)
    client.post(reverse("accounts:settings-privacy"), {"visibility": "everyone", "allow_messages_from": "nobody"})
    bob.profile.refresh_from_db()
    assert bob.profile.allow_messages_from == "nobody"
    client.post(reverse("messaging:block", args=["alice"]))
    assert dm.has_blocked(bob, alice)
    assert "Alice Ade" in client.get(reverse("accounts:settings-privacy")).content.decode()


# --- Digests ----------------------------------------------------------------------------
def _at(hour, weekday_offset=0):
    """A timezone-aware moment today at `hour` local time."""
    local = timezone.localtime().replace(hour=hour, minute=5, second=0, microsecond=0)
    return local + timedelta(days=weekday_offset)


def test_digest_sent_once_with_content(run_hooks, alice, bob, settings):
    settings.DIGEST_HOUR = 7
    run_hooks(posts.create_post, author=alice, kind="post", body=f"hey @bob")
    assert digest.send_due_digests(_at(6)) == 0  # too early
    sent = digest.send_due_digests(_at(9))
    assert sent >= 1
    to_bob = [m for m in mail.outbox if m.to == ["bob@example.com"]]
    assert len(to_bob) == 1 and "notification" in to_bob[0].subject
    assert "List-Unsubscribe" in to_bob[0].extra_headers
    assert digest.send_due_digests(_at(10)) == 0  # once per period
    assert DigestLog.objects.filter(user=bob).count() == 1


def test_no_email_when_nothing_new(alice, settings):
    settings.DIGEST_HOUR = 0
    assert digest.send_due_digests(_at(9)) == 0
    assert mail.outbox == [] and DigestLog.objects.filter(user=alice).exists()


def test_digest_respects_preferences_and_cap(run_hooks, make_user, alice, settings):
    settings.DIGEST_HOUR = 0
    settings.DIGEST_DAILY_CAP = 2
    people = [make_user(email=f"p{i}@example.com", username=f"person{i}") for i in range(4)]
    NotificationPreference.objects.create(user=people[0], digest="off")
    for p in people:
        run_hooks(posts.create_post, author=alice, kind="post", body=f"hello @{p.username}")
    assert digest.send_due_digests(_at(9)) == 2  # capped
    recipients = {m.to[0] for m in mail.outbox}
    assert "p0@example.com" not in recipients
    assert digest.send_due_digests(_at(10)) == 0  # cap reached for today


def test_unsubscribe_link(client, alice):
    token = digest.unsubscribe_token(alice)
    url = reverse("notifications:digest-unsubscribe", args=[token])
    assert "Stop digest emails?" in client.get(url).content.decode()  # opening the link changes nothing
    prefs = NotificationPreference.objects.filter(user=alice).first()
    assert prefs is None or prefs.digest != "off"
    client.post(url)
    assert NotificationPreference.objects.get(user=alice).digest == "off"
    assert client.get(reverse("notifications:digest-unsubscribe", args=["bad-token"])).status_code == 400


def test_digest_choice_in_settings(client, alice):
    client.force_login(alice)
    client.post(reverse("notifications:preferences"), {"digest": "daily", "social_in_app": "on"})
    assert NotificationPreference.objects.get(user=alice).digest == "daily"
    assert "Summary email" in client.get(reverse("notifications:preferences")).content.decode()

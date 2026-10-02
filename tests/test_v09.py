"""Extra checks for v0.9 not covered by test_edits_reposts.py and test_messages_digests.py."""
import pytest
from django.core import mail
from django.core.exceptions import ValidationError
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import RoleAssignment
from apps.accounts.services import assign_role
from apps.messaging import services as dm
from apps.messaging.models import Message
from apps.moderation import services as moderation
from apps.notifications.digest import send_due_digests
from apps.notifications.models import Notification, NotificationPreference
from apps.posts import feed
from apps.posts import services as posts

pytestmark = pytest.mark.django_db


@pytest.fixture
def run_hooks(django_capture_on_commit_callbacks):
    def _run(fn, *args, **kwargs):
        with django_capture_on_commit_callbacks(execute=True):
            return fn(*args, **kwargs)
    return _run


@pytest.fixture
def ada(make_user):
    return make_user(email="ada@example.com", full_name="Ada Lovelace", username="ada")


@pytest.fixture
def bayo(make_user):
    return make_user(email="bayo@example.com", full_name="Bayo Adeyemi", username="bayo")


def _seven_am(days=0):
    local = timezone.localtime(timezone.now()).replace(hour=8, minute=0) + timezone.timedelta(days=days)
    return local


def test_plain_reposts_do_not_duplicate_for_you(ada, bayo):
    original = posts.create_post(author=ada, kind="post", body="dup check")
    posts.repost(user=bayo, post=original)
    items, _ = feed.get_feed(ada, "for-you")
    assert [p.pk for p in items].count(original.pk) == 1



def test_chat_views_send_poll_delete(client, ada, bayo):
    client.force_login(ada)
    r = client.post(reverse("messaging:start", args=["bayo"]))
    conv_url = r.url
    pk = int(conv_url.strip("/").split("/")[-1])
    r = client.post(reverse("messaging:send", args=[pk]), {"body": "<b>hi</b> see https://example.com"},
                    HTTP_ACCEPT="application/json")
    msg = r.json()["message"]
    assert msg["mine"] and "&lt;b&gt;" in msg["html"] and 'href="https://example.com"' in msg["html"]
    client.force_login(bayo)
    data = client.get(reverse("messaging:poll", args=[pk]) + "?after=0").json()
    assert len(data["messages"]) == 1 and not data["messages"][0]["mine"]
    assert "hi" in client.get(reverse("messaging:inbox")).content.decode()
    client.force_login(ada)
    client.post(reverse("messaging:delete", args=[pk, msg["id"]]), HTTP_ACCEPT="application/json")
    assert Message.objects.get(pk=msg["id"]).is_deleted
    client.force_login(bayo)
    assert client.post(reverse("messaging:delete", args=[pk, msg["id"]])).status_code == 302  # not theirs: no-op
    data = client.get(reverse("messaging:poll", args=[pk]) + f"?after={msg['id']}&since_id={msg['id']}").json()
    assert data["deleted"] == [msg["id"]]



def test_reporting_a_message_reaches_moderators(client, ada, bayo, make_user, department):
    conv = dm.start_conversation(ada, bayo)
    message = dm.send_message(sender=ada, conversation=conv, body="rude words")
    with pytest.raises(ValidationError):
        moderation.submit_report(reporter=make_user(email="x@example.com"), target_type="message",
                                 target_id=message.pk, reason="harassment")  # not in the conversation
    moderation.submit_report(reporter=bayo, target_type="message", target_id=message.pk, reason="harassment")
    mod = make_user(email="mod@example.com")
    assign_role(user=mod, role=RoleAssignment.Role.MODERATOR, department=department)
    client.force_login(mod)
    assert "rude words" in client.get(reverse("moderation:queue")).content.decode()
    moderation.remove_content(moderator=mod, target_type="message", target_id=message.pk)
    message.refresh_from_db()
    assert message.is_deleted



def test_digest_respects_hour_cap_and_off(ada, bayo, settings):
    Notification.objects.create(recipient=ada, category="social", kind="mention", text="hi", url="/")
    Notification.objects.create(recipient=bayo, category="social", kind="mention", text="hi", url="/")
    settings.DIGEST_HOUR = 23
    assert send_due_digests(_seven_am()) == 0  # too early
    settings.DIGEST_HOUR = 7
    settings.DIGEST_DAILY_CAP = 1
    assert send_due_digests(_seven_am()) == 1  # cap reached, the other waits
    settings.DIGEST_DAILY_CAP = 250
    NotificationPreference.objects.update_or_create(user=bayo, defaults={"digest": "off"})
    NotificationPreference.objects.update_or_create(user=ada, defaults={"digest": "off"})
    mail.outbox.clear()
    assert send_due_digests(_seven_am()) == 0



def test_live_comments(client, ada, bayo):
    post = posts.create_post(author=ada, kind="post", body="discuss")
    client.force_login(ada)
    assert client.get(reverse("posts:live-comments", args=[post.pk]) + "?after=0").json()["new"] == 0
    posts.add_comment(user=bayo, post=post, body="first!")
    assert client.get(reverse("posts:live-comments", args=[post.pk]) + "?after=0").json()["new"] == 1

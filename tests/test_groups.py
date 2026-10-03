"""Group chats, and photos/files in messages."""
import io

import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from PIL import Image

from apps.messaging import services as dm
from apps.messaging.models import ConversationMember, Message, MessageAttachment
from apps.notifications.models import Notification

pytestmark = pytest.mark.django_db


@pytest.fixture
def run_hooks(django_capture_on_commit_callbacks):
    def _run(fn, *args, **kwargs):
        with django_capture_on_commit_callbacks(execute=True):
            return fn(*args, **kwargs)
    return _run


@pytest.fixture
def people(make_user):
    return [make_user(email=f"p{i}@example.com", full_name=f"Person {i}", username=f"person{i}") for i in range(4)]


def jpeg(name="photo.jpg"):
    buf = io.BytesIO()
    Image.new("RGB", (40, 30), "red").save(buf, "JPEG")
    return SimpleUploadedFile(name, buf.getvalue(), content_type="image/jpeg")


def pdf(name="notes.pdf"):
    return SimpleUploadedFile(name, b"%PDF-1.4\n%test\n", content_type="application/pdf")


def test_create_group_and_message_everyone(run_hooks, people):
    a, b, c, _ = people
    group = run_hooks(dm.create_group, creator=a, title="Project team", members=[b, c])
    assert group.is_group and group.members.count() == 3
    assert ConversationMember.objects.get(conversation=group, user=a).is_admin
    assert Notification.objects.filter(recipient=b, text__contains="added you").exists()
    run_hooks(dm.send_message, sender=b, conversation=group, body="hello team")
    assert dm.unread_count(a) == 1 and dm.unread_count(c) == 1 and dm.unread_count(b) == 0
    assert Notification.objects.filter(recipient=c, kind="message", text__contains="Project team").exists()


def test_group_rules(people, make_user, other_department):
    a, b, c, d = people
    with pytest.raises(ValidationError):
        dm.create_group(creator=a, title="  ", members=[b])
    with pytest.raises(ValidationError):
        dm.create_group(creator=a, title="Solo", members=[])
    outsider = make_user(email="far@example.com", department=other_department)
    with pytest.raises(PermissionDenied):
        dm.create_group(creator=a, title="Mixed", members=[b, outsider])  # other departments can't be added
    d.profile.allow_messages_from = "nobody"
    d.profile.save()
    with pytest.raises(PermissionDenied):
        dm.create_group(creator=a, title="T", members=[d])  # respects "who can message me"
    dm.block(c, a)
    with pytest.raises(PermissionDenied):
        dm.create_group(creator=a, title="T", members=[c])  # and blocks


def test_admin_powers_and_leaving(people):
    a, b, c, d = people
    group = dm.create_group(creator=a, title="Team", members=[b, c])
    with pytest.raises(PermissionDenied):
        dm.add_members(actor=b, group=group, people=[d])  # only admins add people
    dm.add_members(actor=a, group=group, people=[d])
    dm.rename_group(actor=a, group=group, title="Dream team")
    dm.remove_member(actor=a, group=group, person=c)
    group.refresh_from_db()
    assert group.title == "Dream team" and set(group.members.values_list("user_id", flat=True)) == {a.pk, b.pk, d.pk}
    system = list(group.messages.filter(is_system=True).values_list("body", flat=True))
    assert any("added Person 3" in t for t in system) and any("removed Person 2" in t for t in system)
    with pytest.raises(PermissionDenied):
        dm.send_message(sender=c, conversation=group, body="still here?")  # removed people can't post
    dm.leave_group(user=a, group=group)
    assert ConversationMember.objects.get(conversation=group, user=b).is_admin  # admin passes to longest member


def test_photos_and_files_in_messages(client, people):
    a, b, c, _ = people
    conv = dm.start_conversation(a, b)
    message = dm.send_message(sender=a, conversation=conv, body="", files=[jpeg(), pdf()])
    kinds = sorted(message.attachments.values_list("kind", flat=True))
    assert kinds == ["file", "image"]
    image = message.attachments.get(kind="image")
    with Image.open(image.file) as img:
        assert not img.getexif()  # location data stripped
    url = reverse("messaging:attachment", args=[conv.pk, image.pk])
    client.force_login(b)
    assert client.get(url).status_code == 200
    client.force_login(c)  # not in the conversation
    assert client.get(url).status_code == 404


def test_attachment_limits_and_types(people):
    a, b, _, _ = people
    conv = dm.start_conversation(a, b)
    with pytest.raises(ValidationError):
        dm.send_message(sender=a, conversation=conv, body="x", files=[pdf(f"{i}.pdf") for i in range(5)])
    with pytest.raises(ValidationError):
        dm.send_message(sender=a, conversation=conv, body="",
                        files=[SimpleUploadedFile("run.exe", b"MZ\x90\x00", content_type="application/octet-stream")])
    with pytest.raises(ValidationError):
        dm.send_message(sender=a, conversation=conv, body="   ")


def test_deleting_a_message_removes_its_files(people):
    a, b, _, _ = people
    conv = dm.start_conversation(a, b)
    message = dm.send_message(sender=a, conversation=conv, body="", files=[pdf()])
    dm.delete_message(user=a, message=message)
    assert not MessageAttachment.objects.filter(message=message).exists()


def test_group_pages_and_json(client, people):
    a, b, c, d = people
    client.force_login(a)
    r = client.post(reverse("messaging:new-group"), {"title": "Study buddies", "members": [b.pk, c.pk]})
    group_url = r.url
    pk = int(group_url.strip("/").split("/")[-1])
    page = client.get(group_url).content.decode()
    assert "Study buddies" in page and "created the group" in page
    r = client.post(reverse("messaging:send", args=[pk]), {"body": "hi all", "files": [jpeg()]},
                    HTTP_ACCEPT="application/json")
    msg = r.json()["message"]
    assert msg["attachments"][0]["kind"] == "image" and msg["sender"] == "Person 0"
    details = client.get(reverse("messaging:group", args=[pk])).content.decode()
    assert "Person 3" in details and "Make admin" in details  # can add Person 3; can promote others
    client.post(reverse("messaging:group", args=[pk]), {"action": "add", "members": [d.pk]})
    assert ConversationMember.objects.filter(conversation_id=pk, user=d).exists()
    assert "Study buddies" in client.get(reverse("messaging:inbox")).content.decode()
    client.force_login(b)
    assert "Make admin" not in client.get(reverse("messaging:group", args=[pk])).content.decode()
    client.post(reverse("messaging:action", args=[pk]), {"action": "leave"})
    assert not ConversationMember.objects.filter(conversation_id=pk, user=b).exists()


def test_group_message_reports_need_membership(people, make_user):
    from apps.moderation import services as moderation

    a, b, c, d = people
    group = dm.create_group(creator=a, title="Team", members=[b, c])
    message = dm.send_message(sender=a, conversation=group, body="rude")
    with pytest.raises(ValidationError):
        moderation.submit_report(reporter=d, target_type="message", target_id=message.pk, reason="harassment")
    moderation.submit_report(reporter=b, target_type="message", target_id=message.pk, reason="harassment")

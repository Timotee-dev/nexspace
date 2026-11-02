"""Fresh start: the owner wipes test data before launch; the setup survives."""
import io
import os

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from PIL import Image

from apps.academics.models import Course
from apps.accounts.models import RoleAssignment, User
from apps.accounts.services import assign_role
from apps.messaging import services as dm
from apps.messaging.models import Conversation, MessageAttachment
from apps.posts import services as posts
from apps.posts.models import Post
from apps.resources import services as resources
from apps.resources.models import Resource
from apps.spaces import services as spaces
from apps.spaces.models import Space

from .conftest import PASSWORD

pytestmark = pytest.mark.django_db
PHRASE = "DELETE ALL TEST DATA"


@pytest.fixture
def world(make_user, department, settings, tmp_path):
    settings.MEDIA_ROOT = str(tmp_path)
    owner = make_user(email="arifalotimothy@gmail.com", full_name="Owner")
    assign_role(user=owner, role=RoleAssignment.Role.SUPER_ADMIN)
    alice, bob = make_user(email="alice@example.com"), make_user(email="bob@example.com")
    course = Course.objects.create(department=department, code="CSC 301", title="Data Structures", level=300, semester=1)
    spaces.add_member(course.space, owner)
    spaces.add_member(course.space, alice)
    community = Space.objects.create(department=department, name="Chess club", slug="chess", kind=Space.Kind.COMMUNITY)
    posts.create_post(author=alice, kind="post", body="test post from alice")
    posts.create_post(author=owner, kind="post", body="test post from owner")
    buf = io.BytesIO(); Image.new("RGB", (30, 30)).save(buf, "PNG")
    convo = dm.start_conversation(alice, bob)
    dm.send_message(sender=alice, conversation=convo, body="hi",
                    files=[SimpleUploadedFile("p.png", buf.getvalue(), content_type="image/png")])
    assign_role(user=alice, role=RoleAssignment.Role.COURSE_REP, course=course)
    resources.upload(user=alice, course=course, title="Notes", resource_type="lecture_note",
                     file=SimpleUploadedFile("n.pdf", b"%PDF-1.4\n%%EOF"))
    return owner, alice, bob, course, community


def test_wrong_phrase_or_password_deletes_nothing(client, world):
    owner = world[0]
    client.force_login(owner)
    page = client.get("/platform/fresh-start/").content.decode()
    assert "Accounts (everyone except you)" in page and PHRASE in page
    client.post("/platform/fresh-start/", {"confirm": "delete", "password": PASSWORD})
    client.post("/platform/fresh-start/", {"confirm": PHRASE, "password": "wrong"})
    assert User.objects.count() == 3 and Post.objects.count() == 2


def test_only_the_owner_can_see_it(client, world):
    owner, alice = world[0], world[1]
    client.force_login(alice)
    r = client.post("/platform/fresh-start/", {"confirm": PHRASE, "password": PASSWORD})
    assert User.objects.count() == 3
    assert r.status_code == 302 and r["Location"].startswith("/manage/sign-in/")  # the admin-only sign-in screen


def test_fresh_start_wipes_test_data_and_keeps_the_setup(client, world, department):
    owner, alice, bob, course, community = world
    stored = [a.file.path for a in MessageAttachment.objects.all()] + [r.file.path for r in Resource.objects.all()]
    assert all(os.path.exists(p) for p in stored)
    client.force_login(owner)
    r = client.post("/platform/fresh-start/", {"confirm": PHRASE, "password": PASSWORD})
    assert r.status_code == 302
    assert list(User.objects.values_list("email", flat=True)) == ["arifalotimothy@gmail.com"]
    assert not Post.objects.exists() and not Conversation.objects.exists() and not Resource.objects.exists()
    assert not Space.objects.filter(pk=community.pk).exists()
    assert not any(os.path.exists(p) for p in stored)  # uploaded files removed too
    # the setup survives
    assert Course.objects.filter(pk=course.pk).exists()
    course.space.refresh_from_db()
    assert course.space.member_count == 1 and course.space.memberships.filter(user=owner).exists()
    owner.refresh_from_db()
    assert owner.is_owner and owner.department_id == department.pk
    assert client.get("/").status_code == 200  # still signed in, site works
    from apps.core.models import AuditLog
    assert AuditLog.objects.filter(action="platform.fresh_start").exists()

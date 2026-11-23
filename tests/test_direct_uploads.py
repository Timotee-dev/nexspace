"""Direct uploads: files go from the browser straight to Cloudinary (skipping Vercel's 4.5 MB limit),
then come back to NexSpace through the normal upload checks."""
import io
import json

import cloudinary
import cloudinary.utils
import pytest
from django.core import signing
from django.urls import reverse
from PIL import Image

from apps.core import direct_uploads
from apps.messaging.services import start_conversation
from apps.posts.models import Post


@pytest.fixture
def direct(settings, monkeypatch):
    """Direct uploads on, with a fake Cloudinary account and no network."""
    settings.DIRECT_UPLOADS = True
    settings.MAX_UPLOAD_MB = 50
    settings.STORAGE_FILE_LIMIT_MB = 10
    settings.STORAGE_FILE_LIMIT_BYTES = 10 * 1024 * 1024
    old = cloudinary.config()
    saved = (old.cloud_name, old.api_key, old.api_secret)
    cloudinary.config(cloud_name="demo", api_key="123456", api_secret="topsecret")
    store, destroyed = {}, []
    monkeypatch.setattr(direct_uploads, "_download",
                        lambda public_id, resource_type="raw", fmt="", limit=None: store[public_id])
    monkeypatch.setattr(direct_uploads, "_destroy", destroyed.append)
    yield type("Direct", (), {"store": store, "destroyed": destroyed})
    cloudinary.config(cloud_name=saved[0], api_key=saved[1], api_secret=saved[2])


def jpeg_bytes(size=(40, 30)):
    buf = io.BytesIO()
    Image.new("RGB", size, "red").save(buf, "JPEG")
    return buf.getvalue()


def sign(client, name, size, kind=""):
    return client.post(reverse("core:upload-sign"), json.dumps({"name": name, "size": size, "type": kind}),
                       content_type="application/json")


def upload(client, direct, name, content, kind=""):
    """What the browser does: ask for permission, then 'upload' to Cloudinary in pieces. Returns the token."""
    permit = sign(client, name, len(content), kind).json()
    size = permit["part_size"]
    for i, part in enumerate(permit["parts"]):
        direct.store[part["fields"]["public_id"]] = content[i * size:(i + 1) * size]
    return permit["token"]


@pytest.mark.django_db
def test_signature_lets_the_browser_upload_one_named_file(client, user, direct):
    client.force_login(user)
    r = sign(client, "past question.pdf", 8 * 1024 * 1024, "application/pdf")  # 8 MB: over Vercel's 4.5 MB
    assert r.status_code == 200
    permit = r.json()
    fields = permit["fields"]
    assert permit["url"] == "https://api.cloudinary.com/v1_1/demo/raw/upload"
    assert fields["public_id"].startswith(f"nexspace-staging/{user.pk}/") and fields["public_id"].endswith(".pdf")
    assert fields["api_key"] == "123456" and "topsecret" not in json.dumps(permit)  # the secret never leaves
    expected = cloudinary.utils.api_sign_request(
        {"timestamp": fields["timestamp"], "public_id": fields["public_id"], "tags": fields["tags"]}, "topsecret")
    assert fields["signature"] == expected


@pytest.mark.django_db
def test_signing_refuses_bad_requests(client, user, direct, settings):
    assert sign(client, "a.pdf", 100).status_code == 403  # signed out
    client.force_login(user)
    assert "too big" in sign(client, "big.pdf", 51 * 1024 * 1024).json()["error"]["message"]
    assert sign(client, "virus.exe", 100).status_code == 400
    assert sign(client, "empty.pdf", 0).status_code == 400
    assert client.get(reverse("core:upload-sign")).status_code == 405
    settings.DIRECT_UPLOADS = False
    assert sign(client, "a.pdf", 100).status_code == 400


@pytest.mark.django_db
def test_post_with_photo_and_pdf_sent_through_cloudinary(client, user, direct):
    client.force_login(user)
    image = upload(client, direct, "campus.jpg", jpeg_bytes(), "image/jpeg")
    doc = upload(client, direct, "notes.pdf", b"%PDF-1.4\n%test\n", "application/pdf")
    r = client.post(reverse("posts:compose"), {"kind": "post", "body": "Sent the new way",
                                               "__direct__images": [image], "__direct__files": [doc]})
    assert r.status_code == 302
    post = Post.objects.get(body="Sent the new way")
    assert post.images[0].content_type == "image/jpeg" and post.images[0].original_name == "campus.jpg"
    assert post.files[0].original_name == "notes.pdf"
    assert len(direct.destroyed) == 2  # the temporary copies are deleted


@pytest.mark.django_db
def test_direct_uploads_still_get_the_real_file_type_check(client, user, direct):
    client.force_login(user)
    token = upload(client, direct, "notes.pdf", b"MZ\x90\x00 this is an exe", "application/pdf")
    r = client.post(reverse("posts:compose"), {"kind": "post", "body": "x", "__direct__files": [token]})
    assert r.status_code == 200 and not Post.objects.filter(body="x").exists()


@pytest.mark.django_db
def test_tokens_belong_to_one_person_and_cant_be_forged(client, user, make_user, direct):
    client.force_login(user)
    token = upload(client, direct, "campus.jpg", jpeg_bytes())
    thief = make_user(email="thief@example.com")
    client.force_login(thief)
    r = client.post(reverse("posts:compose"), {"kind": "post", "body": "stolen", "__direct__images": [token]},
                    HTTP_REFERER="/compose/")
    assert r.status_code == 302 and r.url == "/compose/" and not Post.objects.filter(body="stolen").exists()
    forged = signing.dumps({"u": thief.pk, "p": f"nexspace-staging/{user.pk}/x.jpg", "n": "x.jpg"}, salt="other")
    r = client.post(reverse("posts:compose"), {"kind": "post", "body": "forged", "__direct__images": [forged]},
                    HTTP_ACCEPT="application/json")
    assert r.status_code == 400 and "couldn't be checked" in r.json()["error"]["message"]
    sneaky = signing.dumps({"u": thief.pk, "p": f"nexspace-staging/{user.pk}/x.jpg", "n": "x.jpg"},
                           salt=direct_uploads.SALT)  # someone else's staging file
    r = client.post(reverse("posts:compose"), {"kind": "post", "body": "sneaky", "__direct__images": [sneaky]},
                    HTTP_ACCEPT="application/json")
    assert r.status_code == 400


@pytest.mark.django_db
def test_upload_that_never_arrived_gives_a_clear_message(client, user, direct, monkeypatch):
    client.force_login(user)
    permit = sign(client, "notes.pdf", 10).json()  # signed, but the browser never finished the upload

    def missing(*args, **kwargs):
        raise OSError("404")

    monkeypatch.setattr(direct_uploads, "_download", missing)
    r = client.post(reverse("posts:compose"), {"kind": "post", "body": "y", "__direct__files": [permit["token"]]},
                    HTTP_ACCEPT="application/json")
    assert r.status_code == 400 and "didn't finish uploading" in r.json()["error"]["message"]


@pytest.mark.django_db
def test_chat_photo_sent_through_cloudinary(client, make_user, direct):
    a, b = make_user(email="a@example.com"), make_user(email="b@example.com")
    conversation = start_conversation(a, b)
    client.force_login(a)
    token = upload(client, direct, "board.jpg", jpeg_bytes(), "image/jpeg")
    r = client.post(reverse("messaging:send", args=[conversation.pk]), {"body": "look", "__direct__files": [token]},
                    HTTP_ACCEPT="application/json")
    assert r.status_code == 200 and r.json()["message"]["attachments"][0]["kind"] == "image"


@pytest.mark.django_db
def test_app_api_is_left_alone(client, user, direct):
    """The Android app sends files itself; tokens on /api/ are never fetched."""
    client.force_login(user)
    r = client.post("/api/posts/", {"body": "x", "__direct__images": ["junk"]})
    assert r.status_code != 400 or "couldn't be checked" not in r.content.decode()


@pytest.mark.django_db
def test_page_tells_the_browser_to_upload_directly(client, user, direct, settings):
    client.force_login(user)
    assert 'data-direct-upload="1"' in client.get(reverse("core:home")).content.decode()
    settings.DIRECT_UPLOADS = False
    assert 'data-direct-upload="0"' in client.get(reverse("core:home")).content.decode()


def test_browser_is_allowed_to_reach_cloudinary():
    from apps.core.middleware import CSP

    assert "connect-src 'self' https://api.cloudinary.com" in CSP


def test_unsent_uploads_are_cleaned_up(settings, monkeypatch):
    import cloudinary.api
    from django.utils import timezone

    settings.DIRECT_UPLOADS = True
    old = (timezone.now() - timezone.timedelta(hours=8)).isoformat()
    new = timezone.now().isoformat()
    monkeypatch.setattr(cloudinary.api, "resources", lambda **kw: {"resources": [
        {"public_id": "nexspace-staging/1/old.pdf", "created_at": old},
        {"public_id": "nexspace-staging/1/new.pdf", "created_at": new}]})
    deleted = []
    monkeypatch.setattr(cloudinary.api, "delete_resources", lambda ids, **kw: deleted.extend(ids))
    assert direct_uploads.clean_up_staging() == 1 and deleted == ["nexspace-staging/1/old.pdf"]


def test_cloudinary_files_can_be_read_back(monkeypatch):
    """NexAI and OCR open stored files; with Cloudinary this used to fail for every file."""
    from apps.core.storage import CloudinaryStorage

    calls = []

    def fake_download(public_id, resource_type="raw", fmt="", limit=None):
        calls.append((public_id, resource_type, fmt))
        return b"%PDF-1.4 hello"

    monkeypatch.setattr(direct_uploads, "_download", fake_download)
    storage = CloudinaryStorage()
    with storage.open("image/nexspace/resources/abc.pdf") as f:
        assert f.read() == b"%PDF-1.4 hello"
    storage.open("raw/nexspace/post-files/abc.docx").read()
    assert calls == [("nexspace/resources/abc", "image", "pdf"), ("nexspace/post-files/abc.docx", "raw", "")]



@pytest.mark.django_db
def test_files_that_couldnt_be_read_are_queued_again(make_user, department):
    """The v0.17 migration re-queues materials NexAI/OCR couldn't open before reading from Cloudinary worked."""
    import importlib

    from django.apps import apps as django_apps
    from django.core.files.uploadedfile import SimpleUploadedFile
    from django.utils import timezone

    from apps.academics.models import Course
    from apps.accounts.models import RoleAssignment
    from apps.accounts.services import assign_role
    from apps.core.management.commands.seed_demo import make_pdf
    from apps.resources import services as resources
    from apps.resources.models import Resource

    admin = make_user(email="adm@example.com")
    assign_role(user=admin, role=RoleAssignment.Role.DEPARTMENT_ADMIN, department=department)
    course = Course.objects.create(department=department, code="CSC 999", title="Test", level=300, semester=1)
    made = [resources.upload(user=admin, course=course, title=f"File {i}", resource_type="lecture_note",
                             file=SimpleUploadedFile(f"f{i}.pdf", make_pdf([["Some readable text here."]])))
            for i in range(3)]
    now = timezone.now()
    Resource.objects.filter(pk=made[0].pk).update(indexed_at=now, index_error="This file couldn't be read.")
    Resource.objects.filter(pk=made[1].pk).update(indexed_at=now, ocr_status="failed",
                                                  ocr_error="The file couldn't be opened for reading.")
    Resource.objects.filter(pk=made[2].pk).update(indexed_at=now, index_error="")  # read fine: left alone
    importlib.import_module("apps.resources.migrations.0004_retry_unread_files").retry(django_apps, None)
    rows = {r.pk: r for r in Resource.objects.all()}
    assert rows[made[0].pk].indexed_at is None and rows[made[0].pk].index_error == ""
    assert rows[made[1].pk].indexed_at is None and rows[made[1].pk].ocr_status == ""
    assert rows[made[2].pk].indexed_at is not None



@pytest.mark.django_db
def test_big_files_go_up_in_pieces(client, user, direct):
    """Cloudinary's free plan takes 10 MB per file, so a 20 MB file is sent as three 9 MB pieces."""
    client.force_login(user)
    permit = sign(client, "scan.pdf", 20 * 1024 * 1024).json()
    ids = [p["fields"]["public_id"] for p in permit["parts"]]
    assert len(ids) == 3 and permit["part_size"] == 9 * 1024 * 1024
    assert all(i.startswith(f"nexspace-staging/{user.pk}/") for i in ids) and len(set(ids)) == 3
    for part in permit["parts"]:  # every piece has its own valid signature
        f = part["fields"]
        assert f["signature"] == cloudinary.utils.api_sign_request(
            {"timestamp": f["timestamp"], "public_id": f["public_id"], "tags": f["tags"]}, "topsecret")
    small = sign(client, "notes.pdf", 1000).json()
    assert len(small["parts"]) == 1 and small["parts"][0]["fields"]["public_id"].endswith(".pdf")


@pytest.mark.django_db
def test_pieces_are_joined_in_order(user, direct, client):
    client.force_login(user)
    content = bytes(range(256)) * (80 * 1024)  # 20 MB, every byte position distinct enough to catch reordering
    token = upload(client, direct, "scan.pdf", content)
    received = direct_uploads.receive(user, token)
    assert received.read() == content and received.name == "scan.pdf"
    assert len(direct.destroyed) == 3  # every piece is deleted


@pytest.mark.django_db
def test_missing_piece_is_caught(user, direct, client):
    client.force_login(user)
    content = b"x" * (12 * 1024 * 1024)
    permit = sign(client, "scan.pdf", len(content)).json()
    direct.store[permit["parts"][0]["fields"]["public_id"]] = content[:9 * 1024 * 1024]
    direct.store[permit["parts"][1]["fields"]["public_id"]] = b"short"  # the last piece didn't finish
    with pytest.raises(direct_uploads.DirectUploadError, match="didn't finish uploading"):
        direct_uploads.receive(user, permit["token"])


@pytest.mark.django_db
def test_a_photo_over_10_mb_is_compressed_to_fit(client, user, direct):
    """A big phone photo (over Cloudinary's 10 MB) arrives in pieces and is shrunk before it's stored."""
    import random

    client.force_login(user)
    rnd = random.Random(1)
    image = Image.frombytes("RGB", (2600, 2000), bytes(rnd.getrandbits(8) for _ in range(2600 * 2000 * 3)))
    buf = io.BytesIO()
    image.save(buf, "PNG")  # noise doesn't compress: about 15 MB
    raw = buf.getvalue()
    assert len(raw) > 10 * 1024 * 1024
    token = upload(client, direct, "board.png", raw, "image/png")
    r = client.post(reverse("posts:compose"), {"kind": "post", "body": "big photo", "__direct__images": [token]})
    assert r.status_code == 302
    stored = Post.objects.get(body="big photo").images[0]
    assert stored.file.size < 10 * 1024 * 1024


@pytest.mark.django_db
def test_a_pdf_over_10_mb_with_big_pictures_is_compressed_to_fit(client, user, direct):
    """A scanned PDF over 10 MB arrives in pieces; its pictures are recompressed so it fits storage."""
    import random

    client.force_login(user)
    rnd = random.Random(2)
    pages = []
    for _ in range(3):
        noise = bytes(rnd.getrandbits(8) for _ in range(2200 * 1700 * 3))
        pages.append(Image.frombytes("RGB", (2200, 1700), noise))
    buf = io.BytesIO()
    pages[0].save(buf, "PDF", save_all=True, append_images=pages[1:], quality=100)
    raw = buf.getvalue()
    assert len(raw) > 10 * 1024 * 1024
    token = upload(client, direct, "scanned exam.pdf", raw, "application/pdf")
    r = client.post(reverse("posts:compose"), {"kind": "post", "body": "big pdf", "__direct__files": [token]})
    assert r.status_code == 302, r.content.decode()[:500]
    stored = Post.objects.get(body="big pdf").files[0]
    assert stored.original_name == "scanned exam.pdf" and stored.file.size < 10 * 1024 * 1024

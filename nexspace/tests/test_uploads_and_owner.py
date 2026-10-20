"""Big uploads are compressed before storage; the owner account comes from OWNER_EMAIL / OWNER_PASSWORD."""
import io
import os
import zipfile

import pytest
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.urls import reverse
from PIL import Image

from apps.accounts.models import User
from apps.core import uploads

pytestmark = pytest.mark.django_db


def noisy_image(w, h):
    return Image.frombytes("RGB", (w, h), os.urandom(w * h * 3))


def big_photo(name="photo.jpg", w=4000, h=3000):
    buf = io.BytesIO()
    noisy_image(w, h).save(buf, "JPEG", quality=98)
    return SimpleUploadedFile(name, buf.getvalue(), content_type="image/jpeg")


def big_pdf(pages=3):
    buf = io.BytesIO()
    images = [noisy_image(3000, 2200) for _ in range(pages)]
    images[0].save(buf, "PDF", save_all=True, append_images=images[1:], quality=97, resolution=150)
    return SimpleUploadedFile("scanned-notes.pdf", buf.getvalue(), content_type="application/pdf")


def big_docx():
    img = io.BytesIO()
    noisy_image(3000, 2000).save(img, "PNG")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("[Content_Types].xml", "<Types/>")
        z.writestr("word/document.xml", "<w:document xmlns:w='x'><w:body/></w:document>")
        z.writestr("word/media/image1.png", img.getvalue())
    return SimpleUploadedFile("lecture.docx", buf.getvalue(),
                              content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document")


def test_big_photo_is_accepted_and_shrunk():
    photo = big_photo()
    assert photo.size > 5 * 1024 * 1024  # used to be rejected (old 5 MB limit)
    result, content_type = uploads.prepare_image(photo)
    assert content_type == "image/jpeg" and result.size < photo.size
    with Image.open(result) as img:
        assert max(img.size) <= 2048


def test_big_pdf_is_compressed_and_still_a_valid_pdf():
    pdf = big_pdf()
    result, content_type = uploads.prepare_document(pdf)
    assert content_type == "application/pdf"
    assert result.size < pdf.size * 0.6, (pdf.size, result.size)
    from pypdf import PdfReader

    result.seek(0)
    assert len(PdfReader(result).pages) == 3


def test_big_word_file_pictures_are_recompressed():
    doc = big_docx()
    result, _ = uploads.prepare_document(doc)
    assert result.size < doc.size
    result.seek(0)
    with zipfile.ZipFile(result) as z:
        assert "word/document.xml" in z.namelist() and "word/media/image1.png" in z.namelist()


def test_small_files_are_left_alone():
    small = SimpleUploadedFile("tiny.pdf", b"%PDF-1.4\n%tiny\n", content_type="application/pdf")
    result, _ = uploads.prepare_document(small)
    assert result is small


def test_too_big_for_storage_even_after_compressing(settings):
    settings.STORAGE_FILE_LIMIT_MB = 1
    settings.STORAGE_FILE_LIMIT_BYTES = 1024 * 1024
    with pytest.raises(ValidationError) as exc:
        uploads.prepare_document(big_pdf(pages=2))
    assert "even after compressing" in exc.value.messages[0]


def test_upload_size_limit_is_100_mb(settings):
    assert settings.MAX_UPLOAD_MB == 100
    huge = SimpleUploadedFile("huge.pdf", b"%PDF" + b"0" * 10, content_type="application/pdf")
    huge.size = 101 * 1024 * 1024
    with pytest.raises(ValidationError):
        uploads.validate_document_upload(huge)


# --- Owner account from Render settings ------------------------------------------------------
OWNER = "arifalotimothy@gmail.com"
OWNER_PW = "Owner-Pass-2026!"


@pytest.fixture
def owner_env(monkeypatch, settings, department):
    monkeypatch.setenv("OWNER_EMAIL", OWNER)
    monkeypatch.setenv("OWNER_PASSWORD", OWNER_PW)
    monkeypatch.setenv("OWNER_FULL_NAME", "Timothy Arifalo")
    settings.PLATFORM_OWNER_EMAILS = [OWNER]


def test_setup_owner_creates_a_ready_admin(owner_env, client):
    call_command("setup_owner")
    user = User.objects.get(email=OWNER)
    assert user.email_verified and user.onboarding_completed and user.is_platform_admin and user.department_id
    r = client.post(reverse("accounts:login"), {"email": OWNER, "password": OWNER_PW})
    assert r.status_code == 302
    assert client.get("/platform/").status_code == 200


def test_setup_owner_repairs_an_existing_account(owner_env, make_user):
    user = make_user(email=OWNER, verified=False)
    user.set_password("something-else-123")
    user.is_active = False
    user.save()
    call_command("setup_owner")
    user.refresh_from_db()
    assert user.check_password(OWNER_PW) and user.is_active and user.email_verified and user.is_platform_admin


def test_setup_owner_strips_everyone_else(owner_env):
    rogue = User.objects.create_superuser(email="arifalotimothy0@gmail.com", password="x-Password-123",
                                          full_name="Zero")
    call_command("setup_owner")
    rogue.refresh_from_db()
    assert not rogue.is_superuser and not rogue.is_platform_admin


def test_setup_owner_does_nothing_without_settings(monkeypatch, capsys):
    monkeypatch.delenv("OWNER_EMAIL", raising=False)
    monkeypatch.delenv("OWNER_PASSWORD", raising=False)
    call_command("setup_owner")
    assert not User.objects.exists()

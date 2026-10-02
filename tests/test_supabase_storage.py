"""Supabase Storage (S3-compatible) — exercised against moto's simulated S3, not mocks."""
from urllib.parse import parse_qs, urlparse

import boto3
import pytest
from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from moto import mock_aws

from apps.academics.models import Course
from apps.accounts.models import RoleAssignment
from apps.accounts.services import assign_role
from apps.core.storage import SupabaseStorage, download_url

pytestmark = pytest.mark.django_db
ENDPOINT = "https://s3.amazonaws.com"  # moto intercepts this; Supabase's endpoint works the same way


@pytest.fixture
def s3(settings):
    with mock_aws():
        settings.SUPABASE_S3_ENDPOINT = ENDPOINT
        settings.SUPABASE_S3_REGION = "us-east-1"
        settings.SUPABASE_S3_ACCESS_KEY_ID = "test"
        settings.SUPABASE_S3_SECRET_ACCESS_KEY = "test"
        settings.SUPABASE_STORAGE_BUCKET = "nexspace"
        boto3.client("s3", region_name="us-east-1", endpoint_url=ENDPOINT,
                     aws_access_key_id="test", aws_secret_access_key="test").create_bucket(Bucket="nexspace")
        yield SupabaseStorage()


def test_save_open_delete_round_trip(s3):
    name = s3.save("resources/notes.pdf", ContentFile(b"%PDF-1.4 hello"))
    assert s3.exists(name)
    with s3.open(name, "rb") as f:
        assert f.read() == b"%PDF-1.4 hello"
    second = s3.save("resources/notes.pdf", ContentFile(b"other"))
    assert second != name  # never overwrites someone else's file
    s3.delete(name)
    assert not s3.exists(name)


def test_links_are_signed_and_expire(s3):
    name = s3.save("resources/notes.pdf", ContentFile(b"x"))
    query = parse_qs(urlparse(s3.url(name)).query)
    assert "X-Amz-Signature" in query and query["X-Amz-Expires"] == ["3600"]


def test_download_link_keeps_the_original_filename(s3, settings):
    from django.db.models.fields.files import FieldFile
    from apps.resources.models import Resource

    name = s3.save("resources/abc123.pdf", ContentFile(b"x"))
    field = FieldFile(None, Resource._meta.get_field("file"), name)
    field.storage = s3
    query = parse_qs(urlparse(download_url(field, 'CSC 301 "Exam".pdf')).query)
    disposition = query["response-content-disposition"][0]
    assert disposition.startswith("attachment;") and 'filename="CSC 301 Exam.pdf"' in disposition


def test_storage_failure_becomes_a_clear_message(settings):
    with mock_aws():  # no bucket created: the upload fails
        settings.SUPABASE_S3_ENDPOINT, settings.SUPABASE_S3_REGION = ENDPOINT, "us-east-1"
        settings.SUPABASE_S3_ACCESS_KEY_ID = settings.SUPABASE_S3_SECRET_ACCESS_KEY = "test"
        settings.SUPABASE_STORAGE_BUCKET = "missing-bucket"
        with pytest.raises(ValidationError) as exc:
            SupabaseStorage().save("x.pdf", ContentFile(b"x"))
        assert "File storage isn't responding" in exc.value.messages[0]


def test_resource_upload_and_download_end_to_end(s3, client, make_user, department, monkeypatch):
    """Upload through NexSpace, then download: the redirect is a signed link with the real filename."""
    from apps.resources import services as resources
    from apps.resources.models import Resource

    monkeypatch.setattr(Resource._meta.get_field("file"), "storage", s3)
    course = Course.objects.create(department=department, code="CSC 301", title="DS", level=300, semester=1)
    rep = make_user(email="rep@example.com")
    assign_role(user=rep, role=RoleAssignment.Role.COURSE_REP, course=course)
    resource = resources.upload(user=rep, course=course, title="Exam 2024", resource_type="past_question",
                                file=SimpleUploadedFile("CSC301 exam.pdf", b"%PDF-1.4 exam"))
    client.force_login(rep)
    r = client.get(reverse("resources:download", args=[resource.pk]))
    assert r.status_code == 302
    query = parse_qs(urlparse(r.url).query)
    assert "X-Amz-Signature" in query and "CSC301 exam.pdf" in query["response-content-disposition"][0]


def test_storage_choice_and_limits():
    from django.conf import settings

    # Tests run with no storage configured: local disk, 10 MB.
    assert settings.STORAGES["default"]["BACKEND"] == "django.core.files.storage.FileSystemStorage"
    assert settings.MAX_DOCUMENT_MB == 10


def test_limit_shown_on_compose_page(client, make_user, settings):
    client.force_login(make_user(email="c@example.com"))
    assert 'data-max-doc-mb="10"' in client.get(reverse("posts:compose")).content.decode()

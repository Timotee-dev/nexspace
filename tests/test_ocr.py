"""OCR of photos and scanned PDF pages with OCR.space (faked here — tests never call the internet)."""
import io
import json
import urllib.error

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from PIL import Image, ImageDraw

from apps.academics.models import Course
from apps.core.management.commands.seed_demo import make_pdf
from apps.nexai import ocr
from apps.nexai.indexing import index_resource
from apps.nexai.models import ResourceChunk
from apps.resources import services as resources
from apps.resources.models import Resource
from apps.search.services import search

pytestmark = pytest.mark.django_db


@pytest.fixture
def course(department):
    return Course.objects.create(department=department, code="CSC 301", title="Data Structures", level=300, semester=1)


@pytest.fixture
def uploader(make_user):
    return make_user(email="up@example.com", full_name="Uploader")


@pytest.fixture
def ocr_on(settings):
    settings.OCR_SPACE_API_KEY = "test-key"
    settings.OCR_DAILY_PAGE_LIMIT = 400
    settings.OCR_TIME_BUDGET = 30
    settings.OCR_SPACE_ENGINE = 2  # most tests check one engine; the auto mode has its own tests below
    return settings


class FakeOcrSpace:
    """Stands in for api.ocr.space and records what was sent."""

    def __init__(self, texts=None, error=None):
        self.texts = list(texts or [])
        self.error = error
        self.sent = []

    def __call__(self, request, timeout=None):
        from urllib.parse import parse_qs

        form = parse_qs(request.data.decode())
        self.sent.append({"headers": dict(request.headers), "form": form,
                          "jpeg_bytes": len(form["base64Image"][0].split(",", 1)[1]) * 3 // 4})
        if isinstance(self.error, Exception):
            raise self.error
        body = self.error if self.error is not None else {
            "ParsedResults": [{"ParsedText": self.texts.pop(0) if self.texts else "", "FileParseExitCode": 1}],
            "OCRExitCode": 1, "IsErroredOnProcessing": False}
        return io.BytesIO(json.dumps(body).encode())


@pytest.fixture
def fake(monkeypatch):
    def install(**kwargs):
        f = FakeOcrSpace(**kwargs)
        monkeypatch.setattr(ocr.urllib.request, "urlopen", f)
        return f
    return install


def photo(name="notes.jpg", size=(3000, 2200), fmt="JPEG"):
    image = Image.new("RGB", size, "white")
    draw = ImageDraw.Draw(image)
    for y in range(100, size[1], 120):
        draw.line([(80, y), (size[0] - 80, y)], fill="black", width=6)
    buffer = io.BytesIO()
    image.save(buffer, fmt, quality=95)
    return SimpleUploadedFile(name, buffer.getvalue(), content_type="image/jpeg")


def scanned_pdf(pages=3):
    images = [Image.new("RGB", (1700, 2200), "white") for _ in range(pages)]
    for image in images:
        ImageDraw.Draw(image).rectangle([200, 200, 1500, 400], fill="black")
    buffer = io.BytesIO()
    images[0].save(buffer, "PDF", save_all=True, append_images=images[1:], resolution=200)
    return SimpleUploadedFile("scan.pdf", buffer.getvalue(), content_type="application/pdf")


def upload(user, course, file, title="Past questions 2023"):
    r = resources.upload(user=user, course=course, title=title, resource_type="past_question", file=file)
    index_resource(r)
    r.refresh_from_db()
    return r


def test_photo_upload_is_accepted_queued_read_and_searchable(ocr_on, fake, uploader, course):
    r = upload(uploader, course, photo())
    assert r.content_type == "image/jpeg" and r.extension == "JPG"
    assert r.ocr_status == "pending" and r.ocr_pages_waiting == [1] and not r.chunks.exists()
    assert "photo or scan" in r.index_error

    api = fake(texts=["Question 1: Explain binary search trees and give two examples of traversal."])
    stats = ocr.process_pending()
    assert stats["pages"] == 1 and stats["files_done"] == 1
    sent = api.sent[0]
    assert sent["headers"]["Apikey"] == "test-key" and sent["form"]["OCREngine"] == ["2"]
    assert sent["jpeg_bytes"] <= ocr.FREE_LIMIT_BYTES  # always under the free 1 MB limit

    r.refresh_from_db()
    assert r.ocr_status == "done" and r.index_error == "" and r.ocr_pages_waiting == []
    assert "binary search trees" in r.chunks.first().text
    # searchable by words inside the photo, in both the library and global search
    assert list(resources.search(Resource.objects.all(), q="traversal")) == [r]
    assert search(uploader, "binary traversal")["resources"] == [r]
    assert ocr.process_pending()["pages"] == 0  # nothing left to do


def test_scanned_pdf_pages_are_sent_one_by_one_under_the_limit(ocr_on, fake, uploader, course):
    r = upload(uploader, course, scanned_pdf(3))
    assert r.ocr_status == "pending" and r.ocr_pages_waiting == [1, 2, 3] and r.ocr_page_count == 3

    api = fake(texts=["Page one: bubble sort compares neighbours", "", "Page three: hashing maps keys to buckets"])
    ocr.process_pending()
    assert len(api.sent) == 3 and all(s["jpeg_bytes"] <= ocr.FREE_LIMIT_BYTES for s in api.sent)
    r.refresh_from_db()
    assert r.ocr_status == "done"
    assert sorted(c.page for c in r.chunks.all()) == [1, 3]  # the blank page added nothing
    assert ocr.pages_used_today() == 3


def test_pdfs_with_real_text_never_use_ocr(ocr_on, fake, uploader, course):
    r = upload(uploader, course, SimpleUploadedFile("notes.pdf", make_pdf([["Merge sort divides the list in half and merges it again carefully."]])))
    assert r.ocr_status == "" and r.chunks.exists()
    api = fake()
    ocr.process_pending()
    assert api.sent == []


def test_daily_page_limit_and_time_budget(ocr_on, fake, uploader, course):
    ocr_on.OCR_DAILY_PAGE_LIMIT = 2
    r = upload(uploader, course, scanned_pdf(3))
    api = fake(texts=["a page of text here", "another page of text", "third"])
    stats = ocr.process_pending()
    assert len(api.sent) == 2 and stats["pages"] == 2
    r.refresh_from_db()
    assert r.ocr_status == "pending" and r.ocr_pages_waiting == [3]  # finishes on a later day
    assert ocr.process_pending()["pages"] == 0

    ocr_on.OCR_DAILY_PAGE_LIMIT = 400
    assert ocr.process_pending(time_budget=0)["pages"] == 0  # no time left this run


def test_outage_pauses_without_losing_pages(ocr_on, fake, uploader, course):
    r = upload(uploader, course, photo())
    fake(error=urllib.error.URLError("down"))
    ocr.process_pending()
    r.refresh_from_db()
    assert r.ocr_status == "pending" and r.ocr_pages_waiting == [1] and "reach" in r.ocr_error
    assert ocr.pages_used_today() == 0  # nothing counted against the free allowance

    fake(texts=["Recovered text from the photo of the exam paper."])
    ocr.process_pending()
    r.refresh_from_db()
    assert r.ocr_status == "done" and r.chunks.exists()


def test_free_plan_limit_message_pauses(ocr_on, fake, uploader, course):
    r = upload(uploader, course, photo())
    fake(error={"IsErroredOnProcessing": True, "ErrorMessage": ["You have reached the maximum number of requests"]})
    ocr.process_pending()
    r.refresh_from_db()
    assert r.ocr_status == "pending" and "maximum" in r.ocr_error


def test_a_page_ocr_cannot_read_is_skipped_not_retried_forever(ocr_on, fake, uploader, course):
    r = upload(uploader, course, scanned_pdf(2))
    fake(error={"IsErroredOnProcessing": True, "ErrorMessage": ["Unable to recognize the file type"]})
    ocr.process_pending()
    r.refresh_from_db()
    assert r.ocr_status == "done" and r.ocr_pages_waiting == []
    assert "No text could be found" in r.index_error


def test_refused_key_and_plain_text_answers(ocr_on, monkeypatch):
    def refuse(request, timeout=None):
        raise urllib.error.HTTPError(ocr.ENDPOINT, 403, "Forbidden", {}, None)

    monkeypatch.setattr(ocr.urllib.request, "urlopen", refuse)
    with pytest.raises(ocr.OcrError, match="refused"):
        ocr.read_jpeg(b"x")
    monkeypatch.setattr(ocr.urllib.request, "urlopen", lambda r, timeout=None: io.BytesIO(b'"Too many requests"'))
    with pytest.raises(ocr.OcrError, match="Too many"):
        ocr.read_jpeg(b"x")


def test_gives_up_after_many_failed_runs(ocr_on, fake, uploader, course):
    r = upload(uploader, course, photo())
    Resource.objects.filter(pk=r.pk).update(ocr_retries=ocr.MAX_RETRIES - 1)
    fake(error=urllib.error.URLError("down"))
    ocr.process_pending()
    r.refresh_from_db()
    assert r.ocr_status == "failed"


def test_without_a_key_nothing_is_sent_and_uploads_still_work(settings, fake, uploader, course):
    settings.OCR_SPACE_API_KEY = ""
    r = upload(uploader, course, photo())
    assert r.ocr_status == "" and "photo or scan" in r.index_error
    api = fake()
    assert ocr.process_pending()["skipped"] == "no key" and api.sent == []


def test_adding_the_key_later_reads_older_photos(settings, fake, uploader, course):
    settings.OCR_SPACE_API_KEY = ""
    r = upload(uploader, course, photo())
    settings.OCR_SPACE_API_KEY = "test-key"
    fake(texts=["Old photo text about linked lists and stacks."])
    call_command("run_scheduled")
    r.refresh_from_db()
    assert r.ocr_status == "done" and "linked lists" in r.chunks.first().text


def test_scheduled_job_survives_ocr_crash(ocr_on, monkeypatch, uploader, course):
    upload(uploader, course, photo())
    monkeypatch.setattr(ocr, "process_pending", lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    call_command("run_scheduled")  # reminders, push and digests still run


def test_upload_page_and_detail_labels(client, ocr_on, fake, uploader, course):
    client.force_login(uploader)
    page = client.get("/resources/upload/").content.decode()
    assert "photo" in page and ".jpg" in page and "read automatically" in page
    r = upload(uploader, course, photo())
    assert "Reading text" in client.get(r.get_absolute_url()).content.decode()
    fake(texts=["Some readable text from the photo of the notes."])
    ocr.process_pending()
    assert "Text found" in client.get(r.get_absolute_url()).content.decode()


def test_fake_images_are_still_rejected(ocr_on, uploader, course):
    from django.core.exceptions import ValidationError

    with pytest.raises(ValidationError):
        resources.upload(user=uploader, course=course, title="x", resource_type="lecture_note",
                         file=SimpleUploadedFile("evil.jpg", b"<script>alert(1)</script>"))


class PerEngine(FakeOcrSpace):
    """Answers differently per engine: Engine 2 misses handwriting, Engine 3 reads it."""

    def __init__(self, by_engine):
        super().__init__()
        self.by_engine = by_engine

    def __call__(self, request, timeout=None):
        from urllib.parse import parse_qs

        engine = parse_qs(request.data.decode())["OCREngine"][0]
        self.sent.append(engine)
        body = {"ParsedResults": [{"ParsedText": self.by_engine[engine], "FileParseExitCode": 1}],
                "OCRExitCode": 1, "IsErroredOnProcessing": False}
        return io.BytesIO(json.dumps(body).encode())


def test_auto_engine_tries_handwriting_engine_only_when_needed(ocr_on, monkeypatch, uploader, course):
    ocr_on.OCR_SPACE_ENGINE = "auto"
    handwriting = "Handwritten: define recursion and give the base case for factorial."
    api = PerEngine({"2": "Hn", "3": handwriting})
    monkeypatch.setattr(ocr.urllib.request, "urlopen", api)
    r = upload(uploader, course, photo())
    ocr.process_pending()
    r.refresh_from_db()
    assert api.sent == ["2", "3"] and handwriting in r.chunks.first().text
    assert ocr.pages_used_today() == 2 and ocr.engine3_left_this_month() == ocr_on.OCR_ENGINE3_MONTHLY_LIMIT - 1

    printed = "Printed: a stack is last in, first out; a queue is first in, first out."
    api2 = PerEngine({"2": printed, "3": "should not be used"})
    monkeypatch.setattr(ocr.urllib.request, "urlopen", api2)
    r2 = upload(uploader, course, photo(), title="Typed notes")
    ocr.process_pending()
    assert api2.sent == ["2"]  # Engine 2 was enough, the small Engine 3 allowance is saved


def test_engine3_allowance_runs_out_gracefully(ocr_on, monkeypatch, uploader, course):
    ocr_on.OCR_SPACE_ENGINE = "auto"
    ocr_on.OCR_ENGINE3_MONTHLY_LIMIT = 0
    api = PerEngine({"2": "", "3": "never"})
    monkeypatch.setattr(ocr.urllib.request, "urlopen", api)
    r = upload(uploader, course, photo())
    ocr.process_pending()
    r.refresh_from_db()
    assert api.sent == ["2"] and r.ocr_status == "done"

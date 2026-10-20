import io
import json
import zipfile

import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.urls import reverse
from django.utils import timezone

from apps.academics.models import Course
from apps.accounts.models import RoleAssignment
from apps.accounts.services import assign_role
from apps.core.management.commands.seed_demo import make_pdf
from apps.discover.services import people_you_may_know, recommended_resources
from apps.nexai import extract, llm, retrieval, services
from apps.nexai.indexing import index_resource
from apps.nexai.models import ResourceChunk, Usage
from apps.nexai.templatetags.nexai import render_answer
from apps.resources import services as resources
from apps.spaces import services as spaces

pytestmark = pytest.mark.django_db

SORTING = [["Merge sort divides the list in half and merges. It runs in O(n log n) and is stable.",
            "Quick sort partitions around a pivot. Average O(n log n), worst case O(n^2)."]]


@pytest.fixture
def course(department):
    return Course.objects.create(department=department, code="CSC 301", title="Data Structures", level=300, semester=1)


@pytest.fixture
def rep(make_user, course):
    u = make_user(email="rep@example.com", full_name="Rep")
    assign_role(user=u, role=RoleAssignment.Role.COURSE_REP, course=course)
    return u


@pytest.fixture
def student(make_user, course):
    u = make_user(email="stu@example.com", full_name="Stu")
    spaces.join(u, course.space)
    return u


def upload(user, course, title="Sorting notes", pages=SORTING, rtype="lecture_note"):
    r = resources.upload(user=user, course=course, title=title, resource_type=rtype,
                         file=SimpleUploadedFile(f"{title}.pdf", make_pdf(pages)))
    index_resource(r)
    return r


@pytest.fixture
def fake_llm(settings, monkeypatch):
    settings.NEXAI_ENABLED = True
    settings.ANTHROPIC_API_KEY = "test-key"
    calls = []

    def complete(*, system, messages, max_tokens=900, temperature=0.2):
        calls.append({"system": system, "messages": messages})
        return calls_reply[0], 120, 40

    calls_reply = ["Merge sort is O(n log n) [1]."]
    monkeypatch.setattr(llm, "complete", complete)
    return calls, calls_reply


# --- Extraction & indexing --------------------------------------------------------
def test_extract_pdf_docx_pptx():
    pages = extract.pages_from_file(io.BytesIO(make_pdf([["Hello page one"], ["Page two text"]])), "a.pdf")
    assert [p for p, _ in pages] == [1, 2] and "Page two" in pages[1][1]

    docx = io.BytesIO()
    with zipfile.ZipFile(docx, "w") as z:
        z.writestr("word/document.xml", '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
                   "<w:body><w:p><w:r><w:t>Stacks are LIFO.</w:t></w:r></w:p></w:body></w:document>")
    assert "Stacks are LIFO." in extract.pages_from_file(docx, "n.docx")[0][1]

    pptx = io.BytesIO()
    with zipfile.ZipFile(pptx, "w") as z:
        for n in (2, 10, 1):
            z.writestr(f"ppt/slides/slide{n}.xml", '<p:sld xmlns:p="x" xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">'
                       f"<a:t>Slide {n}</a:t></p:sld>")
    assert [t for _, t in extract.pages_from_file(pptx, "s.pptx")] == ["Slide 1", "Slide 2", "Slide 10"]
    with pytest.raises(extract.UnsupportedFile):
        extract.pages_from_file(io.BytesIO(b"x"), "old.doc")


def test_chunking_overlaps_and_skips_tiny():
    text = ("Sentence number one is here. " * 80).strip()
    chunks = extract.chunk([(3, text), (4, "tiny")])
    assert len(chunks) > 1 and all(p == 3 for p, _ in chunks)
    assert all(len(t) <= extract.CHUNK_CHARS for _, t in chunks)


def test_upload_is_indexed_after_commit(django_capture_on_commit_callbacks, rep, course):
    with django_capture_on_commit_callbacks(execute=True):
        r = resources.upload(user=rep, course=course, title="Notes", resource_type="lecture_note",
                             file=SimpleUploadedFile("n.pdf", make_pdf(SORTING)))
    r.refresh_from_db()
    assert r.indexed_at and r.chunks.exists() and r.index_error == ""


def test_unreadable_files_record_an_error(rep, course):
    r = resources.upload(user=rep, course=course, title="Scan", resource_type="other",
                         file=SimpleUploadedFile("s.pdf", b"%PDF-1.4 not really a pdf"))
    index_resource(r)
    r.refresh_from_db()
    assert r.indexed_at and r.index_error and not r.chunks.exists()


def test_run_scheduled_indexes_pending(rep, course):
    r = resources.upload(user=rep, course=course, title="Later", resource_type="lecture_note",
                         file=SimpleUploadedFile("l.pdf", make_pdf(SORTING)))
    assert not r.chunks.exists()
    call_command("run_scheduled")
    assert r.chunks.exists()


# --- Retrieval is permission-scoped ------------------------------------------------------
def test_retrieval_only_sees_visible_department_resources(rep, student, course, make_user, other_department):
    mine = upload(rep, course)
    other_course = Course.objects.create(department=other_department, code="CSC 301", title="Elsewhere", level=300, semester=1)
    outsider_rep = make_user(email="orep@example.com", department=other_department)
    upload(outsider_rep, other_course, title="Their merge sort notes")
    hits = retrieval.search(student, "merge sort complexity")
    assert hits and {c.resource_id for c in hits} == {mine.pk}
    mine.is_hidden = True
    mine.save()
    assert retrieval.search(student, "merge sort") == []


# --- Asking ------------------------------------------------------------------------------
def test_search_only_mode_without_api_key(settings, rep, student, course):
    settings.NEXAI_ENABLED = False
    upload(rep, course)
    result = services.ask(user=student, question="How does merge sort work?")
    assert result["search_only"] and result["sources"][0]["label"].startswith("CSC 301 · Sorting notes")
    assert Usage.objects.get().used_model is False


def test_grounded_answer_with_sources_and_calendar(fake_llm, rep, student, course):
    calls, _ = fake_llm
    upload(rep, course)
    from apps.notices import services as notices

    notices.create_event(user=rep, title="CSC 301 test", kind="test", audience="course", target_course=course,
                         starts_at=timezone.now() + timezone.timedelta(days=4))
    result = services.ask(user=student, question="What is the complexity of merge sort?")
    assert result["answer"] == "Merge sort is O(n log n) [1]." and result["sources"][0]["n"] == 1
    prompt = calls[0]["messages"][-1]["content"]
    assert "[1] CSC 301 · Sorting notes" in prompt and "O(n log n)" in prompt and "CSC 301 test" in prompt
    assert "ONLY from the numbered sources" in calls[0]["system"]
    usage = Usage.objects.get()
    assert usage.used_model and usage.input_tokens == 120


def test_no_sources_means_no_guessing(fake_llm, student):
    calls, _ = fake_llm
    result = services.ask(user=student, question="Explain photosynthesis in plants")
    assert calls == [] and "couldn't find" in result["answer"]


def test_daily_limit_and_suspension(fake_llm, settings, rep, student, course):
    upload(rep, course)
    settings.NEXAI_DAILY_LIMIT = 2
    services.ask(user=student, question="merge sort")
    services.ask(user=student, question="quick sort")
    with pytest.raises(PermissionDenied):
        services.ask(user=student, question="merge sort again")
    student.suspended_until = timezone.now() + timezone.timedelta(days=1)
    with pytest.raises(PermissionDenied):
        services.ask(user=student, question="merge sort")


def test_summary_quiz_and_insights(fake_llm, rep, student, course):
    calls, reply = fake_llm
    notes = upload(rep, course)
    upload(rep, course, title="CSC 301 Exam 2023", rtype="past_question",
           pages=[["1. Explain merge sort.", "2. What is a binary search tree?"]])
    reply[0] = "Main topics: merge sort and quick sort [1]."
    assert "merge sort" in services.summarize(user=student, resource=notes)["answer"]
    reply[0] = json.dumps({"questions": [
        {"question": "Merge sort worst case?", "options": ["O(n)", "O(n log n)"], "answer": 1, "explanation": "See [1]"},
        {"question": "bad", "options": ["only one"], "answer": 0, "explanation": ""},
    ]})
    quiz = services.quiz(user=student, resource=notes)
    assert len(quiz["questions"]) == 1 and quiz["questions"][0]["answer"] == 1
    reply[0] = "not json"
    with pytest.raises(ValidationError):
        services.quiz(user=student, resource=notes)
    reply[0] = "Sorting appears most [1]."
    result = services.insights(user=student, course=course)
    assert "Exam 2023" in calls[-1]["messages"][0]["content"] and "Sorting notes" not in calls[-1]["messages"][0]["content"]
    assert result["sources"]


def test_views(client, fake_llm, rep, student, course, make_user, other_department):
    notes = upload(rep, course)
    client.force_login(student)
    r = client.post(reverse("nexai:ask"), {"question": "merge sort?", "course": course.slug})
    assert r.status_code == 302
    page = client.get(reverse("nexai:ask") + f"?course={course.slug}").content.decode()
    assert "merge sort?" in page and 'href="#t1-1"' in page and 'id="t1-1"' in page
    assert client.post(reverse("nexai:summary", args=[notes.pk])).status_code == 200
    assert client.post(reverse("nexai:ask"), {"clear": "1"}).status_code == 302
    assert "merge sort?" not in client.get(reverse("nexai:ask")).content.decode()
    client.force_login(make_user(email="far@example.com", department=other_department))
    assert client.post(reverse("nexai:summary", args=[notes.pk])).status_code == 404


def test_answer_rendering_is_safe():
    html = render_answer('<script>x</script> **Key** idea [2]\n- first\n- second', "t1")
    assert "<script>" not in html and "&lt;script&gt;" in html
    assert "<strong>Key</strong>" in html and 'href="#t1-2"' in html and "<ul><li>first</li><li>second</li></ul>" in html


# --- Recommendations ---------------------------------------------------------------------
def test_recommendations(rep, student, course, make_user):
    a = upload(rep, course, title="A notes")
    b = upload(rep, course, title="B notes")
    resources.record_download(user=student, resource=a)
    assert recommended_resources(student) == [b]
    classmate = make_user(email="mate@example.com")
    spaces.join(classmate, course.space)
    stranger = make_user(email="str@example.com")
    people = people_you_may_know(student)
    assert classmate in people and stranger not in people

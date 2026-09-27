from datetime import timedelta

import pytest
from django.core.cache import cache
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from django.utils import timezone

from apps.academics.models import AcademicSession, Course
from apps.accounts.models import RoleAssignment
from apps.accounts.services import assign_role
from apps.notices import services as notices
from apps.notices.models import AcademicEvent, Announcement, Audience
from apps.posts import feed
from apps.posts import services as posts
from apps.resources import services as resources
from apps.resources.models import Resource
from apps.spaces import services as spaces
from apps.spaces.models import Space, SpaceMembership

pytestmark = pytest.mark.django_db
PDF = b"%PDF-1.4 demo"


def pdf(name="notes.pdf"):
    return SimpleUploadedFile(name, PDF, content_type="application/pdf")


def score(user):
    user.profile.refresh_from_db()
    return user.profile.nexscore


@pytest.fixture
def course(department):
    return Course.objects.create(department=department, code="csc  301", title="Data Structures", level=300, semester=1)


@pytest.fixture
def other_course(department):
    return Course.objects.create(department=department, code="CSC 305", title="Operating Systems", level=300, semester=1)


@pytest.fixture
def student(make_user):
    return make_user(email="stu@example.com", full_name="Stu Dent")


@pytest.fixture
def rep(make_user, course):
    user = make_user(email="rep@example.com", full_name="Rep Resentative")
    assign_role(user=user, role=RoleAssignment.Role.COURSE_REP, course=course)
    return user


@pytest.fixture
def dept_admin(make_user, department):
    user = make_user(email="head@example.com", full_name="Head Admin")
    assign_role(user=user, role=RoleAssignment.Role.DEPARTMENT_ADMIN, department=department)
    return user


# --- Courses & Spaces ----------------------------------------------------------
def test_course_gets_official_space(course):
    assert course.code == "CSC 301" and course.slug == "csc-301"
    assert course.space.is_official and course.space.kind == Space.Kind.COURSE


def test_course_rep_scope(rep, course, other_course, dept_admin):
    assert rep.has_role(RoleAssignment.Role.COURSE_REP, course=course)
    assert not rep.has_role(RoleAssignment.Role.COURSE_REP, course=other_course)
    assert dept_admin.has_role(RoleAssignment.Role.COURSE_REP, course=other_course)
    membership = SpaceMembership.objects.get(space=course.space, user=rep)
    assert membership.role == SpaceMembership.Role.MODERATOR


def test_join_leave_mute_and_feeds(student, make_user, course):
    space = course.space
    author = make_user(email="w@example.com")
    spaces.join(author, space)
    post = posts.create_post(author=author, kind="post", body="in the course space", space=space)
    assert feed.following(student)[0] == []
    assert spaces.join(student, space) is True and spaces.join(student, space) is False
    space.refresh_from_db()
    assert space.member_count == 2
    assert feed.following(student)[0] == [post]
    spaces.set_muted(student, space, True)
    assert post not in feed.for_you(student)[0] and feed.following(student)[0] == []
    spaces.leave(student, space)
    space.refresh_from_db()
    assert space.member_count == 1


def test_posting_in_space_requires_membership(student, course):
    with pytest.raises(PermissionDenied):
        posts.create_post(author=student, kind="post", body="x", space=course.space)


def test_other_department_cannot_join(make_user, other_department, course):
    outsider = make_user(email="o@example.com", department=other_department)
    with pytest.raises(PermissionDenied):
        spaces.join(outsider, course.space)


def test_create_community_space(client, student, make_user):
    space = spaces.create_space(user=student, name="Robotics Club", icon="🤖")
    assert space.slug == "robotics-club" and space.member_count == 1
    assert SpaceMembership.objects.get(space=space, user=student).role == "moderator"
    with pytest.raises(PermissionDenied):
        spaces.create_space(user=make_user(email="u@example.com", verified=False), name="Nope")
    client.force_login(student)
    assert client.get(reverse("spaces:detail", args=[space.slug])).status_code == 200


def test_course_pages_render_every_tab(client, student, course):
    client.force_login(student)
    for tab in ("discussions", "resources", "past-questions", "questions", "people", "upcoming", "about"):
        assert client.get(course.get_absolute_url() + f"?tab={tab}").status_code == 200, tab
    assert client.get(reverse("spaces:courses")).status_code == 200


def test_onboarding_joins_chosen_spaces(client, make_user, course):
    user = make_user(email="n@example.com", onboarded=False)
    client.force_login(user)
    page = client.get(reverse("accounts:onboarding") + "?step=spaces").content.decode()
    assert course.space.name in page
    client.post(reverse("accounts:onboarding") + "?step=spaces", {"spaces": [course.space.pk]})
    user.refresh_from_db()
    assert user.onboarding_completed and SpaceMembership.objects.filter(user=user, space=course.space).exists()


# --- Resources -------------------------------------------------------------------
def test_upload_publishes_immediately_with_badges(rep, student, course):
    official = resources.upload(user=rep, course=course, title="Exam 2023", file=pdf(), resource_type="past_question", exam_type="exam")
    shared = resources.upload(user=student, course=course, title="My notes", file=pdf(), resource_type="lecture_note")
    assert official.is_verified_upload and not shared.is_verified_upload
    assert set(Resource.objects.for_viewer(student)) == {official, shared}
    assert shared.exam_type == ""


def test_upload_rejects_disguised_files(student, course):
    with pytest.raises(ValidationError):
        resources.upload(user=student, course=course, title="x", file=SimpleUploadedFile("x.pdf", b"MZ exe"),
                         resource_type="other")


def test_resource_visibility_by_department(client, make_user, other_department, rep, course):
    r = resources.upload(user=rep, course=course, title="Exam", file=pdf(), resource_type="past_question")
    outsider = make_user(email="o@example.com", department=other_department)
    client.force_login(outsider)
    assert client.get(r.get_absolute_url()).status_code == 404
    assert client.get(reverse("resources:download", args=[r.pk])).status_code == 404


def test_downloads_counted_once_per_day(client, rep, student, course):
    r = resources.upload(user=rep, course=course, title="Exam", file=pdf(), resource_type="past_question")
    client.force_login(student)
    for _ in range(3):
        assert client.get(reverse("resources:download", args=[r.pk])).status_code == 200
    r.refresh_from_db()
    assert r.download_count == 1 and score(rep) == 1


def test_ratings(rep, student, make_user, course):
    r = resources.upload(user=rep, course=course, title="Exam", file=pdf(), resource_type="past_question")
    with pytest.raises(PermissionDenied):
        resources.rate(user=rep, resource=r, stars=5)
    resources.rate(user=student, resource=r, stars=5, review="Great")
    assert score(rep) == 5
    resources.rate(user=student, resource=r, stars=2)  # update, not a second rating
    r.refresh_from_db()
    assert (r.rating_count, r.rating_sum) == (1, 2) and score(rep) == 0
    resources.rate(user=make_user(email="x@example.com"), resource=r, stars=4)
    r.refresh_from_db()
    assert r.rating_avg == 3.0
    with pytest.raises(ValidationError):
        resources.rate(user=student, resource=r, stars=9)


def test_search_filters_and_sorting(rep, student, course, other_course, university):
    session = AcademicSession.objects.create(university=university, name="2023/2024")
    a = resources.upload(user=rep, course=course, title="Lecture 4: Sorting algorithms", file=pdf(),
                         resource_type="lecture_note")
    b = resources.upload(user=rep, course=course, title="CSC 301 Exam", file=pdf(), resource_type="past_question",
                         session=session)
    c = resources.upload(user=student, course=other_course, title="Scheduling notes", file=pdf(), resource_type="lecture_note")
    qs = Resource.objects.for_viewer(student)
    assert list(resources.search(qs, q="CSC 301 sorting")) == [a]
    assert list(resources.search(qs, q="csc301")) and c not in resources.search(qs, q="csc301")
    assert list(resources.search(qs, resource_type="past_question")) == [b]
    assert list(resources.search(qs, session=session.pk)) == [b]
    Resource.objects.filter(pk=c.pk).update(download_count=50)
    assert list(resources.search(qs, sort="downloads"))[0] == c


def test_remove_resource_permissions(rep, student, course, make_user):
    r = resources.upload(user=student, course=course, title="Notes", file=pdf(), resource_type="lecture_note")
    with pytest.raises(PermissionDenied):
        resources.remove(user=make_user(email="z@example.com"), resource=r)
    resources.remove(user=rep, resource=r)  # course rep manages course resources
    assert not Resource.objects.visible().filter(pk=r.pk).exists()


def test_library_page(client, student, rep, course):
    resources.upload(user=rep, course=course, title="Exam", file=pdf(), resource_type="past_question")
    client.force_login(student)
    r = client.get(reverse("resources:library") + "?q=exam&type=past_question&sort=rated")
    assert r.status_code == 200 and "Exam" in r.content.decode()
    r = client.post(reverse("resources:upload"), {"course": course.pk, "title": "Via form", "resource_type": "slides",
                                                   "file": pdf("slides.pdf")})
    assert r.status_code == 302


# --- Announcements & calendar -------------------------------------------------------
def test_announcement_targeting(dept_admin, make_user, course, rep):
    l300 = make_user(email="a@example.com", level=300)
    l100 = make_user(email="b@example.com", level=100)
    notices.publish_announcement(user=dept_admin, title="All", body="x")
    notices.publish_announcement(user=dept_admin, title="L300", body="x", audience=Audience.LEVEL, target_level=300)
    notices.publish_announcement(user=rep, title="Course", body="x", audience=Audience.COURSE, target_course=course)
    titles = lambda u: {a.title for a in notices.active_announcements_for(u)}  # noqa: E731
    assert titles(l100) == {"All"}
    assert titles(l300) == {"All", "L300"}
    spaces.join(l100, course.space)
    assert titles(l100) == {"All", "Course"}


def test_announcements_expire_to_archive(client, dept_admin, student):
    a = notices.publish_announcement(user=dept_admin, title="Soon gone", body="x",
                                     expires_at=timezone.now() + timedelta(hours=1))
    Announcement.objects.filter(pk=a.pk).update(expires_at=timezone.now() - timedelta(minutes=1))
    client.force_login(student)
    assert "Soon gone" not in client.get(reverse("notices:announcements")).content.decode()
    assert "Soon gone" in client.get(reverse("notices:announcements") + "?view=archive").content.decode()


def test_publishing_permissions(client, rep, student, course, other_course):
    with pytest.raises(PermissionDenied):
        notices.publish_announcement(user=rep, title="x", body="x")  # department-wide
    with pytest.raises(PermissionDenied):
        notices.publish_announcement(user=rep, title="x", body="x", audience=Audience.COURSE, target_course=other_course)
    with pytest.raises(PermissionDenied):
        notices.publish_announcement(user=student, title="x", body="x", audience=Audience.COURSE, target_course=course)
    client.force_login(student)
    assert client.get(reverse("notices:announcement-create")).status_code == 403
    client.force_login(rep)
    assert client.get(reverse("notices:announcement-create")).status_code == 200


def test_countdowns_are_relevant_only(dept_admin, rep, student, course):
    notices.create_event(user=rep, title="Test", kind=AcademicEvent.Kind.TEST, audience=Audience.COURSE,
                         target_course=course, starts_at=timezone.now() + timedelta(days=12))
    notices.create_event(user=dept_admin, title="L100 thing", kind=AcademicEvent.Kind.EXAM, audience=Audience.LEVEL,
                         target_level=100, starts_at=timezone.now() + timedelta(days=3))
    assert notices.countdowns_for(student) == []
    spaces.join(student, course.space)
    [event] = notices.countdowns_for(student)
    assert event.title == "Test" and event.days_left == 12


def test_calendar_and_home_render(client, dept_admin, student, course):
    notices.create_event(user=dept_admin, title="Resumption day", kind="resumption",
                         starts_at=timezone.now() + timedelta(days=2))
    client.force_login(student)
    assert "Resumption day" in client.get(reverse("notices:calendar")).content.decode()
    assert client.get(reverse("core:home") + "?tab=department").status_code == 200

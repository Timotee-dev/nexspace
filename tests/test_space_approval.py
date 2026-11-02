import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from django.urls import reverse
from rest_framework.test import APIClient

from apps.academics.models import Course
from apps.accounts.models import RoleAssignment
from apps.accounts.services import assign_role
from apps.notifications.models import Notification
from apps.posts import feed
from apps.posts import services as posts
from apps.posts.models import Post
from apps.search import services as search
from apps.spaces import services as spaces
from apps.spaces.models import Space, SpaceJoinRequest, SpaceMembership

pytestmark = pytest.mark.django_db


@pytest.fixture
def run_hooks(django_capture_on_commit_callbacks):
    def _run(fn, *args, **kwargs):
        with django_capture_on_commit_callbacks(execute=True):
            return fn(*args, **kwargs)
    return _run


@pytest.fixture
def course(department):
    return Course.objects.create(department=department, code="CSC 301", title="Data Structures", level=300, semester=1)


@pytest.fixture
def rep(make_user, course):
    u = make_user(email="rep@example.com", full_name="Rep Resentative")
    assign_role(user=u, role=RoleAssignment.Role.COURSE_REP, course=course)
    return u


@pytest.fixture
def student(make_user):
    return make_user(email="stu@example.com", full_name="Stu Dent")


@pytest.fixture
def club(rep):
    return spaces.create_space(user=rep, name="Robotics Club", requires_approval=True)


def test_who_can_create(make_user, department, rep, student):
    mod = make_user(email="mod@example.com")
    assign_role(user=mod, role=RoleAssignment.Role.MODERATOR, department=department)
    admin = make_user(email="adm@example.com")
    assign_role(user=admin, role=RoleAssignment.Role.DEPARTMENT_ADMIN, department=department)
    assert spaces.can_create_space(rep) and spaces.can_create_space(mod) and spaces.can_create_space(admin)
    assert spaces.can_create_space(student)  # Spaces are clubs and activities: any verified student
    assert not spaces.can_create_space(make_user(email="unverified@example.com", verified=False))


def test_own_level_students_join_their_courses_directly(course, student):
    assert course.space.requires_approval and student.level == 300 == course.level
    assert spaces.request_to_join(student, course.space) == "joined"


def test_other_levels_need_their_course_reps_or_an_admin(run_hooks, client, course, rep, make_user, department):
    """A 200 Level student asking to join a 300 Level course: the 300 Level reps, the course's lecturer
    and the admins are notified; only they can approve (a 100 Level rep can't)."""
    carryover = make_user(email="carry@example.com", level=200, full_name="Carry Over")
    rep100 = make_user(email="rep100@example.com", level=100)
    assign_role(user=rep100, role=RoleAssignment.Role.COURSE_REP, department=department, level=100)
    lecturer = make_user(email="lect@example.com")
    assign_role(user=lecturer, role=RoleAssignment.Role.LECTURER, course=course)
    admin = make_user(email="adm@example.com")
    assign_role(user=admin, role=RoleAssignment.Role.DEPARTMENT_ADMIN, department=department)
    assert run_hooks(spaces.request_to_join, carryover, course.space) == "requested"
    notified = set(Notification.objects.filter(kind="space_request").values_list("recipient__email", flat=True))
    assert notified == {"rep@example.com", "lect@example.com", "adm@example.com"}  # not the 100 Level rep
    note = Notification.objects.filter(kind="space_request").first()
    assert "(200 Level)" in note.text and note.url.endswith("?tab=requests")
    join_request = SpaceJoinRequest.objects.get(user=carryover)
    with pytest.raises(PermissionDenied):
        spaces.decide_request(manager=rep100, join_request=join_request, approve=True)
    client.force_login(rep)  # the 300 Level rep sees and approves it
    page = client.get(course.get_absolute_url() + "?tab=requests").content.decode()
    assert "Carry Over" in page
    spaces.decide_request(manager=rep, join_request=join_request, approve=True)
    assert spaces.is_member(carryover, course.space)


def test_admin_approves_any_level_and_non_members_still_see_materials(client, course, make_user, department):
    from django.core.files.uploadedfile import SimpleUploadedFile

    from apps.core.management.commands.seed_demo import make_pdf
    from apps.resources import services as resources

    outsider = make_user(email="out@example.com", level=100)
    admin = make_user(email="adm@example.com")
    assign_role(user=admin, role=RoleAssignment.Role.DEPARTMENT_ADMIN, department=department)
    resources.upload(user=admin, course=course, title="Past exam 2024", resource_type="past_question",
                     file=SimpleUploadedFile("e.pdf", make_pdf([["Question one about trees and graphs."]])))
    client.force_login(outsider)
    page = client.get(course.get_absolute_url()).content.decode()
    assert "Past exam 2024" in page and "Request to join" in page  # materials open; discussions need joining
    assert client.get(course.get_absolute_url() + "?tab=discussions").status_code == 200
    spaces.request_to_join(outsider, course.space)
    spaces.decide_request(manager=admin, join_request=SpaceJoinRequest.objects.get(user=outsider), approve=True)
    assert spaces.is_member(outsider, course.space)


def test_request_approve_flow(run_hooks, club, rep, student, client):
    with pytest.raises(PermissionDenied):
        spaces.join(student, club)  # no direct joining
    assert run_hooks(spaces.request_to_join, student, club, "I build robots") == "requested"
    assert spaces.request_to_join(student, club) == "pending"
    assert not spaces.is_member(student, club)
    assert Notification.objects.filter(recipient=rep, kind="space_request").exists()

    jr = SpaceJoinRequest.objects.get(space=club, user=student)
    with pytest.raises(PermissionDenied):
        spaces.decide_request(manager=student, join_request=jr, approve=True)
    run_hooks(spaces.decide_request, manager=rep, join_request=jr, approve=True)
    assert spaces.is_member(student, club)
    club.refresh_from_db()
    assert club.member_count == 2
    assert Notification.objects.filter(recipient=student, text__contains="approved").exists()
    with pytest.raises(ValidationError):
        spaces.decide_request(manager=rep, join_request=jr, approve=True)


def test_decline_and_ask_again(run_hooks, club, rep, student):
    spaces.request_to_join(student, club)
    jr = SpaceJoinRequest.objects.get(space=club, user=student)
    run_hooks(spaces.decide_request, manager=rep, join_request=jr, approve=False)
    assert not spaces.is_member(student, club)
    assert Notification.objects.filter(recipient=student, text__contains="wasn't approved").exists()
    assert spaces.request_to_join(student, club) == "requested"
    assert spaces.cancel_request(student, club) is True


def test_private_space_posts_hidden_from_outsiders(club, rep, student, client):
    post = posts.create_post(author=rep, kind="post", body="members-only plans", space=club)
    assert post not in feed.for_you(student)[0]
    assert "posts" not in search.search(student, "members-only plans")
    client.force_login(student)
    assert client.get(post.get_absolute_url()).status_code == 404
    page = client.get(reverse("spaces:detail", args=[club.slug])).content.decode()
    assert "This Space is private" in page and "members-only plans" not in page
    api = APIClient()
    api.force_authenticate(student)
    assert api.get(reverse("api-post", args=[post.pk])).status_code == 404
    # once approved, everything opens up
    spaces.request_to_join(student, club)
    spaces.decide_request(manager=rep, join_request=SpaceJoinRequest.objects.get(user=student), approve=True)
    assert client.get(post.get_absolute_url()).status_code == 200
    assert post in feed.following(student)[0]


def test_mentions_do_not_leak_into_private_spaces(run_hooks, club, rep, student):
    run_hooks(posts.create_post, author=rep, kind="post", body=f"hey @{student.username}", space=club)
    assert not Notification.objects.filter(recipient=student, kind="mention").exists()


def test_views_approve_decline_and_remove(client, club, rep, student, make_user):
    client.force_login(student)
    client.post(reverse("spaces:membership", args=[club.pk]), {"action": "join"})
    jr = SpaceJoinRequest.objects.get(user=student)
    client.force_login(rep)
    page = client.get(reverse("spaces:detail", args=[club.slug]) + "?tab=requests").content.decode()
    assert "Stu Dent" in page and "Approve" in page
    client.post(reverse("spaces:request-decision", args=[club.pk, jr.pk]), {"decision": "approve"})
    assert spaces.is_member(student, club)
    client.post(reverse("spaces:remove-member", args=[club.pk, student.pk]))
    assert not spaces.is_member(student, club)
    outsider = make_user(email="x@example.com")
    client.force_login(outsider)
    r = client.post(reverse("spaces:request-decision", args=[club.pk, jr.pk]), {"decision": "approve"})
    assert r.status_code == 302 and not spaces.is_member(outsider, club)


def test_open_toggle_when_creating(client, rep, student):
    client.force_login(rep)
    client.post(reverse("spaces:create"), {"name": "General Chat", "category": "social"})
    space = Space.objects.get(slug="general-chat")
    assert not space.requires_approval and space.category == "social"
    assert spaces.request_to_join(student, space) == "joined"
    client.post(reverse("spaces:create"), {"name": "Chess Club", "category": "games", "private": "on"})
    assert Space.objects.get(slug="chess-club").requires_approval


def test_onboarding_sends_requests(client, make_user, club, course):
    user = make_user(email="new@example.com", onboarded=False)
    client.force_login(user)
    client.post(reverse("accounts:onboarding") + "?step=spaces", {"spaces": [club.pk, course.space.pk]})
    assert SpaceMembership.objects.filter(user=user, space=course.space).exists()
    assert SpaceJoinRequest.objects.filter(user=user, space=club, status="pending").exists()
    assert not SpaceMembership.objects.filter(user=user, space=club).exists()

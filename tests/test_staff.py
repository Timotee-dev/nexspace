import pytest
from django.core.exceptions import PermissionDenied
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from apps.academics.models import Course
from apps.accounts.models import RoleAssignment, StaffProfile, User
from apps.accounts.services import assign_role, register_staff, verify_staff
from apps.notices import services as notices
from apps.notices.models import Audience
from apps.notifications.models import Notification
from apps.posts import services as posts
from apps.resources import services as resources
from apps.spaces import services as spaces
from apps.spaces.models import Space

from .conftest import PASSWORD

pytestmark = pytest.mark.django_db

@pytest.fixture(autouse=True)
def _department_admin_area(settings):
    """These tests cover department admins using /manage/ (ADMIN_AREA=department).
    The default owner-only admin area is tested in test_admin_sign_in.py."""
    settings.ADMIN_AREA = "department"

R = RoleAssignment.Role


@pytest.fixture(autouse=True)
def _department_verification(settings):
    """These tests cover HOD-verified staff; platform-only verification is tested in test_platform.py."""
    settings.STAFF_VERIFICATION = "department"


@pytest.fixture
def run_hooks(django_capture_on_commit_callbacks):
    def _run(fn, *args, **kwargs):
        with django_capture_on_commit_callbacks(execute=True):
            return fn(*args, **kwargs)
    return _run


@pytest.fixture
def courses(department):
    return [Course.objects.create(department=department, code=f"CSC 30{i}", title=f"Course {i}", level=300, semester=1)
            for i in (1, 5)]


@pytest.fixture
def hod(make_user, department):
    u = make_user(email="hod@example.com", full_name="Head Person", level=None)
    assign_role(user=u, role=R.DEPARTMENT_ADMIN, department=department)
    return u


def staff_signup(client, department, **extra):
    data = {"account_type": "staff", "full_name": "Funmi Adebayo", "email": "funmi@example.com",
            "department": department.pk, "password": PASSWORD, "confirm_password": PASSWORD,
            "position": "lecturer", "title": "dr"}
    data.update(extra)
    return client.post(reverse("accounts:signup"), data)


def test_staff_signup_is_pending_with_no_powers(client, run_hooks, department, courses, hod):
    with_hooks = run_hooks(staff_signup, client, department, courses=[courses[0].pk])
    assert with_hooks.status_code == 302
    user = User.objects.get(email="funmi@example.com")
    assert user.staff.status == StaffProfile.Status.PENDING and user.level is None
    assert not user.is_verified_staff and user.staff_badge == ""
    assert not user.role_assignments.exists()
    assert not user.has_role(R.COURSE_REP, course=courses[0])
    assert Notification.objects.filter(recipient=hod, kind="staff").exists()


def test_staff_signup_validation(client, department, courses):
    r = staff_signup(client, department, position="level_adviser")
    assert "Choose the level you advise" in r.content.decode()
    r = staff_signup(client, department, position="")
    assert "Choose your position" in r.content.decode()
    assert not User.objects.exists()


def test_student_signup_still_needs_a_level(client, department):
    r = client.post(reverse("accounts:signup"), {"full_name": "Stu", "email": "s@example.com", "department": department.pk,
                                                 "password": PASSWORD, "confirm_password": PASSWORD})
    assert r.status_code == 200 and "Choose your level" in r.content.decode()


def test_verification_grants_position_powers(run_hooks, department, courses, hod):
    lecturer = register_staff(email="l@example.com", password=PASSWORD, full_name="Lec Turer", department=department,
                              position="lecturer", title="dr", courses=courses[:1])
    run_hooks(verify_staff, admin=hod, staff=lecturer.staff_profile, approve=True)
    lecturer.refresh_from_db()
    assert lecturer.is_verified_staff and lecturer.staff_badge == "Lecturer" and lecturer.display_name == "Dr. Lec Turer"
    assert lecturer.has_role(R.COURSE_REP, course=courses[0]) and not lecturer.has_role(R.COURSE_REP, course=courses[1])
    assert lecturer.email_verified
    assert Notification.objects.filter(recipient=lecturer, kind="staff", text__contains="verified").exists()
    assert spaces.can_manage(lecturer, courses[0].space)

    adviser = register_staff(email="a@example.com", password=PASSWORD, full_name="Ad Viser", department=department,
                             position="level_adviser", level=300)
    verify_staff(admin=hod, staff=adviser.staff_profile, approve=True)
    assert adviser.has_role(R.LEVEL_ADVISER, department=department, level=300)
    assert not adviser.has_role(R.LEVEL_ADVISER, department=department, level=100)

    new_hod = register_staff(email="h2@example.com", password=PASSWORD, full_name="New Head", department=department,
                             position="hod", title="prof")
    with pytest.raises(PermissionDenied):
        verify_staff(admin=hod, staff=new_hod.staff_profile, approve=True)  # only the platform owner verifies HODs
    owner = User.objects.create_user(email="arifalotimothy@gmail.com", password=PASSWORD, full_name="Owner",
                                      department=department, email_verified=True)
    from apps.accounts.owner import ensure_owner

    ensure_owner(owner)
    verify_staff(admin=owner, staff=new_hod.staff_profile, approve=True)
    assert new_hod.can_moderate(department) and new_hod.has_role(R.DEPARTMENT_ADMIN, department=department)


def test_only_admins_verify_and_nobody_verifies_themselves(make_user, department, hod):
    pending = register_staff(email="p@example.com", password=PASSWORD, full_name="P", department=department,
                             position="exam_officer")
    with pytest.raises(PermissionDenied):
        verify_staff(admin=make_user(email="s@example.com"), staff=pending.staff_profile, approve=True)
    self_hod = register_staff(email="me@example.com", password=PASSWORD, full_name="Me", department=department,
                              position="hod")
    assign_role(user=self_hod, role=R.DEPARTMENT_ADMIN, department=department)
    with pytest.raises(PermissionDenied):
        verify_staff(admin=self_hod, staff=self_hod.staff_profile, approve=True)
    verify_staff(admin=hod, staff=pending.staff_profile, approve=False, note="Not on the staff list")
    pending.refresh_from_db()
    assert pending.staff.status == "rejected" and not pending.role_assignments.exists()


def test_publishing_scopes(department, courses, hod, make_user):
    lecturer = register_staff(email="l@example.com", password=PASSWORD, full_name="L", department=department,
                              position="lecturer", courses=courses[:1])
    adviser = register_staff(email="a@example.com", password=PASSWORD, full_name="A", department=department,
                             position="level_adviser", level=300)
    exams = register_staff(email="e@example.com", password=PASSWORD, full_name="E", department=department,
                           position="exam_officer")
    for u in (lecturer, adviser, exams):
        verify_staff(admin=hod, staff=u.staff_profile, approve=True)
    # lecturer: own course only
    notices.publish_announcement(user=lecturer, title="t", body="b", audience=Audience.COURSE, target_course=courses[0])
    with pytest.raises(PermissionDenied):
        notices.publish_announcement(user=lecturer, title="t", body="b", audience=Audience.COURSE, target_course=courses[1])
    with pytest.raises(PermissionDenied):
        notices.publish_announcement(user=lecturer, title="t", body="b")
    # level adviser: own level only
    notices.publish_announcement(user=adviser, title="t", body="b", audience=Audience.LEVEL, target_level=300)
    with pytest.raises(PermissionDenied):
        notices.publish_announcement(user=adviser, title="t", body="b", audience=Audience.LEVEL, target_level=100)
    # exam officer: department-wide dates and any course
    from django.utils import timezone

    notices.create_event(user=exams, title="Exam", kind="exam", audience=Audience.COURSE, target_course=courses[1],
                         starts_at=timezone.now() + timezone.timedelta(days=3))
    notices.create_event(user=exams, title="Exams begin", kind="event", starts_at=timezone.now() + timezone.timedelta(days=9))
    assert notices.is_publisher(lecturer) and notices.is_publisher(adviser) and notices.is_publisher(exams)
    assert not notices.is_publisher(make_user(email="stu@example.com"))


def test_lecturer_uploads_show_their_badge(client, department, courses, hod, make_user):
    lecturer = register_staff(email="l@example.com", password=PASSWORD, full_name="L", department=department,
                              position="lecturer", title="dr", courses=courses[:1])
    verify_staff(admin=hod, staff=lecturer.staff_profile, approve=True)
    r = resources.upload(user=lecturer, course=courses[0], title="Notes", resource_type="lecture_note",
                         file=SimpleUploadedFile("n.pdf", b"%PDF-1.4"))
    assert r.is_verified_upload
    post = posts.create_post(author=lecturer, kind="post", body="Welcome to the course")
    client.force_login(make_user(email="s@example.com"))
    assert '<span class="tag tag-official">Lecturer</span>' in client.get(r.get_absolute_url()).content.decode()
    html = client.get(post.get_absolute_url()).content.decode()
    assert "Dr. L" in html and '<span class="tag tag-staff">Lecturer</span>' in html


def test_dashboards_by_role(client, department, courses, hod, make_user):
    lecturer = register_staff(email="l@example.com", password=PASSWORD, full_name="L", department=department,
                              position="lecturer", courses=courses)
    adviser = register_staff(email="a@example.com", password=PASSWORD, full_name="A", department=department,
                             position="level_adviser", level=300)
    exams = register_staff(email="e@example.com", password=PASSWORD, full_name="E", department=department,
                           position="exam_officer")
    pending = register_staff(email="p@example.com", password=PASSWORD, full_name="Pending Person",
                             department=department, position="lecturer", courses=courses[:1])
    for u in (lecturer, adviser, exams):
        verify_staff(admin=hod, staff=u.staff_profile, approve=True)
    for u in (hod, lecturer, adviser, exams, pending):
        User.objects.filter(pk=u.pk).update(onboarding_completed=True, email_verified=True)

    def page(user):
        client.force_login(user)
        r = client.get(reverse("manage:dashboard"))
        assert r.status_code == 200
        return r.content.decode()

    hod_page = page(hod)
    assert "Staff to verify" in hod_page and "Pending Person" in hod_page and "Exams and tests" in hod_page
    lec_page = page(lecturer)
    assert "Your courses" in lec_page and "CSC 301" in lec_page and "Upload material" in lec_page
    assert "Staff to verify" not in lec_page
    adv_page = page(adviser)
    assert "300 Level (you advise)" in adv_page and "Your courses" not in adv_page
    ex_page = page(exams)
    assert "Courses with no exam date yet" in ex_page
    assert "waiting for verification" in page(pending)
    client.force_login(make_user(email="s@example.com"))
    assert client.get(reverse("manage:dashboard")).status_code == 302  # students have no dashboard


def test_staff_land_on_dashboard_after_login(client, department, hod):
    User.objects.filter(pk=hod.pk).update(onboarding_completed=True)
    r = client.post(reverse("accounts:login"), {"email": hod.email, "password": PASSWORD})
    assert r.url == reverse("manage:dashboard")


def test_staff_page_verify_flow(client, department, courses, hod):
    pending = register_staff(email="p@example.com", password=PASSWORD, full_name="Pending Person",
                             department=department, position="lecturer", courses=courses[:1])
    client.force_login(hod)
    assert "Pending Person" in client.get(reverse("manage:staff")).content.decode()
    client.post(reverse("manage:staff-decision", args=[pending.staff_profile.pk]), {"decision": "approve"})
    pending.refresh_from_db()
    assert pending.is_verified_staff and pending.has_role(R.LECTURER, course=courses[0])


def test_level_adviser_manages_their_level_space(department, hod):
    level_space = Space.objects.create(department=department, kind="level", level=300, name="300 Level", slug="300-level")
    other = Space.objects.create(department=department, kind="level", level=100, name="100 Level", slug="100-level")
    adviser = register_staff(email="a@example.com", password=PASSWORD, full_name="A", department=department,
                             position="level_adviser", level=300)
    verify_staff(admin=hod, staff=adviser.staff_profile, approve=True)
    assert spaces.can_manage(adviser, level_space) and not spaces.can_manage(adviser, other)
    assert spaces.can_create_space(adviser)


def test_admin_assigns_lecturer_and_adviser_roles(client, department, courses, hod, make_user):
    member = make_user(email="m@example.com")
    client.force_login(hod)
    client.post(reverse("manage:user-action", args=[member.pk]), {"action": "assign_role", "role": "lecturer",
                                                                  "course": courses[1].pk})
    client.post(reverse("manage:user-action", args=[member.pk]), {"action": "assign_role", "role": "level_adviser",
                                                                  "level": 100})
    client.post(reverse("manage:user-action", args=[member.pk]), {"action": "assign_role", "role": "level_adviser",
                                                                  "level": 200})
    assert member.has_role(R.LECTURER, course=courses[1])
    assert set(member.role_assignments.filter(role="level_adviser").values_list("level", flat=True)) == {100, 200}


def test_lecturer_can_sign_up_before_courses_exist(client, department):
    """A brand-new department has no courses yet; lecturers must still be able to sign up."""
    r = staff_signup(client, department)
    assert r.status_code == 302
    user = User.objects.get(email="funmi@example.com")
    assert user.staff.position == "lecturer" and not user.staff.requested_courses.exists()

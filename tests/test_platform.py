import pytest
from django.core.exceptions import PermissionDenied
from django.urls import reverse

from apps.accounts.models import RoleAssignment, User
from apps.accounts.services import assign_role, mark_verified, register_staff, verify_staff
from apps.notifications.models import Notification
from apps.posts import services as posts

from .conftest import PASSWORD

pytestmark = pytest.mark.django_db
OWNER = "arifalotimothy@gmail.com"


@pytest.fixture
def run_hooks(django_capture_on_commit_callbacks):
    def _run(fn, *args, **kwargs):
        with django_capture_on_commit_callbacks(execute=True):
            return fn(*args, **kwargs)
    return _run


@pytest.fixture
def owner(make_user, settings):
    settings.PLATFORM_OWNER_EMAILS = [OWNER]
    user = make_user(email=OWNER, full_name="Timothy Arifalo", verified=False)
    mark_verified(user)  # verifying the email is what switches owner rights on
    user.refresh_from_db()
    return user


@pytest.fixture
def hod(make_user, department):
    u = make_user(email="hod@example.com", full_name="Head")
    assign_role(user=u, role=RoleAssignment.Role.DEPARTMENT_ADMIN, department=department)
    return u


def test_owner_becomes_platform_admin_only_once_verified(make_user, settings, client):
    settings.PLATFORM_OWNER_EMAILS = [OWNER]
    impostor = make_user(email=OWNER, verified=False)
    client.force_login(impostor)
    impostor.refresh_from_db()
    assert not impostor.is_superuser and not impostor.is_platform_admin
    mark_verified(impostor)
    impostor.refresh_from_db()
    assert impostor.is_superuser and impostor.is_platform_admin and impostor.is_owner


def test_owner_rights_restored_on_login(owner, client):
    User.objects.filter(pk=owner.pk).update(is_superuser=False, is_staff=False)
    RoleAssignment.objects.filter(user=owner).delete()
    client.post(reverse("accounts:login"), {"email": OWNER, "password": PASSWORD})
    owner.refresh_from_db()
    assert owner.is_superuser and owner.is_platform_admin


def test_owner_cannot_be_restricted(client, owner, hod):
    client.force_login(hod)
    for action in ("suspend", "ban"):
        client.post(reverse("manage:user-action", args=[owner.pk]), {"action": action})
    owner.refresh_from_db()
    assert owner.is_active and not owner.is_suspended


def test_platform_pages_are_platform_admin_only(client, owner, hod, make_user):
    pages = ["overview", "activity", "people", "staff", "audit", "database"]
    for user in (make_user(email="s@example.com"), hod):
        client.force_login(user)
        for name in pages:
            assert client.get(reverse(f"platform:{name}")).status_code == 403, name
    client.force_login(owner)
    for name in pages:
        assert client.get(reverse(f"platform:{name}")).status_code == 200, name


def test_only_platform_admins_verify_staff(run_hooks, owner, hod, department):
    staff = run_hooks(register_staff, email="l@example.com", password=PASSWORD, full_name="Lec", department=department,
                      position="exam_officer")
    assert Notification.objects.filter(recipient=owner, kind="staff", url="/platform/staff/").exists()
    assert not Notification.objects.filter(recipient=hod, kind="staff").exists()
    with pytest.raises(PermissionDenied):
        verify_staff(admin=hod, staff=staff.staff_profile, approve=True)
    verify_staff(admin=owner, staff=staff.staff_profile, approve=True)
    staff.refresh_from_db()
    assert staff.is_verified_staff


def test_platform_verify_page_and_hod_read_only(client, owner, hod, department):
    staff = register_staff(email="l@example.com", password=PASSWORD, full_name="Waiting Person", department=department,
                           position="exam_officer")
    User.objects.filter(pk=hod.pk).update(onboarding_completed=True)
    client.force_login(hod)
    page = client.get(reverse("manage:staff")).content.decode()
    assert "Waiting Person" in page and "platform admin verifies staff" in page
    client.force_login(owner)
    assert "Waiting Person" in client.get(reverse("platform:staff")).content.decode()
    client.post(reverse("platform:staff-decision", args=[staff.staff_profile.pk]), {"decision": "approve"})
    staff.refresh_from_db()
    assert staff.is_verified_staff


def test_people_search_spans_departments(client, owner, make_user, other_department):
    make_user(email="far@example.com", full_name="Faraway Student", department=other_department)
    client.force_login(owner)
    assert "Faraway Student" in client.get(reverse("platform:people") + "?q=faraway").content.decode()


def test_activity_keeps_anonymous_authors_hidden(client, owner, make_user):
    author = make_user(email="anon@example.com", full_name="Secret Writer")
    posts.create_post(author=author, kind="question", body="an anonymous worry", is_anonymous=True)
    client.force_login(owner)
    page = client.get(reverse("platform:activity") + "?kind=post").content.decode()
    assert "an anonymous worry" in page and "Secret Writer" not in page


def test_database_page_and_admin_cover_every_table(client, owner):
    from django.apps import apps
    from django.contrib import admin

    client.force_login(owner)
    page = client.get(reverse("platform:database")).content.decode()
    assert "Staff profiles" in page and "Posts" in page
    missing = [m for m in apps.get_models()
               if m._meta.app_label not in ("sessions", "contenttypes") and not admin.site.is_registered(m)]
    assert missing == []
    assert client.get("/django-admin/accounts/staffprofile/").status_code == 200


def test_run_jobs_now(client, owner):
    client.force_login(owner)
    r = client.post(reverse("platform:run-jobs"), follow=True)
    assert "Scheduled jobs ran" in r.content.decode()
    assert "Last ran" in client.get(reverse("platform:overview")).content.decode()


def test_mobile_drawer_markup(client, make_user):
    client.force_login(make_user(email="s@example.com"))
    html = client.get(reverse("core:home")).content.decode()
    assert 'id="app-sidebar"' in html and 'aria-controls="app-sidebar"' in html and "data-drawer-close" in html


def test_sessions_last_30_days(settings):
    assert settings.SESSION_COOKIE_AGE == 60 * 60 * 24 * 30


def test_lecturer_can_sign_up_when_no_courses_exist(client, department):
    from apps.accounts.models import StaffProfile

    page = client.get(reverse("accounts:signup")).content.decode()
    assert "No courses have been added for this department yet" in page
    r = client.post(reverse("accounts:signup"), {
        "account_type": "staff", "position": "lecturer", "title": "dr", "full_name": "New Lecturer",
        "email": "lect@example.com", "department": department.pk, "password": PASSWORD, "confirm_password": PASSWORD,
    })
    assert r.status_code == 302
    staff = StaffProfile.objects.get(user__email="lect@example.com")
    assert staff.position == "lecturer" and not staff.requested_courses.exists()


def test_platform_admin_picks_lecturer_courses_when_verifying(client, owner, department):
    from apps.academics.models import Course

    c1 = Course.objects.create(department=department, code="CSC 301", title="DS", level=300, semester=1)
    c2 = Course.objects.create(department=department, code="CSC 305", title="OS", level=300, semester=1)
    lecturer = register_staff(email="l@example.com", password=PASSWORD, full_name="Lec", department=department,
                              position="lecturer", courses=[c1])
    client.force_login(owner)
    page = client.get(reverse("platform:staff")).content.decode()
    assert "Courses they" in page and "CSC 305" in page
    client.post(reverse("platform:staff-decision", args=[lecturer.staff_profile.pk]),
                {"decision": "approve", "courses_submitted": "1", "courses": [c2.pk]})
    lecturer.refresh_from_db()
    assert lecturer.has_role(RoleAssignment.Role.LECTURER, course=c2)
    assert not lecturer.has_role(RoleAssignment.Role.LECTURER, course=c1)

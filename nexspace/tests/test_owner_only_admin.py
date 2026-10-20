"""Only the platform owner (arifalotimothy@gmail.com) can be a platform admin or create department admins."""
import pytest
from django.core.exceptions import PermissionDenied
from django.urls import reverse

from apps.accounts.models import RoleAssignment, User
from apps.accounts.owner import ensure_owner, strip_non_owner_admins
from apps.accounts.services import assign_role, register_staff, verify_staff

from .conftest import PASSWORD

pytestmark = pytest.mark.django_db
OWNER = "arifalotimothy@gmail.com"


@pytest.fixture
def owner(make_user):
    user = make_user(email=OWNER, full_name="Timothy")
    ensure_owner(user)
    user.refresh_from_db()
    return user


def test_ordinary_students_have_no_admin_access(client, make_user):
    student = make_user(email="student@example.com")
    client.force_login(student)
    assert 'aria-label="Admin"' not in client.get("/").content.decode()
    for url in ("/manage/", "/platform/", "/django-admin/"):
        assert client.get(url).status_code in (302, 403), url


def test_createsuperuser_accounts_are_stripped(client, make_user):
    rogue = User.objects.create_superuser(email="rogue@example.com", password=PASSWORD, full_name="Rogue")
    sneaky = make_user(email="sneaky@example.com")
    assign_role(user=sneaky, role=RoleAssignment.Role.SUPER_ADMIN)
    assert strip_non_owner_admins() == 2
    rogue.refresh_from_db()
    assert not rogue.is_superuser and not rogue.is_staff and not rogue.is_platform_admin
    assert not sneaky.role_assignments.filter(role="super_admin").exists()


def test_non_owner_admin_is_demoted_the_moment_they_log_in(client, make_user):
    rogue = User.objects.create_superuser(email="rogue@example.com", password=PASSWORD, full_name="Rogue")
    client.post(reverse("accounts:login"), {"email": "rogue@example.com", "password": PASSWORD})
    rogue.refresh_from_db()
    assert not rogue.is_superuser
    assert client.get("/platform/").status_code in (302, 403)


def test_owner_keeps_full_access(client, owner):
    assert strip_non_owner_admins() == 0
    owner.refresh_from_db()
    assert owner.is_superuser and owner.is_platform_admin
    client.force_login(owner)
    assert client.get("/platform/").status_code == 200


def test_only_owner_makes_department_admins(client, owner, make_user, department):
    hod = make_user(email="hod@example.com")
    assign_role(user=hod, role=RoleAssignment.Role.DEPARTMENT_ADMIN, department=department)  # e.g. verified HOD
    target = make_user(email="target@example.com")
    client.force_login(hod)
    page = client.get(reverse("manage:user", args=[target.pk])).content.decode()
    assert 'value="department_admin"' not in page  # not offered
    client.post(reverse("manage:user-action", args=[target.pk]), {"action": "assign_role", "role": "department_admin"})
    assert not target.has_role(RoleAssignment.Role.DEPARTMENT_ADMIN, department=department)
    client.force_login(owner)
    client.post(reverse("manage:user-action", args=[target.pk]), {"action": "assign_role", "role": "department_admin"})
    assert target.has_role(RoleAssignment.Role.DEPARTMENT_ADMIN, department=department)


def test_hods_can_only_be_verified_by_the_owner(settings, owner, make_user, department):
    settings.STAFF_VERIFICATION = "department"
    hod = make_user(email="hod@example.com")
    assign_role(user=hod, role=RoleAssignment.Role.DEPARTMENT_ADMIN, department=department)
    claimant = register_staff(email="claim@example.com", password=PASSWORD, full_name="Claims HOD",
                              department=department, position="hod")
    with pytest.raises(PermissionDenied):
        verify_staff(admin=hod, staff=claimant.staff_profile, approve=True)
    verify_staff(admin=owner, staff=claimant.staff_profile, approve=True)


def test_department_admins_cannot_remove_each_other(client, make_user, department):
    a = make_user(email="a@example.com")
    b = make_user(email="b@example.com")
    for u in (a, b):
        assign_role(user=u, role=RoleAssignment.Role.DEPARTMENT_ADMIN, department=department)
    ra = b.role_assignments.get(role="department_admin")
    client.force_login(a)
    client.post(reverse("manage:user-action", args=[b.pk]), {"action": "revoke_role", "assignment": ra.pk})
    assert b.has_role(RoleAssignment.Role.DEPARTMENT_ADMIN, department=department)

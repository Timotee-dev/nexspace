"""The admin pages (/manage/, /platform/) belong to the platform owner only."""
import pytest
from django.urls import reverse

from apps.accounts.models import RoleAssignment
from apps.accounts.owner import ensure_owner
from apps.accounts.services import assign_role

from .conftest import PASSWORD

pytestmark = pytest.mark.django_db
OWNER = "arifalotimothy@gmail.com"


@pytest.fixture
def owner(make_user):
    user = make_user(email=OWNER, full_name="Timothy")
    ensure_owner(user)
    return user


def test_admin_link_only_for_the_owner(client, owner, make_user, department):
    hod = make_user(email="hod@example.com")
    assign_role(user=hod, role=RoleAssignment.Role.DEPARTMENT_ADMIN, department=department)
    for user, expected in ((make_user(email="s@example.com"), False), (hod, False), (owner, True)):
        client.force_login(user)
        assert ('aria-label="Admin"' in client.get("/").content.decode()) is expected, user.email


@pytest.mark.parametrize("url", ["/manage/", "/manage/users/", "/platform/", "/platform/people/"])
def test_everyone_else_is_asked_to_sign_in_as_admin(client, make_user, department, url):
    hod = make_user(email="hod@example.com")
    assign_role(user=hod, role=RoleAssignment.Role.DEPARTMENT_ADMIN, department=department)
    client.force_login(hod)
    r = client.get(url)
    assert r.status_code == 302 and r.url.startswith(reverse("manage:sign-in"))
    page = client.get(r.url).content.decode()
    assert "Admin only" in page and "hod@example.com" in page


def test_wrong_account_cannot_get_in(client, make_user):
    student = make_user(email="s@example.com")
    client.force_login(student)
    r = client.post(reverse("manage:sign-in"), {"email": "s@example.com", "password": PASSWORD, "next": "/manage/"})
    assert r.status_code == 403 and "Only the NexSpace admin" in r.content.decode()
    assert client.get("/manage/").status_code == 302  # still locked out


def test_owner_sign_in_switches_to_the_admin_account(client, owner, make_user):
    client.force_login(make_user(email="s@example.com"))
    r = client.post(reverse("manage:sign-in"), {"email": OWNER, "password": PASSWORD, "next": "/manage/users/"})
    assert r.status_code == 302 and r.url == "/manage/users/"
    assert client.get("/manage/users/").status_code == 200
    assert int(client.session["_auth_user_id"]) == owner.pk


def test_wrong_password_and_unsafe_next(client, owner):
    r = client.post(reverse("manage:sign-in"), {"email": OWNER, "password": "wrong", "next": "https://evil.example"})
    assert r.status_code == 403
    r = client.post(reverse("manage:sign-in"), {"email": OWNER, "password": PASSWORD, "next": "https://evil.example"})
    assert r.url == reverse("manage:overview")  # never redirects to another site


def test_department_mode_still_works(client, settings, make_user, department):
    settings.ADMIN_AREA = "department"
    hod = make_user(email="hod@example.com")
    assign_role(user=hod, role=RoleAssignment.Role.DEPARTMENT_ADMIN, department=department)
    client.force_login(hod)
    assert client.get("/manage/").status_code == 200

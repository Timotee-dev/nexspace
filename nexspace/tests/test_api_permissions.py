import pytest
from django.urls import reverse
from rest_framework.test import APIClient, APIRequestFactory

from apps.accounts.models import RoleAssignment
from apps.accounts.services import assign_role, revoke_role
from apps.core.models import AuditLog
from apps.core.permissions import IsVerifiedOrReadOnly

from .conftest import PASSWORD

pytestmark = pytest.mark.django_db


@pytest.fixture
def api():
    return APIClient()


def test_api_signup_login_me(api, department):
    r = api.post(reverse("api-signup"), {
        "full_name": "Api User", "email": "api@example.com", "password": PASSWORD,
        "confirm_password": PASSWORD, "department": department.pk, "level": 100,
    }, format="json")
    assert r.status_code == 201, r.data
    assert r.data["email_verified"] is False
    api.logout()
    r = api.post(reverse("api-login"), {"email": "api@example.com", "password": PASSWORD}, format="json")
    assert r.status_code == 200
    assert api.get(reverse("api-me")).data["username"] == "api_user"


def test_api_errors_use_consistent_envelope(api, department):
    r = api.post(reverse("api-signup"), {"email": "bad"}, format="json")
    assert r.status_code == 400
    assert r.data["error"]["code"] == "invalid" and "email" in r.data["error"]["fields"]
    r = api.get(reverse("api-me"))
    assert r.status_code == 403 and "message" in r.data["error"]


def test_api_login_lockout(api, user):
    for _ in range(5):
        api.post(reverse("api-login"), {"email": user.email, "password": "nope"}, format="json")
    r = api.post(reverse("api-login"), {"email": user.email, "password": PASSWORD}, format="json")
    assert r.status_code == 429 and r.data["error"]["code"] == "locked"


def test_me_cannot_change_protected_fields(api, make_user):
    user = make_user(verified=False)
    api.force_authenticate(user)
    r = api.patch(reverse("api-me"), {"email_verified": True, "email": "x@y.com", "bio": "hi", "theme": "dark"}, format="json")
    assert r.status_code == 200
    user.refresh_from_db()
    assert user.email_verified is False and user.email != "x@y.com"
    assert user.profile.bio == "hi" and user.profile.theme == "dark"


def test_public_profile_api_hides_private_fields(api, make_user, other_department):
    owner = make_user(email="o@example.com", matric_number="CSC/9")
    viewer = make_user(email="v@example.com")
    api.force_authenticate(viewer)
    data = api.get(reverse("api-profile", args=[owner.username])).data
    assert "matric_number" not in data and "email" not in data
    owner.profile.show_social_links = False
    owner.profile.visibility = "department"
    owner.profile.save()
    assert "links" not in api.get(reverse("api-profile", args=[owner.username])).data
    outsider = make_user(email="z@example.com", department=other_department)
    api.force_authenticate(outsider)
    assert api.get(reverse("api-profile", args=[owner.username])).status_code == 404


class _View:
    pass


@pytest.mark.parametrize("method,verified,allowed", [
    ("get", False, True), ("post", False, False), ("post", True, True), ("delete", False, False),
])
def test_unverified_users_blocked_from_writes(make_user, method, verified, allowed):
    user = make_user(verified=verified)
    request = getattr(APIRequestFactory(), method)("/anything/")
    request.user = user
    assert IsVerifiedOrReadOnly().has_permission(request, _View()) is allowed


def test_role_scoping_and_audit(make_user, department, other_department):
    rep = make_user(email="rep@example.com")
    admin = make_user(email="boss@example.com")
    assert not rep.has_role(RoleAssignment.Role.COURSE_REP)
    assign_role(user=rep, role=RoleAssignment.Role.COURSE_REP, department=department, granted_by=admin)
    assert rep.has_role(RoleAssignment.Role.COURSE_REP, department=department)
    assert not rep.has_role(RoleAssignment.Role.COURSE_REP, department=other_department)
    assert not rep.has_role(RoleAssignment.Role.DEPARTMENT_ADMIN)
    assert AuditLog.objects.filter(action="role.assigned", target_id=str(rep.pk)).exists()

    assign_role(user=admin, role=RoleAssignment.Role.SUPER_ADMIN)
    assert admin.has_role(RoleAssignment.Role.MODERATOR, department=other_department)

    revoke_role(user=rep, role=RoleAssignment.Role.COURSE_REP, department=department, revoked_by=admin)
    assert not rep.has_role(RoleAssignment.Role.COURSE_REP)
    assert AuditLog.objects.filter(action="role.revoked").exists()


def test_suspended_user_has_no_roles(make_user, department):
    user = make_user(email="gone@example.com")
    assign_role(user=user, role=RoleAssignment.Role.MODERATOR, department=department)
    user.is_active = False
    assert not user.has_role(RoleAssignment.Role.MODERATOR, department=department)


def test_public_endpoints(api, department):
    assert api.get(reverse("api-departments")).status_code == 200
    assert api.get(reverse("api-topics")).status_code == 200


def test_seed_demo_refuses_in_production(settings):
    from django.core.management import CommandError, call_command

    settings.DEBUG = False
    with pytest.raises(CommandError):
        call_command("seed_demo")


def test_seed_demo_runs_in_debug(settings, db):
    from django.core.management import call_command

    from apps.accounts.models import User

    settings.DEBUG = True
    call_command("seed_demo")
    call_command("seed_demo")  # idempotent
    assert User.objects.filter(email="rep@nexspace.test").exists()


def test_landing_is_public_and_indexable(client, db):
    r = client.get("/")
    assert r.status_code == 200 and b"index, follow" in r.content


def test_private_pages_are_noindex(client, user):
    client.force_login(user)
    assert b"noindex" in client.get("/").content

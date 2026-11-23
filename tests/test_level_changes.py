"""A student's level decides which courses they join without approval, so they can't change it freely:
anything in their first week, then one level up every 6 months; admins can set any level."""
import json
from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import RoleAssignment
from apps.accounts.services import assign_role, level_change_problem
from apps.core.models import AuditLog

pytestmark = pytest.mark.django_db


@pytest.fixture
def senior(make_user):
    """A 200 Level student who joined two months ago."""
    user = make_user(email="two@example.com", level=200)
    type(user).objects.filter(pk=user.pk).update(date_joined=timezone.now() - timedelta(days=60))
    user.refresh_from_db()
    return user


def settings_post(client, user, level):
    return client.post(reverse("accounts:settings-profile"), {"full_name": user.full_name, "username": user.username,
                                                      "level": level, "save_profile": "1"})


def test_new_students_can_fix_their_level_in_the_first_week(client, make_user):
    user = make_user(email="new@example.com", level=100)
    client.force_login(user)
    settings_post(client, user, 300)
    user.refresh_from_db()
    assert user.level == 300 and user.level_changed_at is None  # a fix, not a move up


def test_cant_jump_levels_or_go_down(client, senior):
    client.force_login(senior)
    r = settings_post(client, senior, 400)
    senior.refresh_from_db()
    assert senior.level == 200 and "one level at a time" in r.content.decode()
    settings_post(client, senior, 100)
    senior.refresh_from_db()
    assert senior.level == 200


def test_move_up_once_then_wait_six_months(client, senior):
    client.force_login(senior)
    settings_post(client, senior, 300)
    senior.refresh_from_db()
    assert senior.level == 300 and senior.level_changed_at is not None
    r = settings_post(client, senior, 400)
    senior.refresh_from_db()
    assert senior.level == 300 and "recently" in r.content.decode()
    type(senior).objects.filter(pk=senior.pk).update(level_changed_at=timezone.now() - timedelta(days=200))
    senior.refresh_from_db()
    assert level_change_problem(senior, 400) is None


def test_the_app_api_follows_the_same_rule(client, senior):
    client.force_login(senior)
    r = client.patch("/api/auth/me/", json.dumps({"level": 400}), content_type="application/json")
    senior.refresh_from_db()
    assert r.status_code == 400 and senior.level == 200
    r = client.patch("/api/auth/me/", json.dumps({"level": 300}), content_type="application/json")
    senior.refresh_from_db()
    assert r.status_code == 200 and senior.level == 300
    r = client.patch("/api/auth/me/", json.dumps({"bio": "hi", "level": 300}), content_type="application/json")
    assert r.status_code == 200  # sending the same level back is fine


def test_admin_can_set_any_level(client, senior, make_user, department):
    owner = make_user(email="arifalotimothy@gmail.com")
    assign_role(user=owner, role=RoleAssignment.Role.SUPER_ADMIN)
    assign_role(user=owner, role=RoleAssignment.Role.DEPARTMENT_ADMIN, department=department)
    client.force_login(owner)
    assert "Change their level" in client.get(reverse("manage:user", args=[senior.pk])).content.decode()
    client.post(reverse("manage:user-action", args=[senior.pk]), {"action": "set_level", "level": "100"})
    senior.refresh_from_db()
    assert senior.level == 100 and AuditLog.objects.filter(action="user.level_changed").exists()
    client.post(reverse("manage:user-action", args=[senior.pk]), {"action": "set_level", "level": "junk"})
    senior.refresh_from_db()
    assert senior.level == 100


def test_students_cant_use_the_admin_level_action(client, senior, make_user):
    other = make_user(email="other@example.com", level=100)
    client.force_login(senior)
    client.post(reverse("manage:user-action", args=[other.pk]), {"action": "set_level", "level": "500"})
    other.refresh_from_db()
    assert other.level == 100

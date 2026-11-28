"""When emails fail, the owner sees exactly why (with the fix) instead of silence."""
import io
import json
import urllib.error

import pytest
from django.core import mail
from django.core.cache import cache
from django.urls import reverse

from apps.core import email as brevo
from apps.accounts.models import RoleAssignment
from apps.accounts.services import assign_role

pytestmark = pytest.mark.django_db


def fake_refusal(status, message):
    def urlopen(request, timeout=None):
        raise urllib.error.HTTPError(request.full_url, status, "refused", {},
                                     io.BytesIO(json.dumps({"code": "unauthorized", "message": message}).encode()))
    return urlopen


@pytest.fixture
def owner(make_user, department):
    user = make_user(email="arifalotimothy@gmail.com")
    assign_role(user=user, role=RoleAssignment.Role.SUPER_ADMIN)
    return user


def test_blocked_ip_is_explained(settings, monkeypatch):
    settings.BREVO_API_KEY = "xkeysib-test"
    monkeypatch.setattr(brevo.urllib.request, "urlopen",
                        fake_refusal(401, "We have detected you are using an unrecognised IP address 3.1.2.3."))
    with pytest.raises(brevo.BrevoError) as exc:
        brevo.BrevoEmailBackend().send_messages([mail.EmailMessage("s", "b", to=["a@example.com"])])
    assert "Authorised IPs" in str(exc.value) and "unrecognised IP" in str(exc.value)
    assert "Authorised IPs" in cache.get(brevo.LAST_FAILURE_KEY)["why"]


def test_known_causes_have_fixes():
    assert "BREVO_API_KEY" in brevo.explain_brevo_error(401, "Key not found")
    assert "DEFAULT_FROM_EMAIL" in brevo.explain_brevo_error(400, "sender is not valid")


def test_test_email_button_shows_the_reason(client, owner, settings, monkeypatch):
    settings.BREVO_API_KEY = "xkeysib-test"
    settings.EMAIL_BACKEND = "apps.core.email.BrevoEmailBackend"
    monkeypatch.setattr(brevo.urllib.request, "urlopen",
                        fake_refusal(401, "We have detected you are using an unrecognised IP address 3.1.2.3."))
    client.force_login(owner)
    page = client.post(reverse("platform:test-email"), follow=True).content.decode()
    assert "The test email failed" in page and "Authorised IPs" in page
    assert "Last email failed" in client.get(reverse("platform:overview")).content.decode()


def test_test_email_success(client, owner, settings):
    settings.BREVO_API_KEY = "xkeysib-test"  # locmem backend from the test settings delivers to mail.outbox
    client.force_login(owner)
    page = client.post(reverse("platform:test-email"), follow=True).content.decode()
    assert "Test email sent to arifalotimothy@gmail.com" in page and len(mail.outbox) == 1


def test_only_the_owner_can_send_test_emails(client, make_user, settings):
    settings.BREVO_API_KEY = "xkeysib-test"
    client.force_login(make_user(email="student@example.com"))
    client.post(reverse("platform:test-email"))
    assert len(mail.outbox) == 0

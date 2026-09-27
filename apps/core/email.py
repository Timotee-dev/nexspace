"""Email backend for the Brevo transactional HTTP API.

Render blocks outbound SMTP ports, so production email goes over HTTPS instead.
Uses only the standard library, so there is no extra dependency to install.
"""
import json
import logging
import urllib.error
import urllib.request
from email.utils import parseaddr

from django.conf import settings
from django.core.mail.backends.base import BaseEmailBackend

logger = logging.getLogger(__name__)
BREVO_ENDPOINT = "https://api.brevo.com/v3/smtp/email"


def _contact(address: str) -> dict:
    name, email = parseaddr(address)
    return {"email": email, "name": name} if name else {"email": email}


class BrevoEmailBackend(BaseEmailBackend):
    def send_messages(self, email_messages):
        sent = 0
        for message in email_messages:
            payload = {
                "sender": _contact(message.from_email or settings.DEFAULT_FROM_EMAIL),
                "to": [_contact(addr) for addr in message.to],
                "subject": message.subject,
                "textContent": message.body,
            }
            for content, mimetype in getattr(message, "alternatives", []) or []:
                if mimetype == "text/html":
                    payload["htmlContent"] = content
            request = urllib.request.Request(
                BREVO_ENDPOINT,
                data=json.dumps(payload).encode(),
                headers={
                    "api-key": settings.BREVO_API_KEY,
                    "content-type": "application/json",
                    "accept": "application/json",
                },
                method="POST",
            )
            try:
                with urllib.request.urlopen(request, timeout=15) as response:
                    if 200 <= response.status < 300:
                        sent += 1
            except (urllib.error.URLError, TimeoutError) as exc:
                logger.error("Brevo email failed for %s: %s", message.to, exc)
                if not self.fail_silently:
                    raise
        return sent

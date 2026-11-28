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


class BrevoError(Exception):
    """Brevo refused an email. The message says why, in plain words, with the fix when it's a known one."""


def explain_brevo_error(status: int, detail: str) -> str:
    """Brevo's error, plus what to do about the usual causes."""
    text = (detail or "").strip()
    low = text.lower()
    if "ip" in low and ("unrecognised" in low or "unrecognized" in low or "not authorized" in low or "authorised" in low):
        hint = ("Brevo is blocking the server's address. In Brevo, open Security \u2192 Authorised IPs and turn the "
                "blocking off (Vercel's servers change address, so they can't be added one by one).")
    elif status == 401 or "key not found" in low or "unauthorized" in low:
        hint = "The API key was refused. Check BREVO_API_KEY in your hosting settings (no spaces), then redeploy."
    elif "sender" in low:
        hint = ("The sender isn't verified in Brevo. DEFAULT_FROM_EMAIL must use an address or domain verified in "
                "Brevo \u2192 Senders, Domains & Dedicated IPs.")
    elif status == 402 or "credit" in low or "limit" in low:
        hint = "The Brevo plan's sending limit is used up for now. It resets daily on the free plan."
    else:
        hint = "Check the Brevo account (Transactional \u2192 Logs) for details."
    return f"Brevo refused the email ({status}): {text or 'no details'}. {hint}"


LAST_FAILURE_KEY = "email:last-failure"


def _remember_failure(explanation: str) -> None:
    """Shown on the owner's Platform page, so a broken email setup is noticed straight away."""
    try:
        from django.core.cache import cache
        from django.utils import timezone

        cache.set(LAST_FAILURE_KEY, {"at": timezone.now().isoformat(), "why": explanation}, 7 * 24 * 60 * 60)
    except Exception:
        pass


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
                        try:
                            from django.core.cache import cache

                            cache.delete(LAST_FAILURE_KEY)  # working again
                        except Exception:
                            pass
            except urllib.error.HTTPError as exc:
                try:
                    body = exc.read().decode("utf-8", "replace")
                    detail = json.loads(body).get("message", body)
                except Exception:
                    detail = ""
                explanation = explain_brevo_error(exc.code, str(detail)[:300])
                logger.error("Brevo email failed for %s: %s", message.to, explanation)
                _remember_failure(explanation)
                if not self.fail_silently:
                    raise BrevoError(explanation) from exc
            except (urllib.error.URLError, TimeoutError) as exc:
                logger.error("Brevo email failed for %s: %s", message.to, exc)
                _remember_failure(f"Couldn't reach Brevo: {exc}")
                if not self.fail_silently:
                    raise BrevoError(f"Couldn't reach Brevo: {exc}") from exc
        return sent

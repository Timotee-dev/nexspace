"""Account business logic, shared by the template views and the API."""
import logging
import re

from django.conf import settings
from django.core import signing
from django.core.mail import EmailMultiAlternatives
from django.db import transaction
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone
from django.utils.text import slugify

from apps.core.models import audit

logger = logging.getLogger(__name__)
VERIFY_SALT = "nexspace.accounts.verify-email"


def generate_username(seed: str) -> str:
    from .models import RESERVED_USERNAMES, User

    base = re.sub(r"[^a-z0-9_]", "", slugify(seed).replace("-", "_"))[:24] or "student"
    if len(base) < 3:
        base = f"{base}_ns"
    candidate, n = base, 1
    while candidate in RESERVED_USERNAMES or User.objects.filter(username=candidate).exists():
        n += 1
        candidate = f"{base}{n}"
    return candidate


@transaction.atomic
def register_user(*, email, password, full_name, department, level, matric_number=None):
    from .models import User

    return User.objects.create_user(
        email=email,
        password=password,
        full_name=full_name.strip(),
        department=department,
        level=level,
        matric_number=matric_number or None,
    )


# --- Email verification ----------------------------------------------------
def make_verification_token(user) -> str:
    # The email is part of the payload, so changing email invalidates old links.
    return signing.dumps({"uid": user.pk, "email": user.email}, salt=VERIFY_SALT)


def read_verification_token(token: str):
    """Return the user for a valid token, or None if invalid/expired/stale."""
    from .models import User

    try:
        data = signing.loads(
            token, salt=VERIFY_SALT, max_age=settings.EMAIL_VERIFICATION_MAX_AGE.total_seconds()
        )
    except signing.BadSignature:  # includes SignatureExpired
        return None
    user = User.objects.filter(pk=data.get("uid"), is_active=True).first()
    if user is None or user.email != data.get("email"):
        return None
    return user


def mark_verified(user) -> None:
    if not user.email_verified:
        user.email_verified = True
        user.email_verified_at = timezone.now()
        user.save(update_fields=["email_verified", "email_verified_at"])


def send_verification_email(user) -> None:
    link = settings.SITE_URL + reverse("accounts:verify-email", args=[make_verification_token(user)])
    context = {"user": user, "link": link, "days": settings.EMAIL_VERIFICATION_MAX_AGE.days}
    message = EmailMultiAlternatives(
        subject="Verify your NexSpace email",
        body=render_to_string("emails/verify_email.txt", context),
        to=[user.email],
    )
    message.attach_alternative(render_to_string("emails/verify_email.html", context), "text/html")
    try:
        message.send()
    except Exception:  # never let an email provider outage break sign-up
        logger.exception("Could not send verification email to user %s", user.pk)


# --- Roles -----------------------------------------------------------------
def assign_role(*, user, role, department=None, course=None, granted_by=None):
    from .models import RoleAssignment

    if course is not None:
        department = None  # course scope replaces department scope
    assignment, created = RoleAssignment.objects.get_or_create(
        user=user, role=role, department=department, course=course, defaults={"granted_by": granted_by}
    )
    if created:
        audit(granted_by, "role.assigned", user, role=role,
              department_id=getattr(department, "pk", None), course_id=getattr(course, "pk", None))
        if course is not None and role == RoleAssignment.Role.COURSE_REP:
            from apps.spaces.services import sync_course_rep_moderation

            sync_course_rep_moderation(user, course, is_rep=True)
    return assignment


def revoke_role(*, user, role, department=None, course=None, revoked_by=None) -> bool:
    from .models import RoleAssignment

    deleted, _ = RoleAssignment.objects.filter(user=user, role=role, department=department, course=course).delete()
    if deleted:
        audit(revoked_by, "role.revoked", user, role=role,
              department_id=getattr(department, "pk", None), course_id=getattr(course, "pk", None))
        if course is not None and role == RoleAssignment.Role.COURSE_REP:
            from apps.spaces.services import sync_course_rep_moderation

            sync_course_rep_moderation(user, course, is_rep=False)
    return bool(deleted)

"""Account business logic, shared by the template views and the API."""
import logging
from datetime import timedelta
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
    from .owner import ensure_owner

    ensure_owner(user)


# --- One-time email codes ------------------------------------------------------------
CODE_LIFETIME = 30 * 60
CODE_MAX_ATTEMPTS = 5


def _hash_code(user, purpose, code):
    import hashlib
    import hmac

    msg = f"{purpose}:{user.pk}:{code}".encode()
    return hmac.new(settings.SECRET_KEY.encode(), msg, hashlib.sha256).hexdigest()


def issue_code(user, purpose) -> str:
    """Create a fresh 6-digit code (replacing any unused one for the same purpose) and return it."""
    import secrets

    from .models import EmailCode

    EmailCode.objects.filter(user=user, purpose=purpose, used_at__isnull=True).delete()
    code = f"{secrets.randbelow(1_000_000):06d}"
    EmailCode.objects.create(user=user, purpose=purpose, code_hash=_hash_code(user, purpose, code),
                             expires_at=timezone.now() + timezone.timedelta(seconds=CODE_LIFETIME))
    return code


def check_code(user, purpose, code) -> bool:
    """True (and the code is used up) if `code` is the user's current, unexpired code for `purpose`."""
    import hmac

    from .models import EmailCode

    code = "".join(ch for ch in str(code or "") if ch.isdigit())
    entry = (EmailCode.objects.filter(user=user, purpose=purpose, used_at__isnull=True,
                                      expires_at__gt=timezone.now()).order_by("-created_at").first())
    if entry is None or entry.attempts >= CODE_MAX_ATTEMPTS or len(code) != 6:
        if entry is not None and len(code) == 6:
            EmailCode.objects.filter(pk=entry.pk).update(attempts=entry.attempts + 1)
        return False
    if not hmac.compare_digest(entry.code_hash, _hash_code(user, purpose, code)):
        EmailCode.objects.filter(pk=entry.pk).update(attempts=entry.attempts + 1)
        return False
    EmailCode.objects.filter(pk=entry.pk).update(used_at=timezone.now())
    return True


def send_verification_email(user) -> None:
    link = settings.SITE_URL + reverse("accounts:verify-email", args=[make_verification_token(user)])
    context = {"user": user, "link": link, "days": settings.EMAIL_VERIFICATION_MAX_AGE.days, "site_url": settings.SITE_URL,
               "code": issue_code(user, "verify"), "code_url": settings.SITE_URL + reverse("accounts:verify-code")}
    message = EmailMultiAlternatives(
        subject=f"{context['code']} is your NexSpace verification code",
        body=render_to_string("emails/verify_email.txt", context),
        to=[user.email],
    )
    message.attach_alternative(render_to_string("emails/verify_email.html", context), "text/html")
    try:
        message.send()
    except Exception:  # never let an email provider outage break sign-up
        logger.exception("Could not send verification email to user %s", user.pk)


# --- Roles -----------------------------------------------------------------
COURSE_ROLES = ("course_rep", "lecturer")


def assign_role(*, user, role, department=None, course=None, level=None, granted_by=None):
    from .models import RoleAssignment

    if course is not None:
        department, level = None, None  # course scope replaces department scope
    assignment, created = RoleAssignment.objects.get_or_create(
        user=user, role=role, department=department, course=course, level=level,
        defaults={"granted_by": granted_by},
    )
    if created:
        audit(granted_by, "role.assigned", user, role=role, level=level,
              department_id=getattr(department, "pk", None), course_id=getattr(course, "pk", None))
        if course is not None and role in COURSE_ROLES:
            from apps.spaces.services import sync_course_rep_moderation

            sync_course_rep_moderation(user, course, is_rep=True)
    return assignment


def revoke_role(*, user, role, department=None, course=None, level=None, revoked_by=None) -> bool:
    from .models import RoleAssignment

    deleted, _ = RoleAssignment.objects.filter(user=user, role=role, department=department, course=course,
                                               level=level).delete()
    if deleted:
        audit(revoked_by, "role.revoked", user, role=role, level=level,
              department_id=getattr(department, "pk", None), course_id=getattr(course, "pk", None))
        if course is not None and role in COURSE_ROLES and not RoleAssignment.objects.filter(
                user=user, course=course, role__in=COURSE_ROLES).exists():
            from apps.spaces.services import sync_course_rep_moderation

            sync_course_rep_moderation(user, course, is_rep=False)
    return bool(deleted)


# --- Account deletion (right to erasure) -------------------------------------
@transaction.atomic
def delete_account(user, *, delete_content=False):
    """Erase a person's account. Their personal data is removed; what they posted stays under
    "Deleted user" (so discussions still make sense) unless they ask for it to be deleted too."""
    from apps.discover.models import OpportunityReminder
    from apps.groups import services as groups
    from apps.nexai.models import Usage
    from apps.notifications.models import Notification, NotificationPreference, PushSubscription
    from apps.posts.models import Bookmark, Comment, Post
    from apps.social.models import TopicFollow, UserFollow
    from apps.spaces import services as spaces
    from apps.spaces.models import SpaceJoinRequest, SpaceMembership

    from .models import RoleAssignment

    audit(user, "account.deleted", user, delete_content=delete_content)
    for membership in SpaceMembership.objects.filter(user=user).select_related("space"):
        spaces.leave(user, membership.space)
    for membership in user.study_group_memberships.select_related("group"):
        groups.leave(user=user, group=membership.group)
    for model, field in ((UserFollow, "follower"), (UserFollow, "following"), (TopicFollow, "user"),
                         (Bookmark, "user"), (Notification, "recipient"), (NotificationPreference, "user"),
                         (PushSubscription, "user"), (OpportunityReminder, "user"), (SpaceJoinRequest, "user"),
                         (RoleAssignment, "user"), (Usage, "user")):
        model.objects.filter(**{field: user}).delete()
    if delete_content:
        Post.objects.filter(author=user).update(is_deleted=True, deleted_at=timezone.now())
        Comment.objects.filter(author=user).update(is_deleted=True, body="")

    profile = user.profile
    if profile.avatar:
        profile.avatar.delete(save=False)
    profile.avatar = ""
    profile.bio, profile.skills = "", []
    profile.github_url = profile.linkedin_url = profile.portfolio_url = ""
    profile.save()
    profile.interests.clear()

    user.email = f"deleted-{user.pk}@deleted.invalid"
    user.username = f"deleted_{user.pk}"
    user.full_name = "Deleted user"
    user.matric_number = None
    user.is_active = False
    user.email_verified = False
    user.is_staff = user.is_superuser = False
    user.set_unusable_password()
    user.save()
    from django.contrib.sessions.models import Session

    for session in Session.objects.filter(expire_date__gt=timezone.now()):
        if session.get_decoded().get("_auth_user_id") == str(user.pk):
            session.delete()



# --- Staff ------------------------------------------------------------------
@transaction.atomic
def register_staff(*, email, password, full_name, department, position, title="", staff_id="",
                   courses=(), level=None):
    """Create a staff account. It starts PENDING: no staff powers until a department admin verifies it."""
    from .models import StaffProfile, User

    user = User.objects.create_user(email=email, password=password, full_name=full_name.strip(),
                                    department=department, level=None)
    staff = StaffProfile.objects.create(user=user, position=position, title=title or "",
                                        staff_id=(staff_id or "")[:30], requested_level=level)
    staff.requested_courses.set([c for c in courses if c.department_id == department.pk])
    audit(user, "staff.requested", user, position=position)
    from apps.notifications import services as notifications

    notifications.staff_requested(staff)
    return user


def can_verify_staff(admin, staff) -> bool:
    """settings.STAFF_VERIFICATION = "platform" (default): only platform admins verify staff.
    "department": the HOD or a department admin of that department can too. Nobody verifies themselves."""
    from .models import RoleAssignment

    if admin.pk == staff.user_id:
        return False
    if admin.is_platform_admin:
        return True
    if settings.STAFF_VERIFICATION == "platform":
        return False
    return admin.has_role(RoleAssignment.Role.DEPARTMENT_ADMIN, department=staff.user.department)


@transaction.atomic
def verify_staff(*, admin, staff, approve: bool, note=""):
    """Approve (grant the position's permissions) or reject a staff account."""
    from django.core.exceptions import PermissionDenied

    from .models import RoleAssignment, StaffProfile

    if not can_verify_staff(admin, staff):
        raise PermissionDenied("Only the HOD or a department admin can verify staff.")
    user, dept = staff.user, staff.user.department
    staff.status = StaffProfile.Status.VERIFIED if approve else StaffProfile.Status.REJECTED
    staff.decided_by, staff.decided_at, staff.note = admin, timezone.now(), (note or "")[:300]
    staff.save()
    if approve:
        R, P = RoleAssignment.Role, StaffProfile.Position
        if staff.position == P.HOD:
            assign_role(user=user, role=R.DEPARTMENT_ADMIN, department=dept, granted_by=admin)
        elif staff.position == P.EXAM_OFFICER:
            assign_role(user=user, role=R.EXAM_OFFICER, department=dept, granted_by=admin)
        elif staff.position == P.LEVEL_ADVISER and staff.requested_level:
            assign_role(user=user, role=R.LEVEL_ADVISER, department=dept, level=staff.requested_level,
                        granted_by=admin)
        elif staff.position == P.LECTURER:
            for course in staff.requested_courses.all():
                assign_role(user=user, role=R.LECTURER, course=course, granted_by=admin)
        if not user.email_verified:  # the HOD vouching for them is at least as strong as an email link
            mark_verified(user)
    audit(admin, "staff.verified" if approve else "staff.rejected", user, position=staff.position)
    from apps.notifications import services as notifications

    notifications.staff_decided(staff)
    return staff


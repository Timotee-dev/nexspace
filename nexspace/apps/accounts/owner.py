"""Platform owner accounts (settings.PLATFORM_OWNER_EMAILS).

An owner is made a super admin automatically — on login, when their email is verified, and after every
`migrate` — but only once their email address is verified, so nobody can claim the role by signing up
with the owner's address first.
"""
from django.conf import settings


def is_owner_email(email) -> bool:
    return (email or "").strip().lower() in settings.PLATFORM_OWNER_EMAILS


def ensure_owner(user):
    """Give an owner account full platform rights. Returns True if the user is an active, verified owner."""
    from .models import RoleAssignment, User

    if not (user and user.pk and user.is_active and user.email_verified and is_owner_email(user.email)):
        return False
    if not (user.is_superuser and user.is_staff):
        User.objects.filter(pk=user.pk).update(is_superuser=True, is_staff=True, onboarding_completed=True)
        user.is_superuser = user.is_staff = True
    RoleAssignment.objects.get_or_create(user=user, role=RoleAssignment.Role.SUPER_ADMIN, department=None,
                                         course=None, level=None)
    return True


def ensure_all_owners(**kwargs):
    from .models import User

    for user in User.objects.filter(email__in=settings.PLATFORM_OWNER_EMAILS):
        ensure_owner(user)
    strip_non_owner_admins()


def strip_non_owner_admins(**kwargs):
    """Only the owner accounts may be platform admins. Anyone else holding superuser/staff flags or the
    Super Admin role (e.g. made with `createsuperuser`, or in the database admin) loses them.

    Runs after every `migrate` (so on every Render deploy) and whenever a non-owner logs in.
    Does nothing if no owner email is configured, so a deployment can never lock itself out.
    """
    from django.conf import settings
    from django.db.models import Q

    from .models import RoleAssignment, User
    from .services import audit

    owners = settings.PLATFORM_OWNER_EMAILS
    if not owners:
        return 0
    offenders = (User.objects.filter(Q(is_superuser=True) | Q(is_staff=True)
                                     | Q(role_assignments__role=RoleAssignment.Role.SUPER_ADMIN))
                 .exclude(email__in=owners).distinct())
    count = 0
    for user in offenders:
        User.objects.filter(pk=user.pk).update(is_superuser=False, is_staff=False)
        RoleAssignment.objects.filter(user=user, role=RoleAssignment.Role.SUPER_ADMIN).delete()
        audit(None, "admin.revoked_non_owner", user, email=user.email)
        count += 1
    return count


def demote_if_not_owner(user):
    from django.conf import settings

    from .models import RoleAssignment

    if not settings.PLATFORM_OWNER_EMAILS or is_owner_email(user.email):
        return
    if user.is_superuser or user.is_staff or user.role_assignments.filter(role=RoleAssignment.Role.SUPER_ADMIN).exists():
        strip_non_owner_admins()
        user.is_superuser = user.is_staff = False

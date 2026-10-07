"""Runs on every deploy (Render build command). Makes the platform owner account match OWNER_EMAIL and
OWNER_PASSWORD from Render's Environment settings — the same approach as the Eunice Wears store.

- Creates the account if it doesn't exist (already verified, onboarding done, in the first department).
- If it exists, makes sure it's active, verified and uses OWNER_PASSWORD, so the owner can always sign in.
- Makes it the platform admin. Every other admin account is stripped of its rights (see accounts/owner.py).
The password lives only in Render's settings, never in the code.
"""
import os

from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Create or repair the platform owner account from OWNER_EMAIL and OWNER_PASSWORD."

    def handle(self, *args, **options):
        from django.conf import settings
        from django.utils import timezone

        from apps.academics.models import Department
        from apps.accounts.models import User
        from apps.accounts.owner import ensure_owner, strip_non_owner_admins

        email = os.environ.get("OWNER_EMAIL", "").strip().lower()
        password = os.environ.get("OWNER_PASSWORD", "")
        if not email or not password:
            self.stdout.write("OWNER_EMAIL / OWNER_PASSWORD not set: owner account left as it is.")
            return
        if email not in settings.PLATFORM_OWNER_EMAILS:
            self.stderr.write(f"{email} isn't in PLATFORM_OWNER_EMAILS; check your Render settings.")
            return
        user = User.objects.filter(email__iexact=email).first()
        if user is None:
            user = User.objects.create_user(
                email=email, password=password,
                full_name=os.environ.get("OWNER_FULL_NAME", "").strip() or "NexSpace Admin",
                department=Department.objects.order_by("pk").first(), level=None,
                email_verified=True, onboarding_completed=True,
            )
            user.email_verified_at = timezone.now()
            user.save(update_fields=["email_verified_at"])
            self.stdout.write(self.style.SUCCESS(f"Owner account created for {email}."))
        else:
            changed = []
            if not user.check_password(password):
                user.set_password(password)
                changed.append("password")
            if not user.is_active:
                user.is_active = True
                user.suspended_until = None
                changed.append("reactivated")
            if not user.email_verified:
                user.email_verified, user.email_verified_at = True, timezone.now()
                changed.append("email verified")
            if user.department_id is None:
                user.department = Department.objects.order_by("pk").first()
                changed.append("department")
            user.onboarding_completed = True
            user.save()
            self.stdout.write(f"Owner account {email} checked" + (f" ({', '.join(changed)})." if changed else ": all good."))
        ensure_owner(user)
        stripped = strip_non_owner_admins()
        if stripped:
            self.stdout.write(f"Removed admin rights from {stripped} other account(s).")

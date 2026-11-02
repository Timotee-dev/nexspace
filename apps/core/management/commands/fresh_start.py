from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Delete every account except the platform owner, and all activity (keeps departments, courses, topics)."

    def add_arguments(self, parser):
        parser.add_argument("--owner", required=True, help="The platform owner's email")
        parser.add_argument("--yes", action="store_true", help="Really do it (it can't be undone)")

    def handle(self, *args, **options):
        from apps.accounts.models import User
        from apps.core import fresh_start

        owner = User.objects.filter(email=options["owner"].strip().lower()).first()
        if owner is None or not owner.is_owner:
            raise CommandError("That email isn't the platform owner's verified account.")
        if not options["yes"]:
            for label, n in fresh_start.preview(owner):
                self.stdout.write(f"  {n:>5}  {label}")
            raise CommandError("Nothing deleted. Add --yes to really do it.")
        for label, n in fresh_start.fresh_start(owner).items():
            self.stdout.write(f"  {n:>5}  {label}")
        self.stdout.write(self.style.SUCCESS("Fresh start done."))

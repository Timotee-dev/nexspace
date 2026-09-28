from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Send reminders, index resources for NexAI, recompute trending, deliver push and tidy up."

    def handle(self, *args, **options):
        from apps.core.scheduled import run_all

        self.stdout.write(self.style.SUCCESS(f"Done: {run_all()}"))

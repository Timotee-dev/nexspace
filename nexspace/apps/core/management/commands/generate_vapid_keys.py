from django.core.management.base import BaseCommand

from apps.notifications.webpush import generate_vapid_keys


class Command(BaseCommand):
    help = "Generate a VAPID key pair for browser push notifications."

    def handle(self, *args, **options):
        public, private = generate_vapid_keys()
        self.stdout.write("Add these to your environment (keep the private key secret):\n")
        self.stdout.write(f"VAPID_PUBLIC_KEY={public}")
        self.stdout.write(f"VAPID_PRIVATE_KEY={private}")
        self.stdout.write("VAPID_SUBJECT=mailto:you@example.com")

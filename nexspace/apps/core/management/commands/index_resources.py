from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Extract text from resources for NexAI. Use --all to rebuild every resource's index."

    def add_arguments(self, parser):
        parser.add_argument("--all", action="store_true")

    def handle(self, *args, **options):
        from apps.nexai.indexing import index_pending, index_resource
        from apps.resources.models import Resource

        if options["all"]:
            count = 0
            for resource in Resource.objects.filter(is_removed=False):
                index_resource(resource)
                count += 1
        else:
            count = index_pending(limit=10_000)
        self.stdout.write(self.style.SUCCESS(f"Indexed {count} resource(s)."))

"""Create the real university / faculty / department for production (safe to re-run)."""
from django.core.management.base import BaseCommand
from django.utils.text import slugify

from apps.academics.models import Department, Faculty, University


class Command(BaseCommand):
    help = "Create a university, faculty and department so students can sign up."

    def add_arguments(self, parser):
        parser.add_argument("--university", required=True, help='e.g. "University of Medical Sciences, Ondo"')
        parser.add_argument("--short", required=True, help="e.g. UNIMED")
        parser.add_argument("--faculty", required=True, help='e.g. "Faculty of Computing"')
        parser.add_argument("--department", required=True, help='e.g. "Computer Science"')
        parser.add_argument("--code", required=True, help="e.g. CSC")

    def handle(self, *args, **o):
        uni, _ = University.objects.get_or_create(
            slug=slugify(o["short"]), defaults={"name": o["university"], "short_name": o["short"]}
        )
        faculty, _ = Faculty.objects.get_or_create(
            university=uni, slug=slugify(o["faculty"]), defaults={"name": o["faculty"]}
        )
        dept, created = Department.objects.get_or_create(
            faculty=faculty, slug=slugify(o["department"]),
            defaults={"name": o["department"], "code": o["code"].upper()},
        )
        verb = "Created" if created else "Already exists:"
        self.stdout.write(self.style.SUCCESS(f"{verb} {dept}"))

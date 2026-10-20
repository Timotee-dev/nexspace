from django.db.models.signals import post_save
from django.dispatch import receiver

from apps.academics.models import Course

from .services import ensure_course_space


@receiver(post_save, sender=Course)
def create_course_space(sender, instance, created, **kwargs):
    if created:
        ensure_course_space(instance)

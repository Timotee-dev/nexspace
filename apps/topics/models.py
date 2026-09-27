from django.db import models


class Topic(models.Model):
    """Platform-wide tag. Onboarding interests map to topics; posts get up to 3 (Phase 2)."""

    name = models.CharField(max_length=60, unique=True)
    slug = models.SlugField(unique=True)
    description = models.CharField(max_length=200, blank=True)
    is_active = models.BooleanField(default=True)
    sort_order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["sort_order", "name"]

    def __str__(self):
        return self.name

import logging

from django.db import transaction
from django.utils import timezone

from .extract import UnsupportedFile, chunk, pages_from_file
from .models import ResourceChunk

logger = logging.getLogger(__name__)
MAX_CHUNKS = 400


def index_resource(resource) -> int:
    """(Re)build a resource's passages. Returns the number of passages stored."""
    from apps.resources.models import Resource

    try:
        with resource.file.open("rb") as f:
            passages = chunk(pages_from_file(f, resource.original_name))[:MAX_CHUNKS]
        error = "" if passages else "No readable text found (it may be a scanned image)."
    except UnsupportedFile as exc:
        passages, error = [], str(exc)[:200]
    except Exception as exc:  # corrupt or unreadable file
        logger.warning("Could not index resource %s: %s", resource.pk, exc)
        passages, error = [], "This file couldn't be read."
    with transaction.atomic():
        ResourceChunk.objects.filter(resource=resource).delete()
        ResourceChunk.objects.bulk_create(
            [ResourceChunk(resource=resource, position=i, page=page, text=text) for i, (page, text) in enumerate(passages)]
        )
        Resource.objects.filter(pk=resource.pk).update(indexed_at=timezone.now(), index_error=error)
    return len(passages)


def index_pending(limit=50) -> int:
    from apps.resources.models import Resource

    done = 0
    for resource in Resource.objects.filter(indexed_at__isnull=True, is_removed=False)[:limit]:
        index_resource(resource)
        done += 1
    return done

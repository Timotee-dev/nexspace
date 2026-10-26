import logging

from django.conf import settings
from django.db import transaction
from django.db.models import Max
from django.utils import timezone

from .extract import UnsupportedFile, chunk, pages_from_file
from .models import ResourceChunk

logger = logging.getLogger(__name__)
MAX_CHUNKS = 400
SCAN_NOTE = "This looks like a photo or scan. Its text will be read shortly."


def _scanned_pages(pages):
    """Page numbers whose text is too thin to be real text (photos, scans)."""
    from .ocr import MIN_TEXT_CHARS

    return [page for page, text in pages if page is not None and len(text.strip()) < MIN_TEXT_CHARS]


def index_resource(resource) -> int:
    """(Re)build a resource's passages from its normal text. Pages that are photos or scans are queued for
    OCR (read later by the scheduled job), so an upload never waits on it. Returns the passages stored."""
    from apps.resources.models import Resource

    from . import ocr

    waiting = []
    try:
        if ocr.is_image(resource.original_name):
            passages, error, waiting = [], "", [1]
        else:
            with resource.file.open("rb") as f:
                pages = pages_from_file(f, resource.original_name)
            passages = chunk(pages)[:MAX_CHUNKS]
            if resource.original_name.lower().endswith(".pdf"):
                waiting = _scanned_pages(pages)[: settings.OCR_MAX_PAGES_PER_FILE]
            error = "" if passages else "No readable text found (it may be a scanned image)."
    except UnsupportedFile as exc:
        passages, error = [], str(exc)[:200]
    except Exception as exc:  # corrupt or unreadable file
        logger.warning("Could not index resource %s: %s", resource.pk, exc)
        passages, error = [], "This file couldn't be read."

    fields = {"indexed_at": timezone.now(), "index_error": error}
    if waiting and ocr.enabled():
        fields.update(ocr_status=Resource.OcrStatus.PENDING, ocr_pages_waiting=waiting, ocr_page_count=len(waiting),
                      ocr_retries=0, ocr_error="")
        if not passages:
            fields["index_error"] = SCAN_NOTE
    elif waiting:
        fields.update(ocr_status=Resource.OcrStatus.NONE, ocr_pages_waiting=[], ocr_page_count=0)
        if not passages:
            fields["index_error"] = "No readable text found. It looks like a photo or scan."
    with transaction.atomic():
        ResourceChunk.objects.filter(resource=resource).delete()
        ResourceChunk.objects.bulk_create(
            [ResourceChunk(resource=resource, position=i, page=page, text=text) for i, (page, text) in enumerate(passages)]
        )
        Resource.objects.filter(pk=resource.pk).update(**fields)
    return len(passages)


def add_ocr_text(resource, page, text):
    """Store the text OCR read from one page, after the passages already stored."""
    text = (text or "").strip()
    if not text:
        return 0
    from .extract import _clean

    pieces = chunk([(page, _clean(text))])
    with transaction.atomic():
        existing = ResourceChunk.objects.filter(resource=resource)
        if existing.count() >= MAX_CHUNKS:
            return 0
        start = (existing.aggregate(m=Max("position"))["m"] or -1) + 1
        room = MAX_CHUNKS - existing.count()
        ResourceChunk.objects.bulk_create(
            [ResourceChunk(resource=resource, position=start + i, page=p, text=t) for i, (p, t) in enumerate(pieces[:room])]
        )
    return len(pieces)


def finish_ocr(resource):
    from apps.resources.models import Resource

    has_text = ResourceChunk.objects.filter(resource=resource).exists()
    Resource.objects.filter(pk=resource.pk).update(
        ocr_status=Resource.OcrStatus.DONE, ocr_pages_waiting=[], ocr_error="",
        index_error="" if has_text else "No text could be found, even in the scanned pages.",
    )


def index_pending(limit=50) -> int:
    from apps.resources.models import Resource

    done = 0
    for resource in Resource.objects.filter(indexed_at__isnull=True, is_removed=False)[:limit]:
        index_resource(resource)
        done += 1
    return done


def queue_unread_scans(limit=500) -> int:
    """After OCR is switched on: queue files that were uploaded earlier and had no readable text."""
    from apps.resources.models import Resource

    from . import ocr

    if not ocr.enabled():
        return 0
    old = Resource.objects.filter(is_removed=False, indexed_at__isnull=False, ocr_status=Resource.OcrStatus.NONE,
                                  chunks__isnull=True).distinct()[:limit]
    queued = 0
    for resource in old:
        if ocr.is_image(resource.original_name) or resource.original_name.lower().endswith(".pdf"):
            index_resource(resource)
            queued += 1
    return queued

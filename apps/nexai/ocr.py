"""Reading text from photos and scanned PDF pages with the free OCR.space service.

How it fits together:
- When a course material is uploaded, normal text is pulled out straight away (extract.py).
  Pages with (almost) no text — photos, scans — are remembered as "waiting for OCR".
- The scheduled job (run_scheduled, every 15 minutes) sends those pages to OCR.space one
  at a time, within a time budget and a daily page limit, so the free plan never runs out
  and the scheduler's request never times out.
- Each page is sent as a JPEG under OCR.space's free 1 MB limit, so long PDFs work even
  though the free plan only accepts 3-page PDFs.

Turn it on by setting OCR_SPACE_API_KEY (free key from https://ocr.space/ocrapi).
Without a key nothing is sent anywhere and uploads work exactly as before.
"""
import base64
import json
import logging
import time
import urllib.error
import urllib.parse
import urllib.request
from io import BytesIO

from django.conf import settings
from django.core.cache import cache
from django.utils import timezone

logger = logging.getLogger(__name__)

ENDPOINT = "https://api.ocr.space/parse/image"
FREE_LIMIT_BYTES = 1024 * 1024        # OCR.space free plan: 1 MB per file
TARGET_BYTES = 900 * 1024             # stay safely under it (base64 adds overhead on the wire only)
MIN_TEXT_CHARS = 25                   # a PDF page with less text than this is treated as a scan
IMAGE_EXTENSIONS = (".jpg", ".jpeg", ".png", ".webp")
MAX_RETRIES = 30                      # about a week of failed runs before a file is given up on


class OcrError(Exception):
    """OCR.space refused or failed. `retry` says whether trying later could help."""

    def __init__(self, message, retry=True):
        super().__init__(message)
        self.retry = retry


def enabled() -> bool:
    return bool(settings.OCR_SPACE_API_KEY)


def is_image(filename) -> bool:
    return filename.lower().endswith(IMAGE_EXTENSIONS)


# --- Daily allowance -------------------------------------------------------------------
def _day_key():
    return f"ocr-pages:{timezone.localdate():%Y%m%d}"


def pages_used_today() -> int:
    return cache.get(_day_key(), 0)


def pages_left_today() -> int:
    return max(0, settings.OCR_DAILY_PAGE_LIMIT - pages_used_today())


def _count_page():
    key = _day_key()
    if not cache.add(key, 1, 60 * 60 * 26):
        try:
            cache.incr(key)
        except ValueError:
            cache.set(key, 1, 60 * 60 * 26)


# --- Turning pages into small JPEGs -----------------------------------------------------
def _to_jpeg(image) -> bytes:
    """Grey-scale JPEG under TARGET_BYTES. Grey keeps text sharp and files small."""
    from PIL import ImageOps

    image = ImageOps.exif_transpose(image).convert("L")
    image.thumbnail((settings.OCR_MAX_EDGE, settings.OCR_MAX_EDGE))
    for quality in (85, 75, 65, 55):
        buffer = BytesIO()
        image.save(buffer, "JPEG", quality=quality, optimize=True)
        if buffer.tell() <= TARGET_BYTES:
            return buffer.getvalue()
    while True:  # still too big: shrink until it fits
        image = image.resize((int(image.width * 0.8), int(image.height * 0.8)))
        buffer = BytesIO()
        image.save(buffer, "JPEG", quality=60, optimize=True)
        if buffer.tell() <= TARGET_BYTES or image.width < 600:
            return buffer.getvalue()


def image_page(fileobj) -> bytes:
    from PIL import Image

    fileobj.seek(0)
    with Image.open(fileobj) as image:
        if image.format == "JPEG":  # decode straight to a smaller grey image: far less memory than full size
            image.draft("L", (settings.OCR_MAX_EDGE, settings.OCR_MAX_EDGE))
        image.load()
        return _to_jpeg(image)


def pdf_page(pdf_bytes: bytes, page_number: int) -> bytes:
    """Render one PDF page (1-based) to a JPEG. Only that page is held in memory."""
    import pypdfium2 as pdfium

    pdf = pdfium.PdfDocument(pdf_bytes)
    try:
        page = pdf[page_number - 1]
        try:
            width, height = page.get_size()  # in points (1/72 inch)
            scale = min(200 / 72, settings.OCR_MAX_EDGE / max(width, height, 1))  # ~200 dpi, capped
            bitmap = page.render(scale=scale, grayscale=True)
            try:
                return _to_jpeg(bitmap.to_pil())
            finally:
                bitmap.close()
        finally:
            page.close()
    finally:
        pdf.close()


# --- Talking to OCR.space ---------------------------------------------------------------
def read_jpeg(jpeg: bytes) -> str:
    """Send one page to OCR.space and return the text it found (may be empty)."""
    if len(jpeg) > FREE_LIMIT_BYTES:
        raise OcrError("This page is too large to read.", retry=False)
    data = urllib.parse.urlencode({
        "base64Image": "data:image/jpeg;base64," + base64.b64encode(jpeg).decode(),
        "language": settings.OCR_SPACE_LANGUAGE,
        "OCREngine": str(settings.OCR_SPACE_ENGINE),
        "scale": "true",
        "detectOrientation": "true",
        "isOverlayRequired": "false",
        "filetype": "JPG",
    }).encode()
    request = urllib.request.Request(ENDPOINT, data=data, method="POST",
                                     headers={"apikey": settings.OCR_SPACE_API_KEY,
                                              "Content-Type": "application/x-www-form-urlencoded"})
    try:
        with urllib.request.urlopen(request, timeout=settings.OCR_REQUEST_TIMEOUT) as response:
            body = json.loads(response.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403):
            raise OcrError("The OCR.space key was refused. Check OCR_SPACE_API_KEY on Render.") from exc
        raise OcrError(f"OCR.space returned an error ({exc.code}).") from exc
    except (urllib.error.URLError, TimeoutError, ValueError) as exc:
        raise OcrError("Couldn't reach OCR.space.") from exc
    if not isinstance(body, dict):  # the free plan answers with a plain string when over its limits
        raise OcrError(f"OCR.space: {str(body)[:150]}")
    if body.get("IsErroredOnProcessing"):
        message = body.get("ErrorMessage") or "OCR.space couldn't read this page."
        if isinstance(message, list):
            message = " ".join(str(m) for m in message)
        message = str(message)[:180]
        # Limits and time-outs pass; anything else is about this page, so skip it rather than loop forever.
        busy = any(w in message.lower() for w in ("limit", "maximum", "timed out", "timeout", "busy", "try again"))
        raise OcrError(message, retry=busy)
    results = body.get("ParsedResults") or []
    return "\n".join((r.get("ParsedText") or "") for r in results).strip()


# --- The scheduled step -------------------------------------------------------------------
def process_pending(time_budget=None) -> dict:
    """Read waiting pages until the time budget or today's allowance runs out."""
    from apps.resources.models import Resource

    from .indexing import add_ocr_text, finish_ocr

    stats = {"pages": 0, "files_done": 0, "skipped": ""}
    if not enabled():
        stats["skipped"] = "no key"
        return stats
    budget = settings.OCR_TIME_BUDGET if time_budget is None else time_budget
    deadline = time.monotonic() + budget
    queue = Resource.objects.filter(ocr_status=Resource.OcrStatus.PENDING, is_removed=False).order_by("created_at")
    for resource in queue[:20]:
        if time.monotonic() >= deadline:
            break
        if pages_left_today() <= 0:
            stats["skipped"] = "daily limit reached"
            break
        waiting = list(resource.ocr_pages_waiting or [])
        if not waiting:
            finish_ocr(resource)
            stats["files_done"] += 1
            continue
        try:
            with resource.file.open("rb") as f:
                raw = f.read()
        except Exception:
            logger.warning("OCR: couldn't open resource %s", resource.pk)
            _fail(resource, "The file couldn't be opened for reading.")
            continue
        while waiting and time.monotonic() < deadline and pages_left_today() > 0:
            page = waiting[0]
            try:
                jpeg = image_page(BytesIO(raw)) if is_image(resource.original_name) else pdf_page(raw, page)
                text = read_jpeg(jpeg)
            except OcrError as exc:
                if exc.retry:
                    logger.info("OCR paused for resource %s: %s", resource.pk, exc)
                    if _note_retry(resource, str(exc)) >= MAX_RETRIES:
                        _fail(resource, "Text couldn't be read from the scanned pages after several tries.")
                    stats["skipped"] = str(exc)[:80]
                    return stats  # stop this run; OCR.space is down or busy
                text = ""
            except Exception:
                logger.exception("OCR: page %s of resource %s couldn't be prepared", page, resource.pk)
                text = ""
            _count_page()
            stats["pages"] += 1
            add_ocr_text(resource, page if not is_image(resource.original_name) else None, text)
            waiting.pop(0)
            Resource.objects.filter(pk=resource.pk).update(ocr_pages_waiting=waiting, ocr_error="")
            resource.ocr_pages_waiting = waiting
        if not waiting:
            finish_ocr(resource)
            stats["files_done"] += 1
    return stats


def _note_retry(resource, message) -> int:
    from django.db.models import F

    from apps.resources.models import Resource

    Resource.objects.filter(pk=resource.pk).update(ocr_error=message[:200], ocr_retries=F("ocr_retries") + 1)
    return Resource.objects.values_list("ocr_retries", flat=True).get(pk=resource.pk)


def _fail(resource, message):
    from apps.resources.models import Resource

    Resource.objects.filter(pk=resource.pk).update(ocr_status=Resource.OcrStatus.FAILED, ocr_error=message[:200],
                                                    ocr_pages_waiting=[])

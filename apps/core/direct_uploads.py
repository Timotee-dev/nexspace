"""Direct uploads: the browser sends files straight to Cloudinary, not through NexSpace's server.

Why: Vercel refuses any request over 4.5 MB before it reaches NexSpace. Files sent straight to Cloudinary
skip that limit, so uploads can be as big as Cloudinary's free plan allows (10 MB per file).

How it works:
1. The browser asks NexSpace for permission to upload one file (`/uploads/sign/`). NexSpace checks the size
   and type, picks a random name in a temporary "staging" folder, and returns a Cloudinary signature for
   exactly that name plus a token tied to this user.
2. The browser uploads the file to Cloudinary with that signature.
3. The browser submits the form as usual, but with the token (`__direct__<field>`) instead of the file.
4. DirectUploadMiddleware turns each token back into a normal uploaded file (downloading it from the staging
   folder), so every view, form and check works exactly as it did for files sent the old way: real file-type
   checks, size limits, photo shrinking, NexAI reading. The staging copy is then deleted.
Files that were uploaded but never submitted are deleted by the scheduled job after a few hours.
"""
import logging
import mimetypes
import os
import time
import urllib.request
import uuid

from django.conf import settings
from django.core import signing
from django.core.files.uploadedfile import SimpleUploadedFile

logger = logging.getLogger(__name__)

STAGING = "nexspace-staging"
TAG = "nexspace-staging"
FIELD_PREFIX = "__direct__"
SALT = "nexspace.direct-upload"
TOKEN_MAX_AGE = 6 * 60 * 60          # a form can be left open for a while before it's sent
ALLOWED_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".pdf", ".doc", ".docx", ".ppt", ".pptx"}
SIGN_RATE = (120, 60 * 60)           # signatures per user per hour


class DirectUploadError(Exception):
    """Shown to the person as is."""


def enabled() -> bool:
    return bool(getattr(settings, "DIRECT_UPLOADS", False))


def _max_bytes() -> int:
    return settings.MAX_UPLOAD_MB * 1024 * 1024


def _cloudinary():
    import cloudinary
    import cloudinary.utils

    return cloudinary


def sign(user, name: str, size, content_type: str = "") -> dict:
    """Permission to upload one file straight to Cloudinary. Raises DirectUploadError with a friendly message."""
    if not enabled():
        raise DirectUploadError("Direct uploads aren't switched on.")
    name = os.path.basename(str(name or "")).strip()[:200]
    ext = os.path.splitext(name)[1].lower()
    if not name or ext not in ALLOWED_EXTS:
        raise DirectUploadError("Upload a photo (JPEG, PNG or WebP) or a PDF, Word or PowerPoint file.")
    try:
        size = int(size)
    except (TypeError, ValueError):
        raise DirectUploadError("That file couldn't be read. Try choosing it again.")
    if size <= 0:
        raise DirectUploadError("That file is empty.")
    if size > _max_bytes():
        raise DirectUploadError(f"{name} is too big. Files can be up to {settings.MAX_UPLOAD_MB} MB.")

    cloudinary = _cloudinary()
    public_id = f"{STAGING}/{user.pk}/{uuid.uuid4().hex}{ext}"
    fields = cloudinary.utils.sign_request({"timestamp": int(time.time()), "public_id": public_id, "tags": TAG}, {})
    content_type = (content_type or mimetypes.guess_type(name)[0] or "application/octet-stream")[:100]
    token = signing.dumps({"u": user.pk, "p": public_id, "n": name, "t": content_type, "s": size}, salt=SALT)
    return {
        "url": cloudinary.utils.cloudinary_api_url("upload", resource_type="raw"),
        "fields": fields,
        "token": token,
    }


def _download(public_id: str, resource_type: str = "raw", fmt: str = "", limit: int | None = None) -> bytes:
    """Read a stored file back through Cloudinary's API (works for any file type, including PDFs)."""
    cloudinary = _cloudinary()
    url = cloudinary.utils.private_download_url(public_id, fmt, resource_type=resource_type, type="upload")
    with urllib.request.urlopen(url, timeout=60) as response:
        data = response.read(limit + 1 if limit else -1)
    if limit and len(data) > limit:
        raise DirectUploadError(f"Files can be up to {settings.MAX_UPLOAD_MB} MB.")
    return data


def _destroy(public_id: str) -> None:
    try:
        import cloudinary.uploader

        cloudinary.uploader.destroy(public_id, resource_type="raw", invalidate=True)
    except Exception:  # the scheduled clean-up gets it later
        logger.warning("Couldn't delete staging upload %s", public_id)


def receive(user, token: str):
    """Turn a token from the browser back into an uploaded file. Raises DirectUploadError."""
    try:
        data = signing.loads(token, salt=SALT, max_age=TOKEN_MAX_AGE)
    except signing.SignatureExpired:
        raise DirectUploadError("That upload took too long. Choose the file again and resend.")
    except signing.BadSignature:
        raise DirectUploadError("That upload couldn't be checked. Choose the file again and resend.")
    if data.get("u") != user.pk or not str(data.get("p", "")).startswith(f"{STAGING}/{user.pk}/"):
        raise DirectUploadError("That upload couldn't be checked. Choose the file again and resend.")
    try:
        content = _download(data["p"], limit=_max_bytes())
    except DirectUploadError:
        _destroy(data["p"])
        raise
    except Exception:
        logger.exception("Couldn't fetch staging upload %s", data["p"])
        raise DirectUploadError(f"{data['n']} didn't finish uploading. Check your connection and send it again.")
    _destroy(data["p"])
    if not content:
        raise DirectUploadError(f"{data['n']} didn't finish uploading. Send it again.")
    return SimpleUploadedFile(data["n"], content, content_type=data.get("t") or "application/octet-stream")


def clean_up_staging(older_than_hours: int = 6) -> int:
    """Delete files that were uploaded but never sent with a form. Returns how many were deleted."""
    if not enabled():
        return 0
    import cloudinary.api
    from django.utils import timezone
    from django.utils.dateparse import parse_datetime

    cutoff = timezone.now() - timezone.timedelta(hours=older_than_hours)
    old = []
    result = cloudinary.api.resources(type="upload", resource_type="raw", prefix=f"{STAGING}/", max_results=500)
    for item in result.get("resources", []):
        created = parse_datetime(item.get("created_at", "") or "")
        if created and created < cutoff:
            old.append(item["public_id"])
    for start in range(0, len(old), 100):
        cloudinary.api.delete_resources(old[start:start + 100], resource_type="raw", type="upload")
    return len(old)


def fields_in(post) -> list[str]:
    return [key for key in post.keys() if key.startswith(FIELD_PREFIX)]


def target_field(key: str) -> str:
    return key[len(FIELD_PREFIX):]


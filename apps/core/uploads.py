"""Shared upload helpers: safe random filenames and real content-type checks."""
import os
import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from PIL import Image, UnidentifiedImageError

ALLOWED_IMAGE_FORMATS = {"JPEG": ".jpg", "PNG": ".png", "WEBP": ".webp"}


def post_file_upload_path(instance, filename):
    ext = os.path.splitext(filename)[1].lower()
    return f"post-files/{uuid.uuid4().hex}{ext}"


def avatar_upload_path(instance, filename):
    ext = os.path.splitext(filename)[1].lower()
    return f"avatars/{uuid.uuid4().hex}{ext}"


def validate_image_upload(file):
    """Check size and the real image format from file contents, not the browser's claim."""
    if file.size > settings.MAX_IMAGE_UPLOAD_BYTES:
        raise ValidationError("Images must be 5 MB or smaller.")
    try:
        position = file.tell()
        with Image.open(file) as image:
            image_format = image.format
            image.verify()
        file.seek(position)
    except (UnidentifiedImageError, OSError):
        raise ValidationError("Upload a JPEG, PNG or WebP image.")
    if image_format not in ALLOWED_IMAGE_FORMATS:
        raise ValidationError("Upload a JPEG, PNG or WebP image.")
    ext = os.path.splitext(file.name)[1].lower()
    allowed_exts = {".jpg", ".jpeg", ".png", ".webp"}
    if ext not in allowed_exts:
        raise ValidationError("Upload a JPEG, PNG or WebP image.")


# --- Documents -------------------------------------------------------------
OLE_MAGIC = bytes.fromhex("D0CF11E0A1B11AE1")
DOCUMENT_TYPES = {
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".doc": "application/msword",
    ".ppt": "application/vnd.ms-powerpoint",
}


def _detect_document(file) -> str | None:
    """Return the real extension from file contents, or None if unsupported."""
    import zipfile

    position = file.tell()
    file.seek(0)
    head = file.read(8)
    file.seek(0)
    try:
        if head.startswith(b"%PDF"):
            return ".pdf"
        if head.startswith(OLE_MAGIC):
            return "ole"  # legacy .doc or .ppt; the extension decides which
        if head.startswith(b"PK\x03\x04"):
            try:
                with zipfile.ZipFile(file) as archive:
                    names = set(archive.namelist())
            except zipfile.BadZipFile:
                return None
            if "word/document.xml" in names:
                return ".docx"
            if "ppt/presentation.xml" in names:
                return ".pptx"
        return None
    finally:
        file.seek(position)


def validate_document_upload(file) -> str:
    """Validate a PDF/Word/PowerPoint upload. Returns the safe content type."""
    if file.size > settings.MAX_DOCUMENT_UPLOAD_BYTES:
        raise ValidationError("Files must be 20 MB or smaller.")
    ext = os.path.splitext(file.name)[1].lower()
    if ext not in DOCUMENT_TYPES:
        raise ValidationError("Upload a PDF, Word (.doc, .docx) or PowerPoint (.ppt, .pptx) file.")
    detected = _detect_document(file)
    if detected == "ole" and ext in {".doc", ".ppt"}:
        return DOCUMENT_TYPES[ext]
    if detected != ext:
        raise ValidationError(f"{file.name} doesn't look like a real {ext[1:].upper()} file.")
    return DOCUMENT_TYPES[ext]


def image_content_type(file) -> str:
    validate_image_upload(file)
    with Image.open(file) as image:
        fmt = image.format
    file.seek(0)
    return {"JPEG": "image/jpeg", "PNG": "image/png", "WEBP": "image/webp"}[fmt]


def resource_upload_path(instance, filename):
    ext = os.path.splitext(filename)[1].lower()
    return f"resources/{uuid.uuid4().hex}{ext}"


def announcement_upload_path(instance, filename):
    ext = os.path.splitext(filename)[1].lower()
    return f"announcements/{uuid.uuid4().hex}{ext}"


def optimize_image(file):
    """Resize to IMAGE_MAX_EDGE, fix orientation and strip all metadata (EXIF/GPS).

    Returns a new in-memory file with the same name, or the original on failure.
    """
    from io import BytesIO

    from django.core.files.uploadedfile import InMemoryUploadedFile
    from PIL import ImageOps

    try:
        file.seek(0)
        with Image.open(file) as image:
            fmt = image.format
            image = ImageOps.exif_transpose(image)
            image.thumbnail((settings.IMAGE_MAX_EDGE, settings.IMAGE_MAX_EDGE))
            if fmt == "JPEG" and image.mode not in ("RGB", "L"):
                image = image.convert("RGB")
            buffer = BytesIO()
            options = {"JPEG": {"quality": 85, "optimize": True, "progressive": True},
                       "PNG": {"optimize": True}, "WEBP": {"quality": 85}}[fmt]
            image.save(buffer, fmt, **options)  # a fresh save carries no EXIF
    except Exception:
        file.seek(0)
        return file
    buffer.seek(0)
    content_type = {"JPEG": "image/jpeg", "PNG": "image/png", "WEBP": "image/webp"}[fmt]
    return InMemoryUploadedFile(buffer, "file", file.name, content_type, buffer.getbuffer().nbytes, None)

"""Shared upload helpers: safe random filenames and real content-type checks."""
import logging
import os
import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from PIL import Image, UnidentifiedImageError

logger = logging.getLogger(__name__)

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
        raise ValidationError(f"Images must be {settings.MAX_UPLOAD_MB} MB or smaller.")
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
        raise ValidationError(f"Files must be {settings.MAX_DOCUMENT_MB} MB or smaller.")
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


def optimize_image(file, max_edge=None):
    """Resize to IMAGE_MAX_EDGE (or max_edge), fix orientation and strip all metadata (EXIF/GPS).

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
            edge = max_edge or settings.IMAGE_MAX_EDGE
            image.thumbnail((edge, edge))
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


# --- Compressing big uploads -----------------------------------------------------------
# People may upload up to MAX_UPLOAD_MB (100 MB by default). Anything bigger than COMPRESS_OVER is
# compressed first: photos are resized, PDFs have their pictures recompressed, Word/PowerPoint files have
# their embedded pictures recompressed. What's stored must still fit the storage's own per-file limit
# (Cloudinary's free plan: 10 MB), and people get a clear message if it doesn't.
COMPRESS_OVER = 2 * 1024 * 1024
DOC_IMAGE_EDGE = 1800      # longest side for pictures inside documents
DOC_IMAGE_QUALITY = 65     # JPEG quality for pictures inside documents


def _mb(size):
    return f"{size / (1024 * 1024):.1f} MB"


def _as_upload(data: bytes, name, content_type):
    from io import BytesIO

    from django.core.files.uploadedfile import InMemoryUploadedFile

    return InMemoryUploadedFile(BytesIO(data), "file", name, content_type, len(data), None)


def _shrink_picture(raw: bytes, keep_format: str):
    """Downscale and recompress one embedded picture; returns new bytes or None if not smaller."""
    from io import BytesIO

    try:
        with Image.open(BytesIO(raw)) as img:
            img.thumbnail((DOC_IMAGE_EDGE, DOC_IMAGE_EDGE))
            out = BytesIO()
            if keep_format == "PNG":
                img.save(out, "PNG", optimize=True)
            else:
                if img.mode not in ("RGB", "L"):
                    img = img.convert("RGB")
                img.save(out, "JPEG", quality=DOC_IMAGE_QUALITY, optimize=True, progressive=True)
    except Exception:
        return None
    data = out.getvalue()
    return data if len(data) < len(raw) else None


def _compress_pdf(file) -> bytes | None:
    from io import BytesIO

    from pypdf import PdfReader, PdfWriter

    file.seek(0)
    writer = PdfWriter(clone_from=PdfReader(file))
    for page in writer.pages:
        for picture in page.images:
            try:
                image = picture.image
                if max(image.size) > DOC_IMAGE_EDGE:
                    image.thumbnail((DOC_IMAGE_EDGE, DOC_IMAGE_EDGE))
                if image.mode not in ("RGB", "L"):
                    image = image.convert("RGB")
                picture.replace(image, quality=DOC_IMAGE_QUALITY)
            except Exception:
                continue  # leave pictures pypdf can't re-encode as they are
        try:
            page.compress_content_streams()
        except Exception:
            pass
    writer.compress_identical_objects(remove_identicals=True, remove_orphans=True)
    out = BytesIO()
    writer.write(out)
    return out.getvalue()


def _compress_office(file) -> bytes:
    """Word/PowerPoint files are zip archives: recompress their pictures and repack tightly."""
    import zipfile
    from io import BytesIO

    file.seek(0)
    out = BytesIO()
    with zipfile.ZipFile(file) as src, zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as dst:
        for item in src.infolist():
            data = src.read(item.filename)
            lower = item.filename.lower()
            if "/media/" in lower and len(data) > 150 * 1024:
                if lower.endswith((".jpg", ".jpeg")):
                    data = _shrink_picture(data, "JPEG") or data
                elif lower.endswith(".png"):
                    data = _shrink_picture(data, "PNG") or data
            dst.writestr(item, data)
    return out.getvalue()


def compress_document(file, content_type):
    """Return a smaller copy of a big PDF/Word/PowerPoint file, or the original if it can't be shrunk."""
    if file.size <= COMPRESS_OVER:
        return file
    ext = os.path.splitext(file.name)[1].lower()
    try:
        if ext == ".pdf":
            data = _compress_pdf(file)
        elif ext in (".docx", ".pptx"):
            data = _compress_office(file)
        else:
            data = None  # old .doc/.ppt formats can't be compressed safely
    except Exception:
        logger.warning("Couldn't compress %s", file.name, exc_info=True)
        data = None
    file.seek(0)
    if data and len(data) < file.size:
        logger.info("Compressed %s from %s to %s", file.name, _mb(file.size), _mb(len(data)))
        return _as_upload(data, file.name, content_type)
    return file


def check_storage_limit(file):
    """After compressing, the file must still fit the file storage's per-file limit."""
    limit = settings.STORAGE_FILE_LIMIT_BYTES
    if limit and file.size > limit:
        raise ValidationError(
            f"{file.name} is {_mb(file.size)} even after compressing, and file storage accepts up to "
            f"{settings.STORAGE_FILE_LIMIT_MB} MB per file. Try splitting it into smaller parts, or saving it "
            f"with smaller pictures."
        )


RESOURCE_IMAGE_EXTENSIONS = (".jpg", ".jpeg", ".png", ".webp")


def prepare_document(file, allow_images=False):
    """Validate, compress if big, and check it fits storage. Returns (file, content_type).

    With allow_images (course materials), photos of notes and past questions are accepted too; they're
    resized less than post photos so their text stays readable."""
    if allow_images and os.path.splitext(file.name)[1].lower() in RESOURCE_IMAGE_EXTENSIONS:
        content_type = image_content_type(file)
        file = optimize_image(file, max_edge=settings.RESOURCE_IMAGE_MAX_EDGE)
        check_storage_limit(file)
        return file, content_type
    content_type = validate_document_upload(file)
    file = compress_document(file, content_type)
    check_storage_limit(file)
    return file, content_type


def prepare_image(file):
    """Validate, resize/strip metadata, and check it fits storage. Returns (file, content_type)."""
    content_type = image_content_type(file)
    file = optimize_image(file)
    check_storage_limit(file)
    return file, content_type

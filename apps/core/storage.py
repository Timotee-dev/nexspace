"""Cloudinary storage backend for Django 6's STORAGES setting.

Only activated when CLOUDINARY_URL is set (see settings.STORAGES).
"""
import logging
import posixpath
import uuid

from django.core.files.storage import Storage
from django.utils.deconstruct import deconstructible

logger = logging.getLogger(__name__)


@deconstructible
class CloudinaryStorage(Storage):
    folder = "nexspace"

    def _uploader(self):
        import cloudinary.uploader  # imported lazily so local dev never needs it configured

        return cloudinary.uploader

    def _public_id(self, name: str) -> str:
        return posixpath.join(self.folder, posixpath.splitext(name)[0])

    MEDIA_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".pdf"}

    def _save(self, name, content):
        from django.core.exceptions import ValidationError

        ext = posixpath.splitext(name)[1].lower()
        if ext in self.MEDIA_EXTS:
            options = {"public_id": self._public_id(name), "resource_type": "auto"}
        else:  # Word/PowerPoint: stored as raw files, which keep their extension in the public id
            options = {"public_id": posixpath.join(self.folder, name), "resource_type": "raw"}
        try:
            result = self._uploader().upload(content, overwrite=False, **options)
        except Exception as exc:  # e.g. bad credentials, file too large for the plan, network error
            logger.exception("Cloudinary upload failed for %s", name)
            raise ValidationError(
                "File storage refused the upload. If it keeps happening, the storage settings on the server "
                "need checking. (The details are in the server log.)"
            ) from exc
        if result.get("resource_type") == "raw":
            return f"raw/{result['public_id']}"
        # Store the full Cloudinary-relative path (with format) so url() is deterministic.
        return f"{result['resource_type']}/{result['public_id']}.{result.get('format', '')}".rstrip(".")

    def url(self, name):
        import cloudinary

        resource_type, _, rest = name.partition("/")
        if resource_type == "raw":
            return cloudinary.CloudinaryResource(rest, resource_type="raw").build_url(secure=True)
        public_id, _, fmt = rest.rpartition(".")
        return cloudinary.CloudinaryResource(
            public_id or rest, format=fmt or None, resource_type=resource_type
        ).build_url(secure=True)

    def exists(self, name):
        return False  # names are always unique (see get_available_name)

    def get_available_name(self, name, max_length=None):
        directory, filename = posixpath.split(name)
        ext = posixpath.splitext(filename)[1].lower()
        return posixpath.join(directory, f"{uuid.uuid4().hex}{ext}")

    def delete(self, name):
        resource_type, _, rest = name.partition("/")
        public_id = rest if resource_type == "raw" else (rest.rpartition(".")[0] or rest)
        self._uploader().destroy(public_id, resource_type=resource_type)

    def size(self, name):
        return 0


# --- Supabase Storage (S3-compatible) ---------------------------------------------------
from storages.backends.s3 import S3Storage  # noqa: E402


class SupabaseStorage(S3Storage):
    """Files in a *private* Supabase Storage bucket, over Supabase's S3-compatible API.

    Nothing is public: every link is a signed URL that expires after an hour, so materials shared
    in a department can't be passed around outside NexSpace by copying a link.
    """

    def __init__(self, **settings_overrides):
        from django.conf import settings

        options = {
            "bucket_name": settings.SUPABASE_STORAGE_BUCKET,
            "endpoint_url": settings.SUPABASE_S3_ENDPOINT,
            "region_name": settings.SUPABASE_S3_REGION,
            "access_key": settings.SUPABASE_S3_ACCESS_KEY_ID,
            "secret_key": settings.SUPABASE_S3_SECRET_ACCESS_KEY,
            "addressing_style": "path",
            "signature_version": "s3v4",
            "querystring_auth": True,
            "querystring_expire": 3600,
            "file_overwrite": False,
            "default_acl": None,
        }
        options.update(settings_overrides)
        super().__init__(**options)

    def _save(self, name, content):
        from django.core.exceptions import ValidationError

        try:
            return super()._save(name, content)
        except Exception as exc:
            logger.exception("Supabase Storage upload failed for %s", name)
            raise ValidationError(
                "File storage isn't responding right now, so the upload didn't go through. "
                "Try again in a minute. (The details are in the server log.)"
            ) from exc


def download_url(fieldfile, filename=None, as_attachment=True):
    """URL for downloading a stored file under its original name (signed, for private storage)."""
    storage = fieldfile.storage
    if isinstance(storage, S3Storage) and filename:
        from urllib.parse import quote

        disposition = "attachment" if as_attachment else "inline"
        safe = filename.replace('"', "").replace("\\", "")
        return storage.url(fieldfile.name, parameters={
            "ResponseContentDisposition": f"{disposition}; filename=\"{safe}\"; filename*=UTF-8''{quote(safe)}",
        })
    return fieldfile.url

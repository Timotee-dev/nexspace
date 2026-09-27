"""Cloudinary storage backend for Django 6's STORAGES setting.

Only activated when CLOUDINARY_URL is set (see settings.STORAGES).
"""
import posixpath
import uuid

from django.core.files.storage import Storage
from django.utils.deconstruct import deconstructible


@deconstructible
class CloudinaryStorage(Storage):
    folder = "nexspace"

    def _uploader(self):
        import cloudinary.uploader  # imported lazily so local dev never needs it configured

        return cloudinary.uploader

    def _public_id(self, name: str) -> str:
        return posixpath.join(self.folder, posixpath.splitext(name)[0])

    def _save(self, name, content):
        result = self._uploader().upload(
            content, public_id=self._public_id(name), resource_type="auto", overwrite=False
        )
        # Store the full Cloudinary-relative path (with format) so url() is deterministic.
        return f"{result['resource_type']}/{result['public_id']}.{result.get('format', '')}".rstrip(".")

    def url(self, name):
        import cloudinary

        resource_type, _, rest = name.partition("/")
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
        public_id = rest.rpartition(".")[0] or rest
        self._uploader().destroy(public_id, resource_type=resource_type)

    def size(self, name):
        return 0

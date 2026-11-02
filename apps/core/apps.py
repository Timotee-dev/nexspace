from django.apps import AppConfig


class CoreConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.core"
    label = "core"

    def ready(self):
        # Images: refuse "decompression bombs" — a tiny file that claims to be enormous and would use
        # hundreds of MB of memory to decode. 64 megapixels still allows 50 MP phone photos.
        import warnings

        from PIL import Image

        Image.MAX_IMAGE_PIXELS = MAX_IMAGE_PIXELS
        warnings.simplefilter("error", Image.DecompressionBombWarning)


MAX_IMAGE_PIXELS = 64_000_000

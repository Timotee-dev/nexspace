"""Files stored on Cloudinary couldn't be read back before v0.17, so NexAI marked them "This file couldn't be
read." and OCR marked them failed. Now that they can be read, queue them again for the scheduled job."""
from django.db import migrations


def retry(apps, schema_editor):
    Resource = apps.get_model("resources", "Resource")
    Resource.objects.filter(index_error="This file couldn't be read.").update(indexed_at=None, index_error="")
    Resource.objects.filter(ocr_status="failed", ocr_error="The file couldn't be opened for reading.").update(
        indexed_at=None, ocr_status="", ocr_error="")


class Migration(migrations.Migration):
    dependencies = [("resources", "0003_resource_ocr")]
    operations = [migrations.RunPython(retry, migrations.RunPython.noop)]

from django.core.management import call_command
from django.db import migrations


def create_cache_table(apps, schema_editor):
    # No-op unless settings use the database cache (production). Safe to run repeatedly.
    call_command("createcachetable", verbosity=0)


class Migration(migrations.Migration):
    dependencies = [("core", "0001_initial")]
    operations = [migrations.RunPython(create_cache_table, migrations.RunPython.noop)]

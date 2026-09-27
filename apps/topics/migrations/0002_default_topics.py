from django.db import migrations

DEFAULT_TOPICS = [
    ("Programming", "programming"), ("AI & Machine Learning", "ai"), ("Cybersecurity", "cybersecurity"),
    ("Web Development", "web-development"), ("Mobile Development", "mobile-development"),
    ("Data Science", "data-science"), ("Design", "design"), ("Entrepreneurship", "entrepreneurship"),
    ("Research", "research"), ("Internships", "internships"), ("SIWES", "siwes"),
    ("Scholarships", "scholarships"), ("Hackathons", "hackathons"), ("Networking", "networking"),
    ("Mathematics", "mathematics"), ("Career", "career"),
]


def create_topics(apps, schema_editor):
    Topic = apps.get_model("topics", "Topic")
    for order, (name, slug) in enumerate(DEFAULT_TOPICS):
        Topic.objects.get_or_create(slug=slug, defaults={"name": name, "sort_order": order})


class Migration(migrations.Migration):
    dependencies = [("topics", "0001_initial")]
    operations = [migrations.RunPython(create_topics, migrations.RunPython.noop)]

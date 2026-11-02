"""Course reps are now appointed per level (not per course). Existing course-scoped reps become
reps for that course's level; at most two reps are kept per level (the earliest appointed)."""
from django.db import migrations


def forwards(apps, schema_editor):
    RoleAssignment = apps.get_model("accounts", "RoleAssignment")
    kept = {}
    for ra in RoleAssignment.objects.filter(role="course_rep", course__isnull=False).select_related("course").order_by("created_at", "pk"):
        dept_id, level = ra.course.department_id, ra.course.level
        key = (dept_id, level)
        exists = RoleAssignment.objects.filter(role="course_rep", user_id=ra.user_id, department_id=dept_id,
                                               level=level, course__isnull=True).exists()
        holders = kept.setdefault(key, set(RoleAssignment.objects.filter(
            role="course_rep", department_id=dept_id, level=level, course__isnull=True).values_list("user_id", flat=True)))
        if not exists and len(holders) < 2:
            RoleAssignment.objects.create(user_id=ra.user_id, role="course_rep", department_id=dept_id, level=level,
                                          granted_by_id=ra.granted_by_id)
            holders.add(ra.user_id)
        ra.delete()


class Migration(migrations.Migration):
    dependencies = [("accounts", "0006_email_codes")]
    operations = [migrations.RunPython(forwards, migrations.RunPython.noop)]

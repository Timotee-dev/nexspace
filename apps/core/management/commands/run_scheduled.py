"""All scheduled work in one command. Run it every 10–15 minutes (Render cron job).

- Exam/test/assignment/registration reminders (3 days and 1 day before)
- Opportunity deadline reminders that students set
- Study group meeting reminders (the day before)
- Trending recalculation for every department
- NexAI indexing of new resources
- Push notification delivery
- Housekeeping: delete read notifications older than 90 days

Every step is idempotent (dedupe keys / sent flags), so running it often is safe.
"""
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone


class Command(BaseCommand):
    help = "Send reminders, recompute trending, deliver push notifications and tidy up."

    def handle(self, *args, **options):
        from apps.academics.models import Department
        from apps.discover.models import OpportunityReminder
        from apps.discover.services import compute_trending
        from apps.groups.models import StudyGroup
        from apps.notices.models import AcademicEvent
        from apps.notifications import services as notifications
        from apps.notifications.models import Category, Notification

        now = timezone.now()
        today = timezone.localdate()
        counts = {"event_reminders": 0, "opportunity_reminders": 0, "group_reminders": 0}

        # 1. Academic reminders
        for days in (3, 1):
            day = today + timedelta(days=days)
            events = AcademicEvent.objects.filter(kind__in=AcademicEvent.COUNTDOWN_KINDS, starts_at__date=day)
            for event in events.select_related("target_course"):
                prefix = f"{event.target_course.code} " if event.target_course_id else ""
                when = "tomorrow" if days == 1 else f"in {days} days"
                rows = notifications.notify(
                    notifications.audience_users(event), category=Category.ACADEMIC,
                    kind=Notification.Kind.EVENT_REMINDER,
                    text=f"{prefix}{event.get_kind_display()} {when}: {event.title}", url="/calendar/",
                    critical=event.kind in (AcademicEvent.Kind.EXAM, AcademicEvent.Kind.TEST),
                    dedupe_key=f"event:{event.pk}:{days}",
                )
                counts["event_reminders"] += len(rows)

        # 2. Opportunity reminders
        due = OpportunityReminder.objects.filter(remind_on__lte=today, sent_at__isnull=True).select_related(
            "user", "post__opportunity")
        for reminder in due:
            post = reminder.post
            if not post.is_deleted and post.opportunity.deadline and post.opportunity.deadline >= today:
                left = (post.opportunity.deadline - today).days
                notifications.notify(
                    [reminder.user], category=Category.OPPORTUNITIES, kind=Notification.Kind.OPPORTUNITY_REMINDER,
                    text=f"{post.title}: deadline {'today' if left == 0 else f'in {left} day' + ('s' if left > 1 else '')}",
                    url=post.get_absolute_url(), critical=True, dedupe_key=f"opp-reminder:{reminder.pk}",
                )
                counts["opportunity_reminders"] += 1
            reminder.sent_at = now
            reminder.save(update_fields=["sent_at"])

        # 3. Study group meetings tomorrow
        for group in StudyGroup.objects.filter(next_meeting_at__date=today + timedelta(days=1)):
            members = [m.user for m in group.memberships.select_related("user")]
            rows = notifications.notify(
                members, category=Category.ACADEMIC, kind=Notification.Kind.STUDY_GROUP,
                text=f"{group.name} meets tomorrow at {timezone.localtime(group.next_meeting_at):%H:%M}",
                url=group.get_absolute_url(), dedupe_key=f"group:{group.pk}:{group.next_meeting_at:%Y%m%d%H%M}",
            )
            counts["group_reminders"] += len(rows)

        # 4. Trending
        for dept_id in Department.objects.filter(is_active=True).values_list("id", flat=True):
            compute_trending(dept_id)

        # 5. NexAI: read any resources that haven't been indexed yet
        from apps.nexai.indexing import index_pending

        indexed = index_pending()

        # 6. Push delivery
        push = notifications.send_pending_push()

        # 7. Housekeeping
        deleted, _ = Notification.objects.filter(is_read=True, created_at__lt=now - timedelta(days=90)).delete()

        self.stdout.write(self.style.SUCCESS(f"Done: {counts}, indexed={indexed}, push={push}, cleaned={deleted}"))

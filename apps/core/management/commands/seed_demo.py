"""Create demo data for local development. Refuses to run when DEBUG is False."""
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.academics.models import Department, Faculty, Level, University
from apps.accounts.models import RoleAssignment, User
from apps.accounts.services import assign_role  # noqa: F811
from apps.accounts.services import assign_role
from apps.posts import services as posts
from apps.posts.models import Post
from apps.social.services import toggle_topic_follow, toggle_user_follow
from apps.topics.models import Topic

DEMO_PASSWORD = "nexspace-demo-2026"

DEMO_USERS = [
    # email, full name, level, role, bio, skills, interests
    ("admin@nexspace.test", "Ada Admin", Level.L400, RoleAssignment.Role.SUPER_ADMIN,
     "Keeps NexSpace running.", ["Operations"], ["career"]),
    ("deptadmin@nexspace.test", "Dele Okafor", Level.L400, RoleAssignment.Role.DEPARTMENT_ADMIN,
     "Department admin for Computer Science.", ["Planning"], ["research"]),
    ("rep@nexspace.test", "Chioma Bello", Level.L300, RoleAssignment.Role.COURSE_REP,
     "Course rep for 300 Level. Ask me about lecture materials.", ["Python", "Data structures"],
     ["programming", "ai"]),
    ("mod@nexspace.test", "Tunde Adeyemi", Level.L300, RoleAssignment.Role.MODERATOR,
     "Keeping discussions useful.", ["Community"], ["networking"]),
    ("student@nexspace.test", "Zainab Musa", Level.L300, None,
     "Building web apps and learning ML.", ["JavaScript", "Django", "UI design"],
     ["web-development", "ai", "hackathons"]),
    ("fresher@nexspace.test", "Emeka Obi", Level.L100, None, "", [], []),
]


def make_pdf(pages):
    """Build a small, valid PDF with real text (one list of lines per page) for demo resources."""
    def esc(t):
        return t.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    objects = ["<< /Type /Catalog /Pages 2 0 R >>", None, "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    kids = []
    for lines in pages:
        stream = "BT /F1 11 Tf 56 780 Td 15 TL " + " ".join(f"({esc(line)}) '" for line in lines) + " ET"
        objects.append(f"<< /Length {len(stream)} >>\nstream\n{stream}\nendstream")
        content_id = len(objects)
        objects.append(f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Contents {content_id} 0 R "
                       f"/Resources << /Font << /F1 3 0 R >> >> >>")
        kids.append(len(objects))
    objects[1] = f"<< /Type /Pages /Kids [{' '.join(f'{k} 0 R' for k in kids)}] /Count {len(kids)} >>"
    out, offsets = b"%PDF-1.4\n", []
    for i, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n{body}\nendobj\n".encode("latin-1")
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    out += "".join(f"{o:010d} 00000 n \n" for o in offsets).encode()
    out += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF".encode()
    return out


DEMO_TEXT = {
    "CSC 301 Exam 2023/2024": [
        ["CSC 301 Data Structures - First Semester Examination 2023/2024", "Answer any four questions.",
         "1a. Explain the difference between a stack and a queue. Give one application of each.",
         "1b. Write an algorithm for insertion sort and state its worst-case time complexity.",
         "2a. What is a binary search tree? Insert 50, 30, 70, 20, 40 and draw the resulting tree.",
         "2b. Describe in-order, pre-order and post-order traversal.",
         "3a. Compare merge sort and quick sort in terms of time complexity and stability.",
         "3b. Explain hashing, collisions and two collision resolution techniques.",
         "4. Using Big-O notation, analyse the complexity of binary search."]],
    "CSC 301 Test 2024/2025": [
        ["CSC 301 Mid-semester Test 2024/2025", "1. Define a linked list. How does it differ from an array?",
         "2. Implement push and pop operations for a stack using an array.",
         "3. Trace bubble sort on the list 5, 1, 4, 2, 8 and count the swaps.",
         "4. What is a circular queue and why is it useful?",
         "5. State the time complexity of searching an unsorted linked list."]],
    "Lecture 4: Sorting algorithms": [
        ["Lecture 4 - Sorting algorithms", "Sorting arranges items in order. Stable sorts keep equal items in their original order.",
         "Bubble sort repeatedly swaps adjacent items that are out of order. Worst case O(n^2).",
         "Insertion sort builds the sorted list one item at a time. Worst case O(n^2), best case O(n) on sorted input."],
        ["Merge sort divides the list in half, sorts each half and merges them. Always O(n log n) and stable.",
         "Quick sort picks a pivot and partitions the list around it. Average O(n log n), worst case O(n^2).",
         "Choosing a random pivot makes the worst case unlikely. Quick sort is not stable."]],
    "My summary notes: trees and heaps": [
        ["Summary notes - trees and heaps", "A tree is a hierarchy of nodes with one root.",
         "A binary search tree keeps smaller keys in the left subtree and larger keys in the right subtree.",
         "Search, insert and delete in a balanced BST take O(log n); an unbalanced tree can degrade to O(n).",
         "A heap is a complete binary tree where each parent is at least as large as its children (max-heap).",
         "Heaps implement priority queues. Heap sort runs in O(n log n)."]],
    "CSC 305 Exam 2023/2024": [
        ["CSC 305 Operating Systems - Examination 2023/2024", "1. Define a process and describe the process states.",
         "2. Compare first-come-first-served and round robin scheduling.",
         "3. What is a deadlock? State the four necessary conditions.", "4. Explain paging and page faults."]],
}


class Command(BaseCommand):
    help = "Seed demo university, department, topics and users (development only)."

    def add_arguments(self, parser):
        parser.add_argument("--force", action="store_true", help="Recreate demo users' passwords and roles.")

    @transaction.atomic
    def handle(self, *args, **options):
        if not settings.DEBUG:
            raise CommandError("seed_demo is for development only and will not run when DEBUG is False.")

        uni, _ = University.objects.get_or_create(
            slug="unimed", defaults={"name": "University of Medical Sciences, Ondo", "short_name": "UNIMED"}
        )
        faculty, _ = Faculty.objects.get_or_create(
            university=uni, slug="computing", defaults={"name": "Faculty of Computing"}
        )
        dept, _ = Department.objects.get_or_create(
            faculty=faculty, slug="computer-science", defaults={"name": "Computer Science", "code": "CSC"}
        )
        Department.objects.get_or_create(
            faculty=faculty, slug="information-technology",
            defaults={"name": "Information Technology", "code": "IFT"},
        )

        created = 0
        for email, name, level, role, bio, skills, interests in DEMO_USERS:
            user = User.objects.filter(email=email).first()
            if user is None:
                user = User.objects.create_user(
                    email=email, password=DEMO_PASSWORD, full_name=name, department=dept, level=level,
                    email_verified=True, onboarding_completed=True,
                )
                created += 1
            elif options["force"]:
                user.set_password(DEMO_PASSWORD)
                user.save()
            user.profile.bio = bio
            user.profile.skills = skills
            user.profile.save()
            user.profile.interests.set(Topic.objects.filter(slug__in=interests))
            if role:
                scope = None if role == RoleAssignment.Role.SUPER_ADMIN else dept
                assign_role(user=user, role=role, department=scope)
            if role == RoleAssignment.Role.SUPER_ADMIN:
                User.objects.filter(pk=user.pk).update(is_staff=True, is_superuser=True)

        self._seed_academics(uni, dept)
        if not Post.objects.filter(department=dept).exists():
            self._seed_posts(dept)
        self._seed_phase3(dept)
        self._seed_phase4(dept)

        self.stdout.write(self.style.SUCCESS(f"Demo data ready ({created} new users)."))
        self.stdout.write(f"All demo accounts use the password: {DEMO_PASSWORD}")
        for email, name, *_rest in DEMO_USERS:
            self.stdout.write(f"  {email:28} {name}")

    def _seed_posts(self, dept):
        from datetime import timedelta

        from django.core.cache import cache
        from django.utils import timezone

        users = {u.email.split("@")[0]: u for u in User.objects.filter(email__endswith="@nexspace.test")}
        topic = {t.slug: t for t in Topic.objects.all()}
        deptadmin, rep, mod, student, fresher = (
            users["deptadmin"], users["rep"], users["mod"], users["student"], users["fresher"]
        )
        official = posts.create_post(
            author=deptadmin, kind="post", is_official=True,
            body="Welcome to NexSpace! This is the official space for the Department of Computer Science. "
                 "Ask questions, share what you're building, and look out for updates here.",
        )
        question = posts.create_post(
            author=student, kind="question", topics=[topic["programming"]],
            body="What's the clearest way to explain the difference between a stack and a queue? "
                 "Preparing for the CSC 301 test and my notes are confusing.",
        )
        poll = posts.create_post(
            author=rep, kind="poll", body="When should we hold the CSC 301 revision class?",
            poll_options=["Friday 4pm", "Saturday 10am", "Sunday 5pm"],
            poll_closes_at=timezone.now() + timedelta(days=3),
        )
        posts.create_post(
            author=mod, kind="event", title="Intro to Git & GitHub workshop", topics=[topic["programming"]],
            body="Bring your laptop. We'll set up Git, make your first repo and open a pull request together. @zainab_musa is co-hosting.",
            event={"starts_at": timezone.now() + timedelta(days=5), "ends_at": None, "location": "Lab 2"},
        )
        posts.create_post(
            author=rep, kind="opportunity", title="Software engineering internship (Summer)",
            topics=[topic["internships"]], body="Open to 300 and 400 level students. Remote-friendly.",
            opportunity={"organization": "Paystack", "category": "internship", "deadline": None,
                         "apply_url": "https://paystack.com/careers", "location": "Remote"},
        )
        posts.create_post(
            author=fresher, kind="question", is_anonymous=True,
            body="Is it normal to feel lost in the first month of 100 level? Any advice on keeping up?",
        )
        posts.create_post(
            author=student, kind="post", topics=[topic["web-development"], topic["ai"]],
            body="Just deployed my first Django app. It recommends study partners based on the courses you take. "
                 "Happy to share what I learned about deploying to Render.",
        )
        answer = posts.add_comment(user=rep, post=question,
                                   body="Stack = plates in a pile: last in, first out. Queue = a line at the cafeteria: first in, first out.")
        posts.add_comment(user=mod, post=question, parent=answer, body="Great analogy. Add: stacks power the undo button.")
        posts.add_comment(user=fresher, post=official, body="Excited to be here!")
        cache.clear()  # seeding shouldn't eat the demo users' rate limits
        posts.vote_post(user=rep, post=question, value=1)
        posts.vote_post(user=mod, post=question, value=1)
        posts.vote_post(user=student, post=official, value=1)
        posts.vote_comment(user=student, comment=answer, value=1)
        posts.accept_answer(user=student, comment=answer)
        posts.vote_poll(user=student, post=poll, option_ids=[poll.poll.options.first().pk])
        toggle_user_follow(student, rep)
        toggle_topic_follow(student, topic["programming"])
        cache.clear()

    # --- Phase 3 ------------------------------------------------------------
    COURSES = [
        ("CSC 101", "Introduction to Computer Science", 100, 1, "Dr. A. Ojo"),
        ("CSC 102", "Introduction to Programming", 100, 2, "Mr. K. Balogun"),
        ("CSC 201", "Computer Programming I", 200, 1, "Dr. F. Eze"),
        ("CSC 205", "Discrete Mathematics", 200, 1, "Prof. S. Adewale"),
        ("CSC 301", "Data Structures", 300, 1, "Dr. M. Okonkwo"),
        ("CSC 305", "Operating Systems", 300, 1, "Dr. T. Ibrahim"),
        ("CSC 307", "Database Systems", 300, 1, "Mrs. O. Afolabi"),
        ("CSC 401", "Software Engineering", 400, 1, "Prof. B. Nwosu"),
    ]

    def _seed_academics(self, uni, dept):
        from apps.academics.models import AcademicSession, Course

        for name, current in (("2023/2024", False), ("2024/2025", False), ("2025/2026", True)):
            AcademicSession.objects.get_or_create(university=uni, name=name, defaults={"is_current": current})
        for code, title, level, semester, lecturer in self.COURSES:
            Course.objects.get_or_create(department=dept, code=code, defaults={
                "title": title, "level": level, "semester": semester, "lecturer": lecturer,
                "description": f"{title} for {level} level students.",
            })

    def _seed_phase3(self, dept):
        from datetime import timedelta

        from django.core.cache import cache
        from django.core.files.uploadedfile import SimpleUploadedFile
        from django.utils import timezone

        from apps.academics.models import AcademicSession, Course
        from apps.notices.models import AcademicEvent, Announcement, Audience
        from apps.notices import services as notices
        from apps.resources import services as resources
        from apps.resources.models import Resource
        from apps.spaces import services as spaces
        from apps.spaces.models import Space

        users = {u.email.split("@")[0]: u for u in User.objects.filter(email__endswith="@nexspace.test")}
        if Space.objects.filter(department=dept, kind=Space.Kind.DEPARTMENT).exists():
            return
        cache.clear()
        Space.objects.create(department=dept, kind=Space.Kind.DEPARTMENT, name="Computer Science", slug="computer-science",
                             description="Official Space for the whole department.", is_official=True, icon="💻")
        for level in (100, 200, 300, 400):
            Space.objects.create(department=dept, kind=Space.Kind.LEVEL, level=level, name=f"{level} Level",
                                 slug=f"{level}-level", description=f"Everything for {level} level students.",
                                 is_official=True)
        for name, icon, desc in (("General Discussion", "💬", "Anything and everything."),
                                 ("Programming", "⌨️", "Code, bugs and side projects."),
                                 ("AI & Machine Learning", "🤖", "Models, papers and experiments."),
                                 ("Internships & SIWES", "💼", "Placements, applications and advice.")):
            spaces.create_space(user=users["mod"], name=name, description=desc, icon=icon)

        csc301 = Course.objects.get(department=dept, code="CSC 301")
        rep = users["rep"]
        assign_role(user=rep, role=RoleAssignment.Role.COURSE_REP, course=csc301)
        for key in ("student", "rep", "mod", "deptadmin"):
            u = users[key]
            for space in Space.objects.filter(department=dept).filter(level__in=[u.level]) | Space.objects.filter(
                    department=dept, kind__in=["department", "community"]):
                spaces.join(u, space)
        for space in Space.objects.filter(department=dept, level=100) | Space.objects.filter(department=dept, kind="department"):
            spaces.join(users["fresher"], space)

        prog = Space.objects.get(department=dept, slug="programming")
        cache.clear()
        posts.create_post(author=users["student"], kind="question", space=csc301.space,
                          body="For the CSC 301 test: will linked lists and trees both be covered, or only up to stacks and queues?")
        posts.create_post(author=users["mod"], kind="post", space=prog,
                          body="Share one VS Code extension that made you faster this semester. I'll start: GitLens.")

        pdf = b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj 2 0 obj<</Type/Pages/Kids[]/Count 0>>endobj\ntrailer<</Root 1 0 R>>\n%%EOF"
        sessions = {s.name: s for s in AcademicSession.objects.all()}
        uploads = [
            (rep, csc301, "CSC 301 Exam 2023/2024", Resource.Type.PAST_QUESTION, "exam", "2023/2024",
             "First semester exam. Covers sorting, searching, trees and hashing."),
            (rep, csc301, "CSC 301 Test 2024/2025", Resource.Type.PAST_QUESTION, "test", "2024/2025",
             "Mid-semester test on stacks, queues and linked lists."),
            (rep, csc301, "Lecture 4: Sorting algorithms", Resource.Type.LECTURE_NOTE, "", "2025/2026",
             "Bubble, insertion, merge and quick sort with complexity analysis."),
            (users["student"], csc301, "My summary notes: trees and heaps", Resource.Type.LECTURE_NOTE, "", "2025/2026",
             "Condensed notes I made while revising."),
            (users["deptadmin"], Course.objects.get(department=dept, code="CSC 305"), "CSC 305 Exam 2023/2024",
             Resource.Type.PAST_QUESTION, "exam", "2023/2024", "Processes, scheduling, memory management."),
        ]
        made = []
        for uploader, course, title, rtype, exam, session, desc in uploads:
            cache.clear()
            made.append(resources.upload(
                user=uploader, course=course, title=title, resource_type=rtype, exam_type=exam,
                session=sessions[session], description=desc,
                file=SimpleUploadedFile(f"{title[:20]}.pdf", make_pdf(DEMO_TEXT[title]), content_type="application/pdf"),
            ))
        resources.rate(user=users["student"], resource=made[0], stars=5, review="Exactly what came out last year.")
        resources.rate(user=users["mod"], resource=made[0], stars=4)
        resources.rate(user=users["rep"], resource=made[3], stars=5, review="Clear and short. Thanks!")
        for u in (users["student"], users["mod"], users["deptadmin"]):
            resources.record_download(user=u, resource=made[0])

        now = timezone.now()
        admin = users["deptadmin"]
        notices.publish_announcement(user=admin, title="First semester exam timetable is out", priority="important",
                                     body="The first semester examination timetable has been released. Check your courses and report any clashes to your level adviser by Friday.",
                                     expires_at=now + timedelta(days=30))
        notices.publish_announcement(user=admin, title="300 Level: SIWES orientation on Thursday", audience=Audience.LEVEL,
                                     target_level=300, priority="urgent",
                                     body="Mandatory SIWES orientation for all 300 level students, Thursday 10am in the main auditorium.",
                                     expires_at=now + timedelta(days=7))
        notices.publish_announcement(user=rep, title="CSC 301 revision class moved", audience=Audience.COURSE,
                                     target_course=csc301, body="The revision class now holds on Saturday at 10am in Lab 1.",
                                     expires_at=now + timedelta(days=10))
        Announcement.objects.create(department=dept, title="Course registration closed", body="Registration for this session has closed.",
                                    created_by=admin, expires_at=now - timedelta(days=3))
        notices.create_event(user=rep, title="CSC 301 mid-semester test", kind=AcademicEvent.Kind.TEST,
                             audience=Audience.COURSE, target_course=csc301, starts_at=now + timedelta(days=5), location="LT 2")
        notices.create_event(user=admin, title="CSC 301 examination", kind=AcademicEvent.Kind.EXAM,
                             audience=Audience.COURSE, target_course=csc301, starts_at=now + timedelta(days=12), location="Main hall")
        notices.create_event(user=admin, title="Course registration deadline", kind=AcademicEvent.Kind.REGISTRATION,
                             starts_at=now + timedelta(days=9))
        notices.create_event(user=admin, title="Departmental week opening", kind=AcademicEvent.Kind.EVENT,
                             starts_at=now + timedelta(days=16), location="Faculty auditorium")
        notices.create_event(user=admin, title="Independence Day", kind=AcademicEvent.Kind.HOLIDAY,
                             starts_at=now + timedelta(days=20))
        cache.clear()

    def _seed_phase4(self, dept):
        from datetime import timedelta

        from django.core.cache import cache
        from django.utils import timezone

        from apps.academics.models import Course
        from apps.discover.services import compute_trending, set_reminder
        from apps.groups import services as groups
        from apps.groups.models import StudyGroup
        from apps.posts.models import Post

        if StudyGroup.objects.filter(department=dept).exists():
            return
        users = {u.email.split("@")[0]: u for u in User.objects.filter(email__endswith="@nexspace.test")}
        cache.clear()
        csc301 = Course.objects.get(department=dept, code="CSC 301")
        g = groups.create_group(user=users["student"], name="CSC 301 revision crew", course=csc301,
                                description="Past questions every Saturday.",
                                next_meeting_at=timezone.now() + timedelta(days=1, hours=2), meeting_location="Library, 2nd floor")
        groups.join(user=users["rep"], group=g)
        groups.post_message(user=users["student"], group=g, body="Let's do the 2023/2024 exam first.")
        groups.post_message(user=users["rep"], group=g, body="Sounds good. I'll bring printed copies.")
        groups.create_group(user=users["mod"], name="Algorithms study circle", is_open=False,
                            description="Invite-only, small group.")
        opp = Post.objects.filter(department=dept, kind="opportunity").first()
        if opp:
            opp.opportunity.deadline = timezone.localdate() + timedelta(days=10)
            opp.opportunity.save()
            set_reminder(user=users["student"], post=opp, days_before=7)
        compute_trending(dept.pk)
        from apps.nexai.indexing import index_pending

        index_pending(limit=100)
        cache.clear()

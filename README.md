# NexSpace — Phases 1–6

The digital home of the department. Built to the master prompt (v2, Section 60). Phase 1: setup, authentication, institutions, profiles, scoped roles, design system. Phase 2: the social layer — posts, comments, votes, polls, anonymous posting, topics, following, bookmarks, the three feeds and the NexScore ledger. Phase 3: courses, Spaces, resources and past questions, course reps, announcements and the academic calendar, plus reporting and moderation. Phase 4: notifications, global search, Explore and trending, the Opportunities section and study groups. Phase 5: the admin dashboard, analytics, the installable PWA, browser push notifications and a performance pass. Phase 6: NexAI (grounded answers from the department's own materials), personalised recommendations, and multi-department operation.

**Deploying?** Follow [DEPLOY.md](DEPLOY.md).

## Run it locally (Windows)

```powershell
cd nexspace
python -m venv venv          # or activate your shared venv
venv\Scripts\activate
pip install -r requirements-dev.txt
python manage.py migrate
python manage.py seed_demo
python manage.py runserver
```

Open http://127.0.0.1:8000. No `.env`, Postgres, Redis or Docker is needed: without a `.env` the app uses SQLite, prints emails (verification and password reset links) in the terminal, and stores uploads in `media/`.

Demo accounts (password `nexspace-demo-2026`):

| Email | Role |
|---|---|
| admin@nexspace.test | Super Admin (also Django admin at `/django-admin/`) |
| deptadmin@nexspace.test | Department Admin, Computer Science |
| rep@nexspace.test | Course Rep, Computer Science |
| mod@nexspace.test | Moderator, Computer Science |
| student@nexspace.test | Student, 300 Level |
| fresher@nexspace.test | Student, 100 Level, empty profile |

`seed_demo` also creates sample posts of every type, an anonymous question, comments, votes, a poll and follows; 8 courses, department/level/community Spaces, CSC 301 past questions and notes with ratings, announcements (including an expired one) and calendar dates with countdowns. Chioma (rep@) is course rep for CSC 301; Dele (deptadmin@) and Tunde (mod@) can use the moderation queue.

To test the real sign-up flow, create a new account at `/signup/` and copy the verification link from the terminal.

## Run the tests

```powershell
python -m pytest
```

150 tests. Phase 6: text extraction from PDF/Word/PowerPoint, indexing on upload and by the scheduled job, retrieval that only ever sees the student's own department's live resources, grounded answers with sources and the student's calendar, refusing to guess when nothing matches, daily limits, summaries, quizzes, past question insights, safe answer rendering and recommendations. Phases 4–5: every notification trigger and audience, preferences and critical bypass, reminders sent exactly once, Web Push encryption (decrypted in the test) and VAPID signatures, expired push subscriptions, grouped search without anonymous leaks or cross-department results, trending, opportunity reminders, study group rules, admin permissions and actions, PWA endpoints, a no-N+1 check on the feed, image resizing and metadata stripping. Phase 3 and moderation: course Spaces, course-rep scope, joining/muting and feeds, resource uploads, ratings, downloads, search and filters, announcement targeting and expiry, publishing permissions, countdowns, reporting rules, auto-hide at 5 reports, removal penalties, suspension, bans, and logged anonymous-author lookups. Phase 1: sign-up, verification, login lockout, password reset, onboarding, profile privacy, avatar validation, role scoping, audit logging. Phase 2: every post type, validation, anonymous authors never leaking (HTML, API, profile, Following tab), vote rules, every NexScore cap, comment depth, polls, department isolation, uploads (including disguised files), rate limits, feed pagination and the API.

## What Phase 1 includes

- **Auth:** email sign-up (matric number optional and never a login), email verification with 3-day signed links, login, logout, forgot/reset password, change password, login lockout after 5 failed attempts (15 minutes), API throttling.
- **Verification rule:** unverified users can log in, onboard, browse and edit their profile. `apps.core.permissions.IsVerifiedOrReadOnly` (API) and `verified_required` (views) block every write. Phase 2 must put these on every post, comment, vote and upload endpoint.
- **Onboarding:** interests (topics), then level. The "choose Spaces" step arrives with Spaces in Phase 3.
- **Profiles:** photo, bio, skills, links, interests, NexScore (cached total, ledger comes in Phase 2), privacy controls (profile visibility, matric number, links, Spaces), theme preference.
- **Institution model:** University → Faculty → Department, ready for multi-department and multi-university growth.
- **Roles:** Course Rep, Moderator, Department Admin, Super Admin, scoped per department, with every change audit-logged.
- **Design system and shell:** dark-first with light mode (follows the device, saved per account), mobile bottom nav, tablet icon rail, desktop three-column layout. Every page was checked at 320, 360, 390, 430, 768, 1024, 1280, 1440 and 1920px with no horizontal overflow.
- **API:** `/api/auth/` (signup, login, logout, me, resend-verification), `/api/profiles/<username>/`, `/api/academics/departments/`, `/api/academics/levels/`, `/api/topics/`. Swagger docs are at `/api/docs/` when `DEBUG=True`. Errors use the envelope `{"error": {"code", "message", "fields"}}`.

## What Phase 2 adds

- **Posts:** normal posts, questions, polls, events and opportunities, with up to 4 images and 3 PDF/Word/PowerPoint files, up to 3 topics, @mentions and link detection. File types are checked from their contents, not the file name. The composer switches fields by type (works without JavaScript), previews and removes attachments, limits topics to 3 and keeps a local draft.
- **Anonymous posting** for posts and questions. The real author is stored for moderation but never shown in pages, the API, profiles, or the Following tab, and anonymous posts earn no NexScore.
- **Official updates:** department admins can mark a post official; it shows in everyone's Department tab.
- **Comments:** nested to depth 3 (deeper replies flatten with "replying to @name"), soft delete, accepted answers on questions.
- **Voting:** up/down on posts and comments, net score only, no self-votes, one vote each, rate limited, optimistic UI.
- **NexScore ledger** (`apps/reputation`): +2 upvote, −1 downvote, +15 accepted answer; +100/day cap, +10/day per voter, no points for anonymous content or unverified voters; unvoting, unaccepting and deleting reverse points. Constants live in `apps/reputation/rules.py`.
- **Polls:** single or multiple choice, 2–6 options, optional closing time, results hidden until you vote, votes final.
- **Following** people and topics; onboarding interests count as followed topics. **Saved** page for bookmarks. Topic pages at `/t/<slug>/`.
- **Feeds:** For You (weighted by recency, votes, comments, follows, topics, level, official), Following and Department, with infinite scroll and skeleton loaders (plain "Load more" links without JS). Scoring lives in `apps/posts/feed.py`.
- **Profiles** now have Posts, Replies and About tabs, follower counts and a follow button.
- **Posts are department-private:** you only see and interact with posts from your own department.

New API endpoints: `/api/posts/` (list by tab, create with JSON or multipart), `/api/posts/<id>/` (get, delete), `.../vote/`, `.../bookmark/`, `.../poll-vote/`, `.../comments/`, `/api/comments/<id>/` (delete), `.../vote/`, `.../accept/`, `/api/bookmarks/`, `/api/users/<username>/follow/`, `/api/topics/<slug>/follow/`.

## What Phase 3 adds

- **Courses** (`/courses/`), each with an official Space created automatically. Course pages have Discussions, Resources, Past questions, Questions, People and Upcoming tabs. Joining a course's Space is how a student takes the course, so membership and enrollment can't disagree.
- **Spaces** (`/spaces/`): department, level, course and community Spaces. Only **course reps, moderators and department admins** can create Spaces; the creator becomes its moderator. Spaces **require approval to join** by default (🔒): students send a request, the Space's managers approve or decline it from the Space's Requests tab, and both sides are notified. Approval-only Spaces are private — only members see their posts and member list, and they never appear in outsiders' feeds or search. Creators can choose to make a Space open instead. **Course Spaces stay open** so any student can join a course and get its materials straight away. Managers can remove members. Leave, mute and search within a Space as before. Posts can go in a Space you've joined; joined Spaces feed the Following tab and boost For You.
- **Course reps** are assigned per course (a role scoped to that course). They moderate their course Space, their uploads carry a Course rep badge, and they can post course announcements and calendar dates. Department admins can do everything a course rep can, for every course.
- **Resources and past questions** (`/resources/`): upload a PDF, Word or PowerPoint file with course, type, exam type, session and semester. **Published immediately — no approval queue.** Search (e.g. "CSC 301 sorting"), filter by course, level, year, semester and type, sort by most useful, most downloaded, most recent or highest rated. 1–5 star ratings with optional reviews (one per person, editable, not on your own uploads). Downloads count once per person per day.
- **NexScore** now also gives +5 when a resource gets a 4–5 star rating (taken back if the rating drops) and +1 the first time each person downloads a resource.
- **Announcements** (`/announcements/`): title, message, priority, optional file, expiry, and a target — the whole department, a level, a course or a Space. Expired ones move to the archive automatically. They appear in the Department tab, the home sidebar and a mobile "up next" strip.
- **Academic calendar** (`/calendar/`) with exam countdowns ("CSC 301 Exam — 12 days"). Students only see dates for their department, level, courses and Spaces.
- **Onboarding** gains the "Choose Spaces" step, with the department, level and level courses preselected.

## Reporting and moderation

- Students can report posts, comments, resources, accounts and Spaces with a reason. Reports are private.
- Content reported by **5 different people is hidden automatically** until a moderator reviews it (the author still sees it, with a notice).
- **Moderation queue** at `/moderation/` for moderators and department admins, scoped to their department: dismiss (restores hidden content), remove (author loses 20 NexScore and any points it earned; no penalty on anonymous posts so it can't hint at who wrote them), suspend for 7 days (can read, can't post/comment/vote/upload), and — department admins only — ban (logs them out and blocks login).
- Moderators can **reveal who wrote an anonymous post**. Every lookup is written to the moderation log and the audit log.
- A permanent **moderation log** at `/moderation/log/`.
- Space moderators (community Space creators, course reps) manage their Space's membership role; department-wide reports go to department moderators.

## What Phases 4 and 5 add

- **Notifications** (`/notifications/`, bell with unread badge that refreshes every minute): replies, mentions (anonymous posts mention as "Anonymous Student"), new followers, upvote milestones (5, 10, 25…), accepted answers, new course materials and past questions (for course members), announcements (for their audience), new opportunities, study group meetings, and scheduled reminders for exams, tests, assignments and registration deadlines (3 days and 1 day before) and opportunity deadlines students set. Read/unread, mark all as read, per-category settings for in-app and push. Critical ones (urgent/important announcements, exam and test reminders, reminders you set) always appear in the app.
- **Scheduled job**: `python manage.py run_scheduled` sends reminders, recomputes trending, delivers push and cleans up read notifications older than 90 days. It's safe to run often; `render.yaml` runs it every 15 minutes as a cron job.
- **Browser push**: implemented from scratch (VAPID + aes128gcm) using only `cryptography`, so there's nothing extra to install. Off until you set keys: run `python manage.py generate_vapid_keys` and put the three values in your environment. Students then turn push on per device in Settings → Notifications. Expired subscriptions are removed automatically.
- **Search** (`/search/`): one box across courses, Spaces, resources, posts, opportunities and people, grouped with "See all", live results as you type (debounced), full-screen on mobile. "CSC301" matches "CSC 301"; searching a course code also finds people in that course. Anonymous posts are searchable by content but never by author.
- **Explore** (`/explore/`): trending posts and topics (engagement in the last 48 hours), recommended Spaces, courses for your level, open opportunities, most-downloaded resources, people to follow and quick links. Mobile navigation is now Home · Explore · Create · Spaces · Profile, as the spec describes.
- **Opportunities** (`/opportunities/`): all shared opportunities by category, open ones first by deadline, closed ones hidden unless asked for. Apply, save, share and "remind me 1, 3 or 7 days before".
- **Study groups** (`/groups/`): open or invite-only (via a private link), optional course, member limit, next meeting time, place and link, a simple discussion, and reminders the day before. If the admin leaves, the longest-standing member takes over.
- **Admin dashboard** (`/manage/`, department admins; super admins can switch department): analytics (members, active users, new registrations, posts, comments, votes, resources, downloads, reports, opportunities, 30-day charts, popular Spaces and courses, members by level) with counts only; people (search, filter, assign/remove roles including course reps for a specific course, verify email, suspend, ban, restore); courses; academic sessions; Spaces; announcements; links to the moderation queue and history. Super admins also manage departments and topics. The Django admin is no longer needed for day-to-day work.
- **PWA**: installable (manifest, icons, "Install app" in Settings → Account), a service worker that caches the app shell, keeps the last-loaded home feed for offline reading and shows an offline page for everything else. The cached feed is deleted when you log out so the next person on a shared device can't see it.
- **Performance and privacy**: the feed runs a fixed number of queries however many posts it shows (tested); uploaded images and avatars are resized to 1600px and stripped of EXIF/GPS metadata; unread counts are cached; "last active" is recorded at most every 5 minutes for analytics.

## What Phase 6 adds

- **NexAI** (`/nexai/`, and "Ask NexAI" on every course page):
  - When a resource is uploaded, its text is extracted (PDF pages, Word, PowerPoint slides) and split into passages. Small files are read immediately; the rest, and anything missed, by `run_scheduled`. `python manage.py index_resources --all` rebuilds everything. Scanned image-only PDFs and old .doc/.ppt files can't be read, and the resource page says so.
  - Questions are answered **only from passages the student is allowed to see** (their department, live resources), found by keyword ranking (BM25). Every answer cites its sources as [1], [2], each linking to the resource and page. If nothing matches, NexAI says so instead of guessing.
  - It also knows the student's own upcoming dates, so "When is my next exam?" works.
  - **Summarise** and **Quiz me** buttons on resources, and **"What comes up most?"** on a course's Past questions tab (the spec's "What topics appear most frequently in CSC 301 past questions?").
  - For assignment-style questions it explains the method instead of handing over answers.
  - **Works without an API key**: it then shows the most relevant passages instead of a written answer. Set `ANTHROPIC_API_KEY` to turn on written answers; `NEXAI_MODEL` picks the model (default Claude Haiku 4.5, the cheapest) and `NEXAI_DAILY_LIMIT` caps questions per student per day (default 40).
  - Questions aren't stored. Only a usage row (who, which feature, token counts) is kept, for limits and the "NexAI requests" figure on the admin dashboard. Conversation history lives in the browser session and "New chat" clears it.
- **Recommendations**: "For your courses" on the home sidebar (well-rated materials from your courses you haven't downloaded yet) and "People you may know" (classmates who share the most courses with you).
- **Multiple departments and universities**: every department is fully separate — posts, Spaces, courses, resources, search, NexAI and moderation never cross departments. Super admins add departments at `/manage/departments/` and switch between them in the dashboard.

## Deliberately not built yet

Sharing between departments (faculty-wide Spaces, cross-department search); semantic (embedding) search for NexAI — keyword ranking is used so there's no extra service to run; OCR for scanned PDFs; real-time delivery over WebSockets (notifications poll every minute); editing posts and reposts; email digests. Reposts and editing posts are also not built yet. Mentions are stored so Phase 4 notifications can use them. Navigation only shows pages that exist, so there are no dead buttons. Use the admin dashboard at `/manage/` for courses, sessions, Spaces, roles and accounts. The Django admin at `/django-admin/` remains for emergencies.

## Deploying to Render

1. Push to GitHub and create a Blueprint from `render.yaml`, or create a web service with the same build/start commands.
2. Set `DATABASE_URL` (Supabase pooler string for the right region), `BREVO_API_KEY`, `DEFAULT_FROM_EMAIL`, `CLOUDINARY_URL`, `SITE_URL`, `ALLOWED_HOSTS` and `CSRF_TRUSTED_ORIGINS` (e.g. `https://nexspace.onrender.com`).
3. Create the real department so students can sign up, then a superuser:

```bash
python manage.py bootstrap_institution --university "University of Medical Sciences, Ondo" --short UNIMED --faculty "Faculty of Computing" --department "Computer Science" --code CSC
python manage.py createsuperuser
```

`seed_demo` refuses to run when `DEBUG=False`.

## Project layout

```
config/            settings, urls, wsgi
apps/core/         audit log, Brevo email backend, Cloudinary storage, upload validation,
                   API error envelope, verified-write permission, login rate limiter, home
apps/academics/    University, Faculty, Department, levels
apps/topics/       platform-wide topics (seeded by migration)
apps/accounts/     User, Profile, RoleAssignment, auth views, onboarding, settings, API
apps/posts/        posts, comments, votes, polls, attachments, bookmarks, feeds, composer
apps/spaces/       Spaces, memberships (and course enrollment), courses pages
apps/resources/    resources, past questions, ratings, downloads, search
apps/notices/      announcements and the academic calendar
apps/moderation/   reports, moderation queue, actions and log
apps/notifications/ notifications, preferences, Web Push (webpush.py)
apps/search/       global search
apps/discover/     Explore, trending, Opportunities and reminders
apps/groups/       study groups
apps/manage/       admin dashboard and analytics
apps/nexai/        NexAI: text extraction, indexing, retrieval, Anthropic client, features
apps/social/       following people and topics
apps/reputation/   NexScore ledger and rules
templates/         layouts, partials (field, avatar, icons…), pages, emails
static/css/app.css design tokens + components
static/js/app.js   theme, toasts, password reveal, busy buttons, avatar preview
static/js/feed.js  votes, bookmarks, follows, share, replies, infinite scroll
static/js/composer.js  poll options, attachment previews, topic limit, drafts
static/js/push.js  turning push on/off per device
templates/pwa/sw.js  the service worker
tests/             pytest suite
```

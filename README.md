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

Demo staff (same password): `lecturer@` (Dr. Funmi Adebayo, teaches CSC 301 and 305), `adviser@` (Mr. Kunle Ojo, 300 Level adviser), `exams@` (Mrs. Grace Eze, exam officer), `newstaff@` (a lecturer still waiting for verification); `deptadmin@` is the HOD, Prof. Dele Okafor.

`seed_demo` also creates sample posts of every type, an anonymous question, comments, votes, a poll and follows; 8 courses, department/level/community Spaces, CSC 301 past questions and notes with ratings, announcements (including an expired one) and calendar dates with countdowns. Chioma (rep@) is course rep for CSC 301; Dele (deptadmin@) and Tunde (mod@) can use the moderation queue.

To test the real sign-up flow, create a new account at `/signup/` and copy the verification link from the terminal.

## Run the tests

```powershell
python -m pytest
```

222 tests. Phase 6: text extraction from PDF/Word/PowerPoint, indexing on upload and by the scheduled job, retrieval that only ever sees the student's own department's live resources, grounded answers with sources and the student's calendar, refusing to guess when nothing matches, daily limits, summaries, quizzes, past question insights, safe answer rendering and recommendations. Phases 4–5: every notification trigger and audience, preferences and critical bypass, reminders sent exactly once, Web Push encryption (decrypted in the test) and VAPID signatures, expired push subscriptions, grouped search without anonymous leaks or cross-department results, trending, opportunity reminders, study group rules, admin permissions and actions, PWA endpoints, a no-N+1 check on the feed, image resizing and metadata stripping. Phase 3 and moderation: course Spaces, course-rep scope, joining/muting and feeds, resource uploads, ratings, downloads, search and filters, announcement targeting and expiry, publishing permissions, countdowns, reporting rules, auto-hide at 5 reports, removal penalties, suspension, bans, and logged anonymous-author lookups. Phase 1: sign-up, verification, login lockout, password reset, onboarding, profile privacy, avatar validation, role scoping, audit logging. Phase 2: every post type, validation, anonymous authors never leaking (HTML, API, profile, Following tab), vote rules, every NexScore cap, comment depth, polls, department isolation, uploads (including disguised files), rate limits, feed pagination and the API.

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

## Editing, reposts, direct messages, digests and live updates (v0.9)

- **Edit posts and comments:** "Edit post" in the post menu, "Edit" on your comments. Edited items show "· Edited". Every earlier version is kept, and the author and moderators can open **Edit history** — so nobody can quietly change a post after it's been reported. Poll questions lock once anyone has voted; kind, anonymity, Space and attachments never change. Newly @mentioned people are notified.
- **Reposts and quotes:** the repost button (with a count) offers **Repost / Undo repost** or **Quote**. It updates instantly. Plain reposts appear as the original post with a "<name> reposted" line in the Following feed and on profiles (For You never shows the same post twice); quotes embed the original. Reposting a repost reposts the original. Anonymous originals stay anonymous; posts in private 🔒 Spaces can't be reposted; if the original is deleted the repost disappears and quotes show "This post isn't available". The original author is notified.
- **Direct messages (/messages/):** one-to-one chats with people in your department. Start one with **Message** on a profile. Unread badges in the sidebar and bottom bar (Messages replaced Profile there — Profile is in the drawer). Settings → Privacy → **Who can send you direct messages** (anyone in my department / only people I follow / nobody; department admins can still reach you officially) and a **Blocked people** list. In a chat: mute, remove from inbox, block, report the person, delete your own messages, report a message (moderators only ever see a message if it's reported). Unverified and suspended accounts can't send. One notification per conversation per 10 minutes so a burst of messages isn't a burst of pings.
- **Email digests:** a summary email — upcoming exams and deadlines, unread messages and notifications, new announcements, new materials in your courses, popular posts — **weekly by default** (Mondays from 7am), or daily, or off (Settings → Notifications → Summary email). Nobody gets an empty digest. At most `DIGEST_DAILY_CAP` (250) go out a day to stay under Brevo's free 300/day; the rest go on the next run. Every digest has a one-click unsubscribe link (it asks for a real click, so email link-scanners can't unsubscribe people).
- **Live updates without paid servers:** chats update every 3 seconds, notification and message badges every 15 seconds, and "Show N new posts" / "Show N new comments" bars appear on the feed and posts — all without refreshing, and nothing runs while the tab is in the background. (True WebSockets need a paid always-on server plus Redis; this works on the free plan. It can be swapped for WebSockets later without changing the pages.)

## Group chats, photos and files in messages (v0.10)

- **Group chats:** Messages → **New group**. Name it and pick people from your department (searchable list), up to 50. The creator is the group admin; admins rename the group, add and remove people, and make others admins. Anyone can leave; if the last admin leaves, the longest-standing member becomes admin. Changes show as small lines in the chat ("Ada added Bayo"). You can only add people you're allowed to message (same department, their "who can message me" setting, and blocks all apply).
- **Photos and files in messages** (one-to-one and groups): the paperclip attaches up to 4 at a time — photos (resized, location data removed) and PDF, Word or PowerPoint files (same size limit as other uploads). They appear live in the chat. Only people in the conversation can open them; deleting a message deletes its files too.
- Group messages notify everyone in the group (bundled, and not if they've muted it). Reporting works the same as for one-to-one messages: only people in the conversation can report a message.

## Lecturer sign-up fix (v0.9.2)

- Lecturers can sign up even when their department has no courses on NexSpace yet (the course list is optional, and says so when it's empty). The course list follows the department picked.
- Whoever verifies a lecturer (Platform → Staff to verify) ticks the courses they'll be lecturer for — correcting or adding to what the lecturer picked. Courses can also be added later in Manage → People → Assign role → Lecturer.

## Supabase Storage (v0.9.1)

- Optional: files can live in **Supabase Storage** instead of Cloudinary (only used if the `SUPABASE_S3_*` variables are set) (the same project as the database): up to **50 MB per file** on the free plan, in a **private** bucket. Every download or image is a signed link that expires after an hour, so course materials can't be passed around outside NexSpace by copying a link. Downloads keep their original file names.
- Cloud downloads now go straight from storage to the student instead of through the Render server, which keeps the free server fast and avoids running out of memory on big files.
- Clearer upload errors: "Files must be 50 MB or smaller" for size, and a separate "File storage isn't responding" message (with the real cause in the server log) for storage problems.
- The upload limit shown on the compose and upload pages follows the real limit. Server request timeout raised to 180 seconds for big uploads on slow connections.
- Set up: DEPLOY.md section 4. Cloudinary still works if you prefer it.

## Edits, reposts, messages, digests and live updates (v0.9)

- **Editing:** authors can edit their posts ("Edit post" in the menu) and comments. Edited items show "Edited"; every earlier version is kept and visible to the author and moderators ("Edit history"), so nobody can quietly change a post after it's reported. A poll's question locks once anyone has voted; kind, anonymity, Space and attachments never change. Newly @mentioned people are notified.
- **Reposts and quotes:** a repost button with a count (Repost / Undo repost / Quote), updating without a page reload. Plain reposts appear in the Following feed and on profiles as the original post with "<name> reposted"; they never duplicate a post in For You. Quotes embed the original. Anonymous posts stay anonymous; posts in private 🔒 Spaces can't be reposted. Deleted or hidden originals show "This post isn't available".
- **Direct messages (/messages/):** one-to-one chats inside a department. **Message** button on profiles, inbox with unread dots, chat bubbles that update live every 3 seconds, Enter to send on computers. "Who can message you" setting (anyone in my department / people I follow / nobody); department and platform admins can still reach people for official matters. Block and unblock (from a profile, a chat, or Settings → Privacy), mute, remove from inbox, delete your own messages. Messages are private: moderators only see a message if someone in the chat reports it. Messages replaced Profile in the phone's bottom bar (Profile is in the slide-out menu).
- **Email digests:** a weekly (default) or daily summary — upcoming exams and deadlines, unread messages and notifications, new announcements, new course materials and popular posts. Nothing is sent when there's nothing new. Choose in Settings → Notifications; every digest has a one-click unsubscribe (it asks for a real click, so email link-scanners can't unsubscribe people). Sending stops at `DIGEST_DAILY_CAP` (250) a day to stay inside Brevo's free 300/day and continues on the next run.
- **Live updates without WebSockets:** notification and message badges refresh every 15 seconds, chats every 3 seconds, and "Show N new posts" / "Show N new comments" bars appear without refreshing. Nothing runs while the tab is hidden. (True WebSockets would need a paid server plus Redis; this costs nothing on the free plan.)
- **Verification codes:** Brevo's free plan wraps every email link in click tracking, which can hang on phones. Verification emails now carry a **6-digit code** (also in the subject line): tap **Enter code** on the yellow banner and type it. The link is also printed as plain text, which Brevo doesn't rewrite. Codes last 30 minutes, are single-use, and lock after 5 wrong tries. Password reset emails also include the plain-text link.

## Platform owner, platform dashboard and phone drawer (v0.8)

- **Platform owner:** the accounts in `PLATFORM_OWNER_EMAILS` (default `arifalotimothy@gmail.com`) are always platform (super) admins. Rights switch on automatically once the email is **verified** (so nobody can claim them by signing up with that address first), and are restored on every login and every `migrate` if anyone removes them. Nobody else can suspend or ban the owner from inside NexSpace.
- **Staff verification** is done by platform admins by default (`STAFF_VERIFICATION=platform`). HODs see who is waiting but can't verify; set `STAFF_VERIFICATION=department` to let HODs verify their own staff again.
- **Platform dashboard at /platform/** (platform admins only):
  - **Overview:** system health (database, cache, email, file storage, push, NexAI, production mode, when scheduled jobs last ran) with a "Run scheduled jobs now" button; totals across NexSpace; staff to verify; latest activity; every department with members, activity, posts, open reports and pending staff.
  - **Staff to verify:** every department's staff sign-ups, verify or reject with a reason.
  - **People:** search every account in every department (name, email, username, matric number), filter by staff, admins, suspended, banned, unverified, no department; jump to manage any of them.
  - **Activity:** one timeline of sign-ups, posts, comments, uploads, reports, moderation and admin actions, filterable by department and type. Anonymous posts stay anonymous; revealing an author is a separate, logged action.
  - **Audit log:** every sensitive action in plain English.
  - **Database:** every table with its row count, each opening in the database admin (/django-admin/) where any row can be searched, edited or deleted. Every model is registered there.
- **Sign-in lasts 30 days.**
- **Phones:** the sidebar is hidden and slides in from the left like X — swipe right anywhere (except on horizontally scrolling rows like tabs), or tap your photo in the top bar. Swipe left, tap outside, press Esc or pick a link to close it. The top bar is now just your photo, the logo, search and notifications.

## Staff accounts and dashboards (v0.7)

- **Sign up as staff:** the sign-up form asks "I am a Student / Staff". Staff choose a position — **HOD, Lecturer, Level Adviser or Exam Officer** — a title (Prof., Dr., Mrs. …), an optional staff ID, and lecturers tick the courses they teach, level advisers the level they advise.
- **Verification first:** a staff account starts *pending*. It works like a normal account but has no staff powers and no badge until the HOD or a department admin verifies it at **/manage/staff/** (or from their dashboard). They get notified either way; a rejection can include a reason. Nobody can verify themselves. In a brand-new department, super admins verify the first HOD.
- **What each position can do once verified:**
  - **HOD** → full department admin (everything in /manage/, moderation, verifying staff, announcements to anyone).
  - **Lecturer** → for their courses: materials published with a "Lecturer" badge, course announcements, test/assignment/exam dates, managing the course Space and approving its members.
  - **Level Adviser** → their level: announcements and dates for that level, managing the level Space.
  - **Exam Officer** → exam and test dates for any course or level, department-wide dates and announcements.
  - Staff can create Spaces. Their name shows with their title and a position badge on posts, comments and their profile, which also lists the courses they teach, their office and office hours (editable in Settings).
- **Personal dashboard at /dashboard/** — people with a role land there after logging in. It shows a section for every role they hold:
  - **HOD:** members, weekly activity, posts and open reports; staff waiting to verify (one-click verify); courses with no lecturer or course rep; quick actions.
  - **Lecturer / course rep:** a card per course (students, materials, downloads in 30 days, unanswered questions, next date) with Upload / Announce / Add a date buttons, plus the questions still waiting for an answer.
  - **Level adviser:** students in their level on NexSpace, how many verified their email and were active this week, who hasn't verified yet, upcoming dates and active notices.
  - **Exam officer:** exams and tests in the next 60 days and every course with no exam date yet (with "Add exam").
  - **Everyone who manages a Space:** people waiting to join. **Moderators:** open reports.
- Department admins can also give any account these roles by hand in /manage/ → People (lecturer for a course, level adviser for a level, exam officer).

## Audit fixes (v0.6.2)

A full review of the app found and fixed: a privacy leak (profile Replies showed comments from private Spaces), department admins getting "not found" on course announcements, accounts created with `createsuperuser` breaking pages because they had no department (they're now asked for one), a plain-white 403 page, accessibility problems (colour contrast in both themes, unlabelled topic checkboxes, invalid list markup — the key pages now pass automated WCAG 2 AA checks), Word/PowerPoint files losing their extension on Cloudinary, uploads over Cloudinary's free 10 MB limit crashing (now a friendly error and a 10 MB limit), login lockouts and rate limits not being shared across server processes (now a database cache), stale database connections (health checks on). Added: account deletion (Settings → Account), `/privacy/` and `/guidelines/` pages linked from sign-up, the NexSpace logo in emails, a `/admin` shortcut to the dashboard, no offline mode on localhost, and a protected `/internal/run-scheduled/` URL so a free scheduler can replace the paid Render cron job.

## Deliberately not built yet

Sharing between departments (faculty-wide Spaces, cross-department search); semantic (embedding) search for NexAI — keyword ranking is used so there's no extra service to run; OCR for scanned PDFs; true WebSocket push (live updates use short polling instead). Navigation only shows pages that exist, so there are no dead buttons. Use the admin dashboard at `/manage/` for courses, sessions, Spaces, roles and accounts. The Django admin at `/django-admin/` remains for emergencies.

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

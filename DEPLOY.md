# Deploying NexSpace

This takes about an hour the first time. You need accounts on GitHub, Render, Supabase, Brevo and Cloudinary. Anthropic (NexAI) is optional.

## 1. Before you start (on your laptop)

```powershell
pip install -r requirements-dev.txt
python -m pytest            # everything should pass on Windows before you deploy
python manage.py generate_vapid_keys   # copy the three lines somewhere safe
```

Push the project to a **private** GitHub repository. Never commit `.env`.

## 2. Database — Supabase

1. Create a project. Pick the region closest to your Render region.
2. Project Settings → Database → Connection string → **Transaction pooler** (port 6543). Copy it and put your database password in it.
3. Double-check the region in the pooler host (e.g. `aws-0-eu-west-2.pooler.supabase.com`) matches the project's region. A mismatch is the most common cause of "could not connect" errors on Render.

This is your `DATABASE_URL`.

## 3. Email — Brevo

1. Senders & domains: add and verify the address you'll send from (ideally on your own domain, with the DNS records Brevo gives you, so verification emails don't land in spam).
2. SMTP & API → API keys → create a key. That's `BREVO_API_KEY`.
3. Security → Authorised IPs: **turn IP blocking off** for this key. Render's outgoing IP changes on each deploy.

NexSpace sends over Brevo's HTTPS API, not SMTP (Render blocks SMTP ports). You only need `BREVO_API_KEY` and `DEFAULT_FROM_EMAIL` (the exact sender you verified) on Render — no `EMAIL_HOST`/`EMAIL_PORT` variables.

Brevo's free plan rewrites every link in an email into a tracking link, and that redirect can hang on some phones. NexSpace works around it: verification emails include a 6-digit code (users tap **Enter code** on the banner), and links are also printed as plain text, which Brevo leaves alone.

## 4. Files — Supabase Storage (recommended) or Cloudinary

**Supabase Storage** (same Supabase project as your database): files up to 50 MB each, 1 GB total on the free plan, kept in a *private* bucket — every download is a signed link that expires after an hour.

1. Supabase → **Storage** → **New bucket** → name `nexspace`, leave **Public bucket OFF** → Create.
2. **Project Settings → Storage → S3 Connection** (or Storage → Settings → S3 access): copy the **Endpoint** (`https://<project-ref>.supabase.co/storage/v1/s3`) and the **Region**.
3. On the same page, **New access key** → copy the **Access key ID** and **Secret access key** (the secret is shown once).
4. Add these on Render:

| Variable | Value |
|---|---|
| `SUPABASE_S3_ENDPOINT` | the endpoint |
| `SUPABASE_S3_REGION` | the region, e.g. `eu-central-1` |
| `SUPABASE_S3_ACCESS_KEY_ID` | the access key ID |
| `SUPABASE_S3_SECRET_ACCESS_KEY` | the secret |
| `SUPABASE_STORAGE_BUCKET` | `nexspace` (only if you named it differently) |

When these are set, NexSpace uses Supabase Storage and allows 50 MB per file (change with `MAX_DOCUMENT_MB`; Supabase's free plan caps single files at 50 MB). You can then delete `CLOUDINARY_URL`.

**Cloudinary** (alternative): public links and 10 MB per file on the free plan. Dashboard → copy the **API environment variable** (`cloudinary://...`) into `CLOUDINARY_URL`, and in Settings → Security tick "Allow delivery of PDF and ZIP files".

## 5. Render

1. New → **Blueprint** → pick your repo. Render reads `render.yaml` and creates the web service and the `nexspace-scheduled` cron job.
2. Set these environment variables on the **web service**:

| Variable | Value |
|---|---|
| `DATABASE_URL` | Supabase pooler string |
| `BREVO_API_KEY` | from Brevo |
| `DEFAULT_FROM_EMAIL` | `NexSpace <no-reply@yourdomain>` (the verified sender) |
| `SUPABASE_S3_*` | from Supabase Storage (section 4) — or `CLOUDINARY_URL` instead |
| `SITE_URL` | `https://your-app.onrender.com` (or your domain) — used in email links |
| `ALLOWED_HOSTS` | `your-app.onrender.com,yourdomain.com` |
| `CSRF_TRUSTED_ORIGINS` | `https://your-app.onrender.com,https://yourdomain.com` |
| `VAPID_PUBLIC_KEY`, `VAPID_PRIVATE_KEY`, `VAPID_SUBJECT` | from step 1 (`VAPID_SUBJECT=mailto:you@yourdomain`) |
| `ANTHROPIC_API_KEY` | optional — turns on NexAI's written answers |
| `CRON_SECRET` | optional — enables the free scheduler URL (section 5b) |
| `PLATFORM_OWNER_EMAILS` | optional — defaults to `arifalotimothy@gmail.com`; comma-separate to add more owners |
| `DIGEST_DAILY_CAP` | optional — most summary emails per day (default 250; Brevo free allows 300 in total) |
| `DIGEST_HOUR` | optional — local hour digests start going out (default 7) |
| `STAFF_VERIFICATION` | optional — `platform` (default: you verify all staff) or `department` (HODs verify) |

`SECRET_KEY` is generated for you. `DEBUG` is already `False`.

3. Give the **cron job** the same `DATABASE_URL`, `CLOUDINARY_URL`, `BREVO_API_KEY`, `VAPID_*`, `SECRET_KEY` and `ANTHROPIC_API_KEY` values (it runs reminders, NexAI indexing and push delivery, so it needs the same access as the web service).

4. Deploy. The build runs `collectstatic` and `migrate` automatically.

## 5b. Scheduled jobs for free (cron-job.org)

Render cron jobs cost at least $1/month. Instead, let a free scheduler call NexSpace every 15 minutes:

1. Add an environment variable to the web service: `CRON_SECRET` = a long random string (e.g. from Render's **Generate** button). Save.
2. Create a free account at **cron-job.org** → **Create cronjob**:
   - URL: `https://your-app.onrender.com/internal/run-scheduled/`
   - Schedule: every 15 minutes
   - Advanced → Headers → add `Authorization` with the value `Bearer <your CRON_SECRET>`
   - Save, then use **Test run**. You should get `{"status": "ok", ...}`.
3. If you use this, delete the `- type: cron` block from `render.yaml` (or don't create the cron job).

This sends exam and deadline reminders, the weekly summary emails, delivers push notifications, reads new uploads for NexAI and recomputes trending. The URL returns "not found" to anyone without the secret. On the free plan the first call after a quiet period may time out while the server wakes up; the next one works.

## 6. First-time setup (Render → web service → Shell)

```bash
python manage.py bootstrap_institution --university "University of Medical Sciences, Ondo" --short UNIMED --faculty "Faculty of Computing" --department "Computer Science" --code CSC
python manage.py createsuperuser
```

Easier: just sign up on the live site with `arifalotimothy@gmail.com` and click the verification email. That account becomes the platform admin automatically, so you don't need `createsuperuser` at all.

Then log in, and in `/manage/`:

1. **People** → give your department head (or yourself) the *Department Admin* role.
2. **Sessions** → add the current academic session and mark it current.
3. **Courses** → add every course (each gets its official Space automatically).
4. **Spaces** → add the department Space, one per level, and a few community Spaces (General Discussion, Internships & SIWES…).
5. After students sign up: **People** → assign each course rep to their course, and one or two moderators.

Do **not** run `seed_demo` in production — it refuses to anyway.

## 7. Custom domain (optional)

Render → Settings → Custom domains → add it, then create the CNAME/A records your registrar asks for. Update `SITE_URL`, `ALLOWED_HOSTS` and `CSRF_TRUSTED_ORIGINS`, and redeploy.

## 8. Check it works

- Sign up with a real email → the verification email arrives and the link works.
- Upload a PDF to a course → it downloads, and after a minute "Summarise" appears (if NexAI is on).
- Settings → Notifications → turn on push → publish an urgent announcement from another account → the phone buzzes within 15 minutes.
- `/manifest.webmanifest` loads and Chrome offers "Install app".
- Render → cron job → Logs shows `Done: {...}` every 15 minutes.

## 9. Free-plan limits to know

- **Render free** sleeps after 15 minutes without visitors; the next visit takes about a minute.
- **Supabase free** pauses a project after about a week with no activity. Restore it from the Supabase dashboard; your data is kept. The every-15-minutes scheduler above counts as activity.
- **Cloudinary free** rejects files over 10 MB, so NexSpace limits uploads to 10 MB. On a paid Cloudinary plan, raise it with `MAX_DOCUMENT_MB=20`.
- Login lockouts and rate limits are stored in the database (a cache table created automatically by `migrate`), so they work across all server processes.

## 10. Keep it safe

- Supabase: turn on daily backups (Pro plan) or schedule `pg_dump` exports.
- Render: add a health check path of `/` and set up an alert email for failed deploys.
- Rotate `BREVO_API_KEY`, `CLOUDINARY_URL`, `ANTHROPIC_API_KEY` and the database password if anyone who had them leaves.
- Review and edit `templates/core/privacy.html` and `templates/core/guidelines.html` (live at `/privacy/` and `/guidelines/`) before inviting students — add your contact details and your department's own rules.

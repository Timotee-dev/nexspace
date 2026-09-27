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

NexSpace sends over Brevo's HTTPS API, not SMTP (Render blocks SMTP ports).

## 4. Files — Cloudinary

Dashboard → copy the **API environment variable** (`cloudinary://...`). That's `CLOUDINARY_URL`.

## 5. Render

1. New → **Blueprint** → pick your repo. Render reads `render.yaml` and creates the web service and the `nexspace-scheduled` cron job.
2. Set these environment variables on the **web service**:

| Variable | Value |
|---|---|
| `DATABASE_URL` | Supabase pooler string |
| `BREVO_API_KEY` | from Brevo |
| `DEFAULT_FROM_EMAIL` | `NexSpace <no-reply@yourdomain>` (the verified sender) |
| `CLOUDINARY_URL` | from Cloudinary |
| `SITE_URL` | `https://your-app.onrender.com` (or your domain) — used in email links |
| `ALLOWED_HOSTS` | `your-app.onrender.com,yourdomain.com` |
| `CSRF_TRUSTED_ORIGINS` | `https://your-app.onrender.com,https://yourdomain.com` |
| `VAPID_PUBLIC_KEY`, `VAPID_PRIVATE_KEY`, `VAPID_SUBJECT` | from step 1 (`VAPID_SUBJECT=mailto:you@yourdomain`) |
| `ANTHROPIC_API_KEY` | optional — turns on NexAI's written answers |

`SECRET_KEY` is generated for you. `DEBUG` is already `False`.

3. Give the **cron job** the same `DATABASE_URL`, `CLOUDINARY_URL`, `BREVO_API_KEY`, `VAPID_*`, `SECRET_KEY` and `ANTHROPIC_API_KEY` values (it runs reminders, NexAI indexing and push delivery, so it needs the same access as the web service).

4. Deploy. The build runs `collectstatic` and `migrate` automatically.

## 6. First-time setup (Render → web service → Shell)

```bash
python manage.py bootstrap_institution --university "University of Medical Sciences, Ondo" --short UNIMED --faculty "Faculty of Computing" --department "Computer Science" --code CSC
python manage.py createsuperuser
```

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

## 9. Keep it safe

- Supabase: turn on daily backups (Pro plan) or schedule `pg_dump` exports.
- Render: add a health check path of `/` and set up an alert email for failed deploys.
- Rotate `BREVO_API_KEY`, `CLOUDINARY_URL`, `ANTHROPIC_API_KEY` and the database password if anyone who had them leaves.
- Publish community rules and a privacy notice before inviting students (what's stored, what "anonymous" means, how reports work).

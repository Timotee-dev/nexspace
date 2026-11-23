"""Vercel build step (see pyproject.toml → [tool.vercel.scripts]).

Vercel runs collectstatic on its own. This adds what Render's build command did:
- apply database migrations
- create or repair the platform owner account from OWNER_EMAIL / OWNER_PASSWORD

Only on production builds: preview builds (other branches) share the same database, so they must not
change it. Set RUN_MIGRATIONS_ON_PREVIEW=1 if your previews use their own database.
"""
import os
import subprocess
import sys


def run(*args):
    print("$ python manage.py", " ".join(args), flush=True)
    subprocess.run([sys.executable, "manage.py", *args], check=True)


def main():
    env = os.environ.get("VERCEL_ENV", "")
    if not os.environ.get("DATABASE_URL", "").strip():
        sys.exit("DATABASE_URL is not set. Add it in Vercel → Project → Settings → Environment Variables.")
    run("check", "--deploy", "--fail-level", "ERROR")
    if env == "production" or os.environ.get("RUN_MIGRATIONS_ON_PREVIEW") == "1":
        run("migrate", "--noinput")
        run("setup_owner")
    else:
        print(f"Skipping migrations on a {env or 'local'} build (they only run for production).", flush=True)


if __name__ == "__main__":
    main()

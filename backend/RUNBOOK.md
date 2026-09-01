# Runbook — Hephzibah Luxe backend

Command reference for operating this Django + DRF project. All commands run from
`backend/` with the virtualenv active.

**There is no Celery here any more** — no worker, no beat, no broker. Deferred
work runs in a thread pool inside the web process and everything scheduled runs
as `manage.py run_scheduled <group>` from platform cron. If you are looking for
the old `celery -A config worker` / `beat` commands, read
[`docs/adr/0001-remove-celery.md`](docs/adr/0001-remove-celery.md) first — the
reasoning, what replaced each piece, and what it cost.

## Environment

```powershell
# Activate the virtualenv (Windows PowerShell)
.\venv\Scripts\Activate.ps1
# …or call the interpreter directly without activating:
.\venv\Scripts\python.exe manage.py <command>
```

Config comes from `backend/.env` (see `.env.example` for the full list). Required
keys include `DATABASE_URL`, `BREVO_API_KEY` + the 13 `BREVO_TEMPLATE_*` vars,
`CORS_ALLOWED_ORIGINS`, and `FRONTEND_BASE_URL`. `CACHE_REDIS_URL` is required
whenever `DEBUG=False`.

`REDIS_URL`, `CELERY_BROKER_URL` and `CELERY_RESULT_BACKEND` are **no longer
read**. Leaving them set in an old `.env` is harmless (nothing looks at them), but
delete them so nobody assumes a broker exists.

## Fresh database / new environment

After pointing `DATABASE_URL` at a new/empty database (make sure the rest of
`.env` is filled in first — the app fails fast at boot on any missing required
key), bootstrap it in order:

```powershell
python manage.py migrate               # 1. create all tables + run the data migrations
                                       #    that seed NotificationType / ScheduledTask /
                                       #    Brevo ServiceHealthState rows
python manage.py createsuperuser       # 2. create an admin login
python manage.py collectstatic --noinput   # 3. PRODUCTION only — gather admin static for WhiteNoise to serve
```

`migrate` is idempotent — safe to re-run on every deploy.

There is **no schedule to seed**. `seed_periodic_tasks` is gone along with
`django-celery-beat`: task timing now lives in each cron service's schedule
(each Render cron job's **Schedule** field), and the per-task on/off switches are seeded by data
migration into `Scheduled Task Settings` as before. Run
`python manage.py run_scheduled --list` to see the shipped groups and what is in
each.

Redis needs nothing here (no migrate/seed) — all tables live in Postgres.

## Deploying on Render

There is **no `render.yaml`** — all four services are created by hand in the
Render dashboard. The only deploy-relevant file in the repo is `.python-version`
(3.12, matching the dev venv). **Four services off this one repo — one web and
three cron:**

| Service | Type | Command | Schedule |
|---|---|---|---|
| **hephzibah-luxe-api** | Web Service | `gunicorn config.wsgi …` | — (always on) |
| **cron-notify** | Cron Job | `run_scheduled notification_retry` | `*/10 * * * *` |
| **cron-daily** | Cron Job | `run_scheduled daily_maintenance` | `0 8 * * *` |
| **cron-weekly** | Cron Job | `run_scheduled weekly_maintenance` | `0 3 * * 1` |

Render cron schedules are **UTC**, same as the Railway ones they replace, so the
expressions carried over unchanged. A cron job runs its command and **exits**, so
it bills execution time rather than 24/7 — that is where the money came back when
the always-on `worker` and `beat` services were removed. Render will not start a
run while the previous one is still going, which is the overlap guard the sweeps
want. Note Render cron jobs are **not offered on the free tier** and floor at
about $1/mo each, so the three together cost ~$3/mo even with a free web service.

Neither Render Postgres nor Render Key Value is used: Postgres is **Neon** and the
cache is **Upstash**, both reached over the public internet with TLS. Put every
service in the **Ohio** region — Neon lives in `us-east-2`, and any other region
adds a cross-region round trip to every query.

`migrate` runs in the **web** service's *build command*, not a pre-deploy command:
Render's dedicated pre-deploy step is a paid-tier feature. Web healthchecks at
`/health/`. Static files are served by **WhiteNoise** (no separate static host);
`collectstatic` also runs in the build command.

`ensure_developer` runs alongside them, and must: it is what recreates the
protected developer account after a database restore or a fresh environment, and
what repairs it if an admin managed to demote it. Idempotent — one query per
address in `PLATFORM_DEVELOPER_EMAILS`, and a no-op when that is unset. See
docs/adr/0004-protected-developer-role.md.

```
# Web — Build Command
pip install -r requirements.txt && python manage.py collectstatic --noinput && python manage.py migrate --noinput && python manage.py ensure_developer
# Web — Start Command
gunicorn config.wsgi:application --bind 0.0.0.0:$PORT --workers 2 --threads 4 --timeout 120

# Cron (all three) — Build Command      (NO migrate — see step 4)
pip install -r requirements.txt
# Cron — Start Command, one per job
python manage.py run_scheduled notification_retry
```

Workers dropped 3 → 2 versus Railway: the free instance is 512 MB, and three
gunicorn workers plus threads will OOM it. Raise it after moving to a paid plan.

**Steps:**
1. Nothing to provision on Render. Neon and Upstash already exist; copy their
   URLs. Redis is the cache and rate-limit store only — there is no broker.
   Keep the `/0?ssl_cert_reqs=required` suffix on the Upstash URL: without the
   `/0?` the port fails to parse and every cache call raises.
2. Create one **Web Service** and three **Cron Jobs** from this repo, with the
   commands and schedules above. Cron jobs have no healthcheck path and no
   restart policy to set — Render treats an exit-0 run as *finished*.
3. Put the env vars in **one Environment Group** and link it to all four services
   (they all import `config.settings`), from `.env.example`. The non-negotiables
   for a first boot:
   `DJANGO_SECRET_KEY`, `DEBUG=False`, `ALLOWED_HOSTS=<your-domain>`,
   `DATABASE_URL=<Neon pooled URL>`,
   `CACHE_REDIS_URL=<Upstash rediss:// URL>`,
   `BREVO_API_KEY` + the 13 `BREVO_TEMPLATE_*`, `CORS_ALLOWED_ORIGINS`,
   `FRONTEND_BASE_URL`, and `USE_R2_STORAGE=True` + the R2 keys.
   A cron service missing one `BREVO_TEMPLATE_*` crash-loops exactly as the old
   worker did — settings fails fast on a missing key.
4. Deploy web first (its build command migrates), then the cron jobs.
   **Do not** add `migrate` to a cron job's build command: two services migrating
   a fresh database concurrently is the race that used to break beat.
5. Create the admin login once: `python manage.py createsuperuser`. **This needs a
   shell, and Render has no shell or SSH on free instances** — so either do this
   from your machine with the production env exported, or move the web service to
   a paid plan first. See "Post-deploy checklist" below.

**Optional config, and when you'd set it.** These have working defaults, so a
first boot doesn't need them:

| Var | Default | Set it when |
|---|---|---|
| `RATE_LIMIT_INQUIRY_SUBMIT_BURST` | `6/10m` | The public inquiry form is refusing legitimate re-submits. Keyed on `(IP, email)` — one lead's own allowance, not the office's. A double-click costs one attempt if the dedupe marker is already written, two if it races the first request still in flight; 6 is sized so a lead gets three submissions even in the pessimistic case. The window clears in ten minutes rather than at the next hour boundary. Raising this does **not** widen what one submitter can take from `RATE_LIMIT_INQUIRY_SUBMIT_IP` (10/h) — only how fast |
| `RATE_LIMIT_INQUIRY_SUBMIT_IP` | `10/h` | Spam is getting through (raise nothing — *lower* this) or a shared connection is being refused (raise it). Keyed on the IP alone; this is the tier that stops a script varying the email on every request |
| `RATE_LIMIT_AUTH_LOGIN_DAILY` / `_TOKEN_REFRESH_DAILY` / `_PASSWORD_RESET_*_DAILY` | `100/d`, `500/d`, `20/d`, `50/d`, `20/d` | An office or mobile gateway (one IP, many people) is being refused on a specific auth endpoint. Per-endpoint on purpose — raising one no longer affects the others, which is the whole reason they exist |
| `THROTTLE_ANON` | `1000/day` | Rarely. It is a safety net for endpoints with no limit of their own, not the binding limit — the per-endpoint daily caps above are. If this is what's binding, something is wired wrong. Keep it above the sum of those caps. The inquiry form is exempt entirely, so it is never the cause of a refused lead |
| `THROTTLE_USER_BURST` | `120/m` | A logged-in user is being refused. Almost always a frontend retry/polling loop rather than a real person — check before raising it. Keep it a **per-minute** rate: a `/day` value lets one loop lock a real person out for hours |
| `RATELIMIT_RETRY_AFTER_SECONDS` | `60` | Almost never. It is only the **fallback** for a 429 whose wait could not be computed — normally `Retry-After` carries the real `time_left` of whichever tier filled, so a daily cap reports hours rather than a minute. Raising this does not slow anyone down; it only changes what a client is told when the real figure is unavailable |
| `RECAPTCHA_SECRET_KEY` | *(blank ⇒ verification skipped)* | You want the inquiry form's captcha actually checked. Blank is the deliberate local/CI/test state. The **site** key lives in the frontend, not here |
| `RATE_LIMIT_AUTH_LOGIN_ACCOUNT` / `_ACCOUNT_DAILY` | `10/h`, `50/d` | Keyed on the submitted **email**, not the IP — the axis a per-IP limit cannot see, since a distributed run sends one attempt per address. Mostly a backstop for addresses with **no account**: a real one hits the five-failure account lock first (see "A user says they cannot sign in"). Both must stay **above** `User.MAX_FAILED_LOGINS`, or a locked account reports an opaque 429 instead of the reset instruction |
| `RATE_LIMIT_ADMIN_LOGIN` / `_DAILY` | `5/m`, `50/d` | An operator is being refused at **`/admin/login/`** (a 429, not the lockout message). Its own IP groups, so admin traffic and public API traffic never draw down each other's buckets; the *account* tiers are shared with the API on purpose. Tighter than the API's `10/m` because the admin has a handful of humans and no programmatic callers — nothing legitimate here retries, so a burst is a guesser. Failed POSTs only; a GET of the form is never counted |
| `RATE_LIMIT_AUTH_LOGIN` / `_TOKEN_REFRESH` / `_PASSWORD_RESET_*` | see `.env.example` | Tuning the per-minute auth throttles. Note `AUTH_LOGIN` counts **failed** attempts only, so a successful sign-in never consumes it — raise it only if genuinely bad attempts from one address are being refused too soon |

**Before you launch the public inquiry form**, two things are on you, not the
code: add the marketing origin (`hephzibahluxe.com`) to `CORS_ALLOWED_ORIGINS` —
it's the only allowance in the project, no regex and no allow-all, so a browser
POST from the site is blocked until it's listed — and tick
`receives_inquiry_alerts` on at least one staff account (Django admin → Users).
**The flag defaults to off, so until somebody has it, every submitted lead saves
and the client still gets their acknowledgement, but nobody is told about it** —
the only trace is an `inquiry_no_recipients` log event. The database currently
holds exactly **one** staff/admin account, so that is the one to tick unless you
add more first.

**Why R2 matters here:** the platform has no local-disk media path at all —
`USE_R2_STORAGE=True` is **required** in every real environment (when it's off,
media falls back to non-persistent in-memory storage meant only for tests/CI).
So all client documents/invoices/receipts and event pictures live on R2. For the event pictures the frontend shows inline (event
covers, day images, contact photos), also set up a **separate public R2 bucket**
(`R2_PUBLIC_BUCKET_NAME` + `R2_PUBLIC_URL`) so they get unsigned, long-lived URLs
instead of the 1h signed URLs used for documents — see `.env.example` and
`apps/core/storages.py`. Leaving them blank is safe (images fall back to the
signed bucket).

**Deploy check** before shipping: `python manage.py check --deploy` (with
`DEBUG=False`). HSTS now ships on by default (`SECURE_HSTS_SECONDS`, env-tunable),
so the only expected remaining warning is the SSL-redirect one — `SECURE_SSL_REDIRECT`
is opt-in, turn it on once HTTPS is confirmed end to end.

## Post-deploy checklist & commands

Run this after a redeploy where env values changed — especially `DATABASE_URL`,
the R2 buckets, or "all of them". These commands run **against production**, not the
Windows dev box.

On a paid web service: `render ssh <service>`, or the **Shell** tab in the
dashboard. **On the free tier neither exists** — Render offers no shell and no SSH
on free instances. Until the web service is paid, run these from your own machine
with the production env exported instead; that works here only because Neon and
Upstash are public TLS endpoints rather than private-network addresses, so the
live hazard is pointing at the *wrong* env. Check `DATABASE_URL` and
`CACHE_REDIS_URL` before every such run. `curl` runs from your own machine
regardless.

Only web is SSH-able while idle — a cron service has no running container between
scheduled runs. To run something ad hoc, use the web shell; `run_scheduled` works
there too.

### First, know what your changes did

| Change | Consequence to handle after deploy |
|---|---|
| **New `DATABASE_URL`** | Points at an **empty** DB. `preDeployCommand` auto-runs `migrate`, whose data migrations seed every `Scheduled Task Settings` row **enabled** — so any admin toggles from the old DB are gone and every scheduled task resumes. No users either → make a new superuser. There is no separate schedule to re-seed. |
| **New R2 buckets** | New buckets start empty; old media doesn't carry over. The **public** bucket must actually be public (r2.dev subdomain or custom domain) or public image URLs 403. Bad R2 keys fail at **runtime**, not boot → test an upload. |
| **Any other var** | Set it on **all four services** (they share `config.settings`); a `DATABASE_URL` that differs between web and a cron service is a split brain. Blank/typo'd `BREVO_API_KEY`, any of the 13 `BREVO_TEMPLATE_*` (parsed as ints), `CACHE_REDIS_URL` (when `DEBUG=False`), or `FRONTEND_BASE_URL` **crash the boot** — note `BREVO_TEMPLATE_INQUIRY_RECEIVED` and `BREVO_TEMPLATE_INQUIRY_SUBMITTED_INTERNAL` are the two newest and are read with `env.int()` and **no default**, so a service that had a clean boot before the inquiry feature will crash-loop until both are set on it; `DEBUG=False` with empty `ALLOWED_HOSTS` makes every request 400. |

If the new `DATABASE_URL` is a **copy** of the old data (not a fresh DB), skip the
superuser step and step 6 — existing users and task states are preserved.

### Checklist

**1. Watch each service's Deploy Logs** for a clean boot — web shows `migrate` →
`collectstatic` → `gunicorn … Listening`, and then one line from
`apps.core.background`: `background: async dispatch enabled (max_workers=4,
max_queued=100)`. That line is the confirmation that `config/wsgi.py` opted this
process into asynchronous dispatch; without it every `.delay()` would run inline
in the request and email would be slow but not lost. A crash-loop = a
missing/typo'd env var (the log names it).

A cron service shows nothing until its first scheduled run.

**2. Health check from your machine** — validates the new DB + Redis in one shot:

```bash
WEB=https://<your-web-domain>
curl -sS $WEB/health/          # {"status":"ok"} — process up (no I/O)
curl -sS $WEB/health/ready/    # {"status":"ok"} — DB + cache reachable
# 503 with errors.db  -> bad DATABASE_URL ;  errors.cache -> bad CACHE_REDIS_URL
# The body says only WHICH one failed ({"db": "unreachable"}) — never the host,
# port or role, because this endpoint is unauthenticated and unthrottled. For the
# driver's actual message read the logs: event=health_dependency_down.
```

**3. Confirm the new values loaded, migrations applied, make an admin** (Render
Shell on a paid plan, else locally with the production env exported)**:**

```bash
render ssh hephzibah-luxe-api        # paid plans only; skip on free
python manage.py shell -c "from django.conf import settings as s; print('DEBUG', s.DEBUG); print('DB', s.DATABASES['default']['HOST'], s.DATABASES['default']['NAME']); print('R2', s.USE_R2_STORAGE, s.AWS_S3_ENDPOINT_URL, s.AWS_STORAGE_BUCKET_NAME); print('PUBLIC', getattr(s,'R2_PUBLIC_URL',''), getattr(s,'R2_PUBLIC_BUCKET_NAME',''))"
python manage.py migrate --check     # exits 0 if nothing pending
python manage.py createsuperuser     # new DB is empty
```

**4. Test the new R2 keys/buckets end-to-end** (bad keys only surface here, not at boot):

```python
python manage.py shell
```
```python
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from apps.core.storages import select_public_media_storage
# private / signed bucket (documents):
p = default_storage.save("healthcheck/t.txt", ContentFile(b"ok"))
print("private:", default_storage.exists(p), default_storage.url(p)); default_storage.delete(p)
# public bucket (display images):
sp = select_public_media_storage(); print("public backend:", type(sp).__name__)  # want PublicMediaStorage
pp = sp.save("healthcheck/pub.txt", ContentFile(b"ok")); print("public url:", sp.url(pp))
```
Then from your machine: `curl -I "<public url>"` → expect `200` (`403` = the public
bucket isn't public yet).

**5. Confirm background dispatch actually works.** This replaces the old
Redis→worker queue-drain check. There is no queue to watch, so watch the durable
row instead — which is the honest test anyway, since the row is what the system
guarantees. From the **web** shell:

```python
from apps.core import background
print("async enabled:", background.async_enabled())   # must be True in web

# Queue a real notification and watch it leave QUEUED.
import time
from apps.notifications.models import Notification, NotificationStatus
from apps.notifications.services import queue_notification
n = queue_notification(
    recipient_email="you@example.com", template_name="password_reset",
    context={"user_first_name": "Ops", "code": "000000", "expires_in_minutes": 30},
)
for _ in range(20):
    n.refresh_from_db(); print(n.status, n.attempt_count, n.error_message[:80])
    if n.status != NotificationStatus.QUEUED: break
    time.sleep(1)
# queued -> sent  : dispatch, the pool and Brevo all work
# queued forever  : async_enabled() is False, or the pool never ran — the retry
#                   sweep will pick it up in <=10 min, which is itself the proof
#                   that the durability floor works
# queued -> failed: the pool ran; read error_message (Brevo key/template)
```

Then confirm each cron service can run its group. From the web shell (same image,
same env), or from a manual run of the cron service:

```bash
python manage.py run_scheduled notification_retry   # exits 0, prints one line per task
python manage.py run_scheduled daily_maintenance
```

A non-zero exit means at least one task failed, and the command names which —
that exit code is what Render's cron run history reports on.

**6. Re-apply task on/off state** — a fresh DB re-enabled every scheduled task:

```python
from apps.notifications.models import ScheduledTaskSettings
print(dict(ScheduledTaskSettings.objects.values_list("task_key", "is_enabled")))
ScheduledTaskSettings.objects.update(is_enabled=False)   # or toggle individually in the admin
```
Takes effect on the next cron run — no redeploy, and no scheduler to reload.

**7. App smoke test from your machine** — auth against the new DB with the new superuser:

```bash
curl -sS -X POST $WEB/api/v1/auth/token/ -H 'Content-Type: application/json' \
  -d '{"email":"you@example.com","password":"your-password"}'      # -> {"access","refresh"}
curl -sS $WEB/api/v1/users/me/ -H "Authorization: Bearer <access>"
```
Use `https://` **and** the `/api/v1/` prefix — a plain-`http` or prefix-less POST
301-redirects and downgrades to GET (`405 Method "GET" not allowed`).

### Is anything keeping the DB compute awake?

**This used to be beat, and beat is gone.** Its `DatabaseScheduler` queried
`django_celery_beat_periodictasks` every ~5s (`DEFAULT_MAX_INTERVAL = 5`) on a
persistent connection — enough on its own to stop Neon scaling to zero, *even with
every Periodic Task disabled*. The remedy documented here used to be "pause /
scale the beat service to 0", a manual workaround for a structural
problem. Removing Celery removed the problem; that instruction is retired.

What can still hold the DB awake, in rough order of likelihood:

| Cause | Why | What to do |
|---|---|---|
| An uptime monitor on `/health/ready/` | It opens a DB connection **and** a Redis command per probe | Point it at `/health/` (no I/O). The web service already healthchecks `/health/` |
| A background thread that outlived its task | `apps/core/background` closes thread-local connections in a `finally`, so this should be impossible — but a `pg_stat_activity` row from a `bg` thread means that contract broke | Read the traceback in the web logs; the leak is a bug, not a config |
| The cron services | Each run is a cold boot that opens one connection, so `*/10` wakes the compute 144×/day | Real, and a deliberate trade — see below |
| Real traffic | web runs `CONN_MAX_AGE=0` (pinned in `config/settings.py`, asserted by `DatabaseConnectionSettingsTests`), so a connection lives only as long as the request | Nothing to do |

**The standing trade.** `cron-notify` at `*/10` costs 144 wake-ups a day. Widening
it to `*/15` or `*/30` is a genuine saving, but the retry sweep is the **only**
retry path for a failed email and a password-reset code lives 30 minutes
(`accounts.utils.RESET_CODE_TTL_MINUTES`). Widen the cron and you must lengthen
the TTL with it, or a single transient Brevo failure delivers a code that is
already dead. Do not change one without the other.

See exactly what's connected, from the web shell:

```python
from django.db import connection
with connection.cursor() as c:
    c.execute("""SELECT pid, state, now()-backend_start AS conn_age,
                        now()-state_change AS idle_for, left(query,90) AS last_query
                 FROM pg_stat_activity
                 WHERE datname = current_database() AND pid <> pg_backend_pid()
                 ORDER BY backend_start;""")
    for row in c.fetchall():
        print(row)
# Expect: nothing but your own shell during a quiet period. A long conn_age with a
# tiny idle_for is something polling — read its last_query to find out what.
```

## Everyday

```powershell
python manage.py runserver              # dev server on :8000
python manage.py check                  # system checks (run before committing)
python manage.py makemigrations         # generate migrations after model changes
python manage.py migrate                # apply migrations
python manage.py run_scheduled --list   # show the cron groups and the tasks in each
python manage.py createsuperuser        # create an admin login
python manage.py shell                  # Django shell
```

## Tests

```powershell
pytest                                  # full suite (pytest.ini / conftest.py)
pytest apps/document_hub                 # one app
python manage.py test apps.document_hub  # Django test runner equivalent (dotted path)
```

**Shared test database (Neon/Postgres):** the test DB is created on the same
server as `DATABASE_URL`, so a leftover `test_<dbname>` from an interrupted run
makes the next run prompt (and hang non-interactively) or collide. Pass
`--noinput` to auto-recreate it and `--keepdb` to reuse it between runs (much
faster; the schema is stable once migrations are applied):

```powershell
python manage.py test --noinput --keepdb            # whole suite, reuse test DB
python manage.py test apps.document_hub --noinput --keepdb
```

If a run dies mid-way and the next start errors with `database "test_…" already
exists` / `is being accessed by other users`, re-run with `--noinput` (it drops
and recreates) once no other test process is connected.

## Background work & scheduled tasks

There is no worker, no beat and no broker. Two mechanisms, and it is worth knowing
which one you are looking at:

**1. Deferred work — a thread pool inside the web process** (`apps/core/background.py`).
`queue_notification()` writes a `Notification` row, commits it, and *then* hands the
send to a bounded pool (4 threads by default). Nothing polls anything.

Async dispatch is **opt-in per process, and only `config/wsgi.py` opts in.**
Everywhere else — every management command, all three cron groups, `shell`, tests,
data migrations — `.delay()` runs the work **inline** in the calling thread. That is
not a nicety: the retry sweep dispatches sends from inside a cron process that exits
the moment the command returns, and a pool there would be destroyed seconds later,
silently dropping exactly the mail the sweep exists to rescue.

**2. Scheduled work — `manage.py run_scheduled <group>`, invoked by platform cron.**
One group, one process, run to completion, exit. Groups are collapsed by cadence:

| Group | Cron service | Schedule | Tasks |
|---|---|---|---|
| `notification_retry` | `cron-notify` | `*/10 * * * *` | retry failed + stranded notifications; send due debounced event-details emails |
| `daily_maintenance` | `cron-daily` | `0 8 * * *` | payment-due digest; meeting-prep digest; prune reset tokens; flush expired JWTs; clear expired sessions |
| `weekly_maintenance` | `cron-weekly` | `0 3 * * 1` | purge old notifications; sweep orphaned documents/blobs |

```powershell
python manage.py run_scheduled --list               # groups and their contents
python manage.py run_scheduled notification_retry   # run one group now
```

Everything in a group runs synchronously and independently: one failing task does
not strand the rest, each failure is printed, and the command exits **non-zero** if
any failed — which is what Render's cron run history reports on. This command has
no alerting of its own by design (per `docs/OBSERVABILITY_STANDARD.md`, the app
emits and the monitoring stack decides who gets paged).

### The durability rule

> **Every deferred task must have a durable status field and a cron sweep that
> re-drives it. A task that can only be triggered once is a task that will be lost.**

The pool has no persistence: if the process dies, in-flight work is gone. That is
safe *only* because the row is committed first and a sweep re-drives anything
stranded. If you add a background task, it needs both halves. See
[`docs/adr/0001-remove-celery.md`](docs/adr/0001-remove-celery.md).

### `notification_retry` is the load-bearing one

Since in-task retry was removed, this sweep is the **only** retry path for a failed
send — and it also re-drives rows stuck in `queued` (a row whose in-process dispatch
was lost to a deploy or an OOM). Its cadence is therefore the ceiling on how long a
password-reset email can be delayed, and a reset code lives 30 minutes. Read the
warning under "Is anything keeping the DB compute awake?" before widening it.

### Checking on it

Nothing to inspect in a broker — the `Notification` table *is* the queue view:

```powershell
python manage.py shell -c "from apps.notifications.models import Notification as N; from django.db.models import Count; print(list(N.objects.values('status').annotate(n=Count('id'))))"
```

Read it as:

| Status | What it means | What happens next |
|---|---|---|
| `queued` | Handed to the thread pool | Sent within seconds. Older than ~10 min means the dispatch was lost — the sweep re-drives it |
| `sent` | Brevo accepted it | Nothing. Purged after 90 days |
| `failed` | Attempted and failed; `error_message` says why | Retried by the sweep while `attempt_count < 3` |
| `deferred` | **Never attempted** — the Brevo breaker was open. `attempt_count` is 0 | Retried by the sweep, identically to `failed` |
| `abandoned` | Given up: budget spent, or past the 7-day window | Nothing. Purged after 90 days |

`deferred` is new and worth knowing about when triaging: these rows used to show as
`failed` with `attempt_count=0`, which read as "we tried and it broke" when in fact
nothing had been tried. Seeing a block of them means Brevo was down, not that
something is misconfigured — check `Service Health States`.

Django admin → **Notifications** shows the same per row, with `attempt_count` and
`error_message`. `context` is deliberately **excluded** from that admin: it is the
exact params dict sent to Brevo, which for `password_reset` and `user_credentials`
means a live code or temporary password. It is redacted automatically once a row
reaches a terminal state.

### Brevo health

`Service Health States` in the admin holds Brevo's live up/down status, maintained
from the outcome of **real sends** (three consecutive failures trips it). The active
5-minute probe was removed with Celery — 288 runs and 576 outbound HTTPS calls a
day, to learn a few minutes earlier what the next real send would report.

While Brevo is `down`, ordinary sends park themselves as `failed` with the message
`Deferred: Brevo is currently unavailable.` rather than hammering a dead API, and
the sweep re-attempts with `force=True` — so the sweep is also what detects
recovery. A `down` verdict older than 30 minutes
(`ServiceHealthState.DOWN_STALE_AFTER`) stops blocking sends, so a stale row can
never mute the platform indefinitely. The admin still shows the last real verdict.

### Local development

No extra processes to run. `python manage.py runserver` gives you the real
behaviour, because `runserver` boots through `config/wsgi.py`. To debug a task
synchronously inside the request that triggered it, set `BACKGROUND_EAGER=True`.

## Management commands (this project)

Three custom commands ship with the backend (everything else is stock Django).
`python manage.py help` lists them under `[accounts]`, `[core]` and `[documents]`.

```powershell
# [core] Run one group of scheduled tasks to completion, then exit. This is the
# whole scheduler — platform cron invokes it. Exits non-zero if any task failed.
python manage.py run_scheduled --list
python manage.py run_scheduled notification_retry
python manage.py run_scheduled daily_maintenance
python manage.py run_scheduled weekly_maintenance

# [documents] Sweep orphaned document artifacts: dangling Document registry rows
# AND document_hub file blobs with no owning row (deleted/replaced files, rollback
# leftovers). Scoped so it never touches other apps' files (budget receipts, etc.).
python manage.py cleanup_orphaned_documents --dry-run   # report only
python manage.py cleanup_orphaned_documents             # actually delete

# [accounts] THE BREAK-GLASS. Release a login lock from the shell — the only way
# back in when the locked account is the one you would use to press the admin's
# "Release login lock" button. No password change needed, and note that
# `changepassword` does NOT help: the lock is failed_login_count, not the password.
# Run it INSIDE the deployment, never from the dev box — two of the three counters
# live in Redis, so a local run clears the prod DB counter against a LOCAL cache
# and leaves the lock in place. See "You are locked out of the Django admin
# yourself" under Common operator tasks.
python manage.py release_login_lock you@example.com
python manage.py release_login_lock --all               # every locked account

# [accounts] Create or repair the protected developer accounts named in the
# PLATFORM_DEVELOPER_EMAILS environment variable — the role above admin, which an
# admin cannot revoke (docs/adr/0004-protected-developer-role.md). Idempotent, so
# it belongs in the build command and is safe to run by hand any time. It creates
# the account if missing, re-derives role/is_active/is_staff/is_superuser if an
# admin managed to change them, and NEVER touches an existing password. A no-op
# with a friendly message when the variable is unset.
python manage.py ensure_developer --dry-run             # report only
python manage.py ensure_developer
```

`cleanup_orphaned_documents` also runs weekly now, wrapped as a task in
`weekly_maintenance`. It deletes for real there — no `--dry-run` — which is the
point, since every orphan it finds is a blob being billed as R2 storage. **Run it
with `--dry-run` by hand and read the output the first time**, and if the report
ever looks wrong, turn `documents_cleanup_orphaned` off in Scheduled Task Settings
to stop the deletions without a redeploy.

### Seeding data — where each kind lives

- **Task schedule (timing)** → *not seeded, not in the database.* It lives in each
  cron job's **Schedule** field in the Render dashboard, and the group
  contents are in `apps/core/management/commands/run_scheduled.py`.
- **Notification types / scheduled-task toggles / Brevo health row** → seeded by
  data migrations during `migrate` (no separate command).
- **Portal defaults (FAQ template + welcome message)** → *no seed command*;
  configured in Django admin (`Portal Defaults`) or via `PATCH
  /document-hub/defaults/`. A new engagement then auto-seeds **only the FAQ**;
  the Service Agreement / Quotation / Welcome Booklet are added per client via
  `POST /document-hub/documents/`.
- **Admin login** → `createsuperuser`.
- **Client portals** → auto-created by a signal when a user is registered with
  role `client` (no command).

## Common operator tasks

**Offboard a client (reversible — never delete the user).**
Django admin → Users → select → *"Deactivate (offboard) selected users"*, or:
```
PATCH /api/v1/users/<email>/status/   { "is_active": false, "reason": "..." }
```
Takes effect immediately (SimpleJWT rejects an inactive user on every request,
even with an unexpired access token) and blacklists their refresh tokens. Undo
with the *Re-activate* action or `{ "is_active": true }` — that clears the
audit trio (`deactivated_at`/`deactivated_by`/`deactivation_reason`) and restores
login. Their portal, events, documents and attribution history are untouched
throughout; that's why offboarding is a deactivation and not a delete.
**You cannot deactivate your own account** (guard in `accounts.services`).

**A user says they cannot sign in.** Django admin → Users → filter **login lock:
Locked out**, or read the **Login** column (`Locked (5/5)` in red, `2/5 failed` in
amber). Open the account for the **Rate-limit buckets** readout, which shows the
failure counter and both account-keyed rate tiers live — that is what turns "they
still can't get in" from a guess into a reading.

To unblock: select them → **"Release login lock (unblock sign-in)"**. They can
sign in immediately and **no password change is required**.

Why the action rather than editing the row: three separate things can refuse one
address — the counter on the user, a mirror counter in the cache, and two
account-keyed rate buckets — and you cannot tell from outside which is firing.
**Clearing only the database counter would look like it worked and then hand the
user a 429 on their next attempt.** The action clears all three
(`login_guard.release_account()`), which is why the counters are read-only in the
admin.

Nothing needs doing if you would rather not intervene: five consecutive failures
lock an account, any successful sign-in resets the run to zero, an untouched run
ages out after 24 hours, and **completing a password reset clears it** — that is
the self-service path the 401 tells the user to take. Full reasoning:
[`docs/adr/0002-login-failure-tracking.md`](docs/adr/0002-login-failure-tracking.md).

**You are locked out of the Django admin yourself.** `/admin/login/` is guarded
too, so the *Release login lock* button is behind the door that is locked. Four
ways back in, in the order you would normally reach for them:

**1. Complete a password reset.** The self-service path, and the one to use
first. The code goes to your inbox, which an attacker cannot read, and finishing
the flow clears the lock.

**2. Run the break-glass command** against production:

```bash
render ssh hephzibah-luxe-api        # paid plans only — see the warning below
python manage.py release_login_lock you@example.com
python manage.py release_login_lock --all                # every locked account
```

Instant, needs no password change, and works with no admin session. It clears all
three things at once (the same `login_guard.release_account()` the admin button
calls), so the two can never drift.

> **On the free tier there is no shell and no SSH**, so this command cannot be run
> against production at all — the admin action is your only route until the web
> service moves to a paid plan. Plan for that before you need it: the whole point
> of this command is that it works when the admin is locked behind the door.

> **Point it at the production env, not your dev one.** Two of the three counters
> live in Redis, so a run against a *local* cache clears the database counter and
> leaves the lock in place — the exact half-release this command exists to
> prevent. Neon and Upstash are public TLS endpoints, so a laptop run does reach
> production *if* given the production `DATABASE_URL` and `CACHE_REDIS_URL`;
> verify both before running.

> `manage.py changepassword` does **not** rescue you. The lock is
> `failed_login_count`, which is independent of the password: you would change it
> successfully and still be refused.

**3. Wait 24 hours.** Both lock counters age out on their own
(`User.FAILED_LOGIN_WINDOW`), lazily, with no sweep job. Doing nothing is a valid
fix if you are not in a hurry.

**4. Ask another admin to press the button.** If a second admin can still sign
in, Django admin → Users → select → *"Release login lock (unblock sign-in)"* is
the easiest option of all. Options 2 and 3 exist for when there is no such
person — which, with **one** staff account in the database today, is the case
here until you add another.

### Which refusal did you actually hit?

They look different and recover differently:

| What you see | What it is | Fix |
|---|---|---|
| Lockout message on the form, **correct password still refused** | the five-strike **account lock** | any of the four above |
| Plain 429 JSON with a `Retry-After` | the **per-IP rate limit** (`5/m`, `50/d`) | wait for the window, or sign in from another network |

A correct password cannot rescue a locked account, by design: the lock is checked
*before* the password, and the refusal redirects back to the form rather than
re-rendering it, precisely so the submitted credentials are thrown away instead
of being authenticated. [`apps/accounts/README.md`](apps/accounts/README.md) §1c
has the full reasoning.

### Someone on your own network is being refused at /admin/login/

Worth knowing before it happens, because the two axes behave very differently
when a guesser shares your office IP:

- **They cannot lock your account.** The lock is keyed on the *email typed*, so
  hammering `nobody@example.com` locks that address, not yours. Only attempts
  against **your** address can lock you.
- **They can spend your IP's budget.** The per-IP tiers cannot tell two people
  behind one address apart, so `admin_login` (5/m) and `admin_login_daily` (50/d)
  are shared by everyone on that connection. If they burn the daily 50, everyone
  at that address gets a 429 at the admin door until the window rolls.
- **Switching networks is the immediate way out** — a phone hotspot is a
  different IP with its own empty buckets. Your account was never the problem.
- **The portal is unaffected.** `/admin/login/` and `POST /api/v1/auth/token/`
  have separate IP counters, so a flood at the admin door does not stop anyone
  signing in to the client portal from that same address.

Both halves are pinned by tests
(`test_someone_elses_flood_from_your_ip_does_not_lock_your_account` and
`test_but_their_flood_does_spend_the_shared_ip_budget`).

**Find a client / get their portal id.** `GET /api/v1/users/?role=client&search=<name>`
— each row carries `portal_id`, which most staff endpoints take as a body/query param.

**See who last changed a record.** Every model's admin has a collapsed
**Attribution** fieldset (created by/at, last updated by/at), and the changelist
shows the same as columns. In the API the equivalents are
`created_by_display` / `last_updated_by_display` — the raw user ids are never
exposed.

**Inspect reference-code counters.** Django admin → Reference Counters
(read-only). `eventtype:W` = how many weddings have been numbered;
`<engagement_id>:INV` = that engagement's invoice sequence. Editing is disabled
on purpose — lowering a counter reissues codes that collide with ones already
sent to clients.

**A client says their reset code doesn't work.** Django admin → Password Reset
Tokens → find their row. There is deliberately no code and no hash to read — it is
stored PBKDF2-hashed and cannot be recovered, by us or by anyone with the database.
What you can read is `attempt_count`: at 5 the token is burned and they will keep
being told to request a new code however many times they retype it, which is the
answer. `expires_at` is 30 minutes after `created_at`. The fix is always the same:
have them request a new code, which invalidates the old one.

**Set a client's timezone.** Django admin → Users → *Personal Info* → `timezone`,
an IANA name (`Africa/Lagos`, `America/New_York`); blank inherits
`PLATFORM_DEFAULT_TIMEZONE`. Clients can also set their own via
`PATCH /users/me/update/`. This does **not** change how any timestamp is displayed
— it decides which calendar *day* their payment-due and meeting-prep digests are
measured against, which is off by one for a client far from UTC if it is wrong. An
unknown name is rejected on save.

**Choose who receives lead alerts.** Django admin → Users → tick
`receives inquiry alerts` on each staff member who should get the
`inquiry_submitted_internal` email. One email per ticked account, per lead. The
flag only counts on an **active staff** account — deactivating someone stops
their alerts without anyone editing the flag. With nobody ticked, inquiries still
save and still get acknowledged; the internal alert is skipped and logged as
`inquiry_no_recipients`.

**Retry a failed email.** Django admin → Notifications → select → *Resend*. The
`notification_retry` cron group does this automatically every 10 minutes — for
`failed` and `deferred` rows with attempts remaining **and** for rows stuck in
`queued` more than 10 minutes (a dispatch lost to a deploy or a restart). The admin action is for when
you don't want to wait. Client-visible history is `GET /api/v1/notifications/`
(auth emails are excluded from it by design).

**Destructive deletes are gated.** Events, payment schedules and budgets refuse
to delete when they have related data unless you pass `?confirm=true`; the 400
body carries the impact breakdown. A payment schedule with a **paid** milestone
is refused outright — clear or unmark those first.

## Admin-managed feature toggles (Django admin, no env/deploy)

- **Notifications on/off, per type** — `Notification Type Settings`, one
  row per notification (`new_reminder`, `payment_due`, `meeting_prep_due`,
  `phase_advanced`, `event_details_updated`, `document_added`,
  `invoice_issued`, `receipt_issued`, `milestone_paid`, `user_credentials`,
  `password_reset`, `inquiry_received`, `inquiry_submitted_internal`) —
  toggle `enabled` inline from the admin list. Turning
  one off doesn't affect the others. A type with no row is treated as
  enabled. All email (including auth) sends via Brevo through this same
  pipeline — see `apps/notifications/README.md`.
- **Background tasks on/off, per task** — `Scheduled Task Settings`
  (`payment_due_digest`, `meeting_prep_digest`, `notifications_retry_failed`,
  `notifications_cleanup_old`, `event_details_notification`,
  `accounts_flush_expired_jwt`, `accounts_prune_reset_tokens`,
  `core_clear_sessions`, `documents_cleanup_orphaned`) — whether a scheduled or
  debounced job runs at all, independent of the per-notification-type toggle
  above. Every gated task checks this as its first statement. Toggle `is_enabled`
  inline; a task with no row runs normally, and the change takes effect on the
  next cron run with no redeploy.

  Task **timing** is deliberately *not* here — it moved out of the admin with
  `django-celery-beat`. Timing lives in each cron service's Cron Schedule (still no
  redeploy, just a different dashboard); this remains the on/off switch.
- **Phase auto-lock** — `Portal Settings` (`auto_lock_enabled`, `auto_lock_phase`,
  and which locks to apply). Locks event details / contacts once an engagement
  reaches the chosen phase.
- **A client's calendar timezone** — `Users` → `timezone` (per account; blank
  inherits `PLATFORM_DEFAULT_TIMEZONE`). Not a display setting — it selects which
  calendar day that client's daily digests are computed against. See
  `apps/core/timezones.py`.
- **Event-details notification debounce** — `Portal Settings`
  (`event_details_notify_debounce_seconds`, default 900 = 15 min). How long an
  event/event-day must go unedited before the "event details updated" client
  email sends; every edit resets the window. The send is now quantised to the
  `notification_retry` cadence, so a 15-minute window means the email lands
  15–25 minutes after the last edit rather than exactly 15 — in exchange for a
  debounce that survives a deploy, which the old countdown did not.
- **Portal defaults** — `Portal Defaults` (template docs + welcome message seeded
  to new portals). **Team defaults** — `Team Member.is_default`.
- **"Contact Your Team" info** — `Portal Settings` (`contact_email`,
  `contact_whatsapp`). Backs the client portal's Send an Email / Send a
  Message panel. Used to be the `HEPHZIBAH_EMAIL`/`HEPHZIBAH_WHATSAPP` env
  vars — now changeable without a redeploy.

## Notes

- **Reference codes** (`HL-PSW001-C001`, `-INV001`, …) are auto-generated and
  read-only — never set them by hand. The `<segment>` (e.g. `PSW001`) encodes the
  couple/honoree initials + event-type letter + a global per-event-type counter;
  `C`/`Q`/`INV`/`R` are Service Agreement / Quotation / Invoice / Receipt (the
  Service Agreement category value is `svc_agreement`). See
  `apps/document_hub/README.md`.
- **Media storage** is Cloudflare R2 in all real use (`USE_R2_STORAGE=True` + R2
  keys) — there is no local-disk media. With it off, media uses non-persistent
  in-memory storage, which exists only so tests/CI can run without R2.
- **File blobs are never auto-deleted.** Django's `FileField` leaves the old blob
  behind on delete/replace, so storage grows quietly. Run
  `cleanup_orphaned_documents --dry-run` periodically. It's scoped to the
  `documents` registry + document_hub paths and deliberately skips budget
  receipts, so it can't touch another app's files.
- **Attribution is system-set.** `created_by`/`last_updated_by` are
  `editable=False` and stamped from `request.user`; never set them by hand, and
  in a new admin they belong in `readonly_fields` (never `raw_id_fields`, which
  fails Django's system check). Pattern + checklist in `apps/core/README.md`.
- **Where the docs live.** Each app has its own `README.md` with an app-specific
  "Tips & gotchas" section — start there before reading code. The route list is
  `docs/API_CONTRACT.md`; the end-to-end manual test journey is
  `POSTMAN_TEST_DATA_V3.md` at the repo root.

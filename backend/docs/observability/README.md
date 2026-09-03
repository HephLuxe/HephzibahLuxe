# Observability — the event catalogue and the alert rules

The app **emits**; the stack **decides**. Nothing here is imported by Django —
`apps/core/logging.py` writes one JSON object per line to stdout and to Loki, and
these files are the Grafana side of that contract, versioned next to the code
that produces the events so the two cannot drift.

See [`../OBSERVABILITY_STANDARD.md`](../OBSERVABILITY_STANDARD.md) for the
principles. This is the concrete wiring.

## Files

| File | What to do with it |
|---|---|
| `grafana-contact-points.yaml` | Provision the ntfy contact points + routing. Install **first** — the rules reference them by name. |
| `grafana-alert-rules.yaml` | Provision the alert rules. |

Install by dropping both into Grafana's provisioning directory
(`/etc/grafana/provisioning/alerting/`) and restarting, or paste them via
**Alerting → Alert rules → Import**. Two placeholders must be replaced first:

- `LOKI_DATASOURCE_UID` — Grafana → Connections → Loki → copy the UID from the URL.
- `NTFY_BASE` — your ntfy host, e.g. `https://ntfy.example.com`.

The contact points post to `?template=grafana`, ntfy's built-in template for
Grafana's webhook payload, so the ntfy server must be **>= 2.14.0**. Without it
the request still succeeds and the notification still arrives — as the raw
Grafana JSON envelope, unreadable on a phone. `grafana-contact-points.yaml`
carries an inline-template fallback for older servers in its header comment.

Tier is carried by an `X-Priority` header (5 for P1, 3 for P2), not by the topic
— both tiers share `grafana-alerts`. On Android, priority 5 only rings through
Do Not Disturb once that notification channel has been granted the DND override
in the OS settings; the header does not do it alone. Verify on the phone that is
meant to be woken before trusting a P1 to arrive:

```
curl -H "X-Priority: 5" -d "P1 test" NTFY_BASE/grafana-alerts
```

## The log shape these queries match

`HephJsonFormatter` emits, per line:

```json
{"timestamp": "...", "level": "ERROR", "message": "...",
 "event": "inquiry_no_recipients", "inquiry_id": "...",
 "logger": "apps.inquiries.services", "request_id": null, "user_id": null}
```

Loki stream labels come from `LOKI_PUSH_*` in settings:
`{service="hephzibah-api", environment="production"}`.

So every query below is `{service="hephzibah-api"} | json | event="<slug>"`.
`event` is a **parsed field, not a label** — do not put it inside `{}`.

## Severity tiers

**P1 — page.** Something is being lost silently. These are the ones where the
absence of a complaint is not evidence of health.

**P2 — notify.** Degraded, act today, no one is losing data this minute.

**P3 — dashboard only, no rule.** Useful to look at, noisy to alert on. Listed so
nobody wires a pager to them by accident.

## The catalogue

Every `event=` slug the codebase emits. Grep-verified against the source, not
aspirational.

### P1 — page

| Event | Level | Emitted by | Why it pages |
|---|---|---|---|
| `inquiry_no_recipients` | ERROR | `inquiries/services.py` | A lead was captured and **nobody was told**. The client still got their acknowledgement, so there is no complaint to alert you. Must be zero. |
| `client_ip_unresolved` | WARNING | `core/ratelimit.py` | The edge stopped sending `X-Forwarded-For`, so **every client shares one rate-limit bucket**. Must be zero. |
| `recaptcha_v2_key_in_use` | ERROR | `inquiries/recaptcha.py` | The configured secret is a **v2** key, so the v3 score threshold and the action/replay check are not running and every token that merely parses is accepted. Same shape as the row above — a protection control collapsed silently, nothing errored, the form still returns 201. One wrong env var, permanent until noticed, and it cannot be caught at deploy time (see below). Must be zero. |
| `brevo_outage` | ERROR | `notifications/services.py` | The email pipeline is down. Transition-only, so a multi-hour outage pages once, not once per failed send. |
| `scheduled_group_failed` | ERROR | `core/management/commands/run_scheduled.py` | A cron group had a failing task. |
| `developer_account_repaired` | WARNING | `accounts/developers.py` | A protected developer account had been demoted, deactivated or stripped of `is_superuser`, and the platform put it back. The repair means nobody was locked out — which is exactly why it must page: the attempt succeeded at the database and left no other trace, and nothing else on the platform will tell you an admin tried to remove the owner. The `fields` field says what had been changed. Must be zero; one occurrence is either an admin who does not understand the role or one who does. See docs/adr/0004-protected-developer-role.md. |
| `developer_account_delete_blocked` | ERROR | `accounts/signals.py` | Something tried to **delete** the protected developer account and the `pre_delete` guard aborted the transaction. Three earlier layers refuse before this one is reachable (the admin hides the button, `has_delete_permission` refuses, the bulk action filters the row), so reaching it means a shell, a raw ORM call, or a cascade nobody anticipated. Must be zero. |
| *(absence of)* `scheduled_group_completed` | INFO | same | **A cron service stopped running at all.** A failed run is visible twice over; a run that never happens produces nothing. `notification_retry` is the only retry path for failed email and the only sender of debounced event-details mail. |

### P2 — notify

| Event | Level | Emitted by | Why |
|---|---|---|---|
| `brevo_send_failed` | WARNING | `notifications/services.py` | Individual sends failing without the circuit tripping. Alerts on a burst, not one. |
| `unknown_timezone` | ERROR | `core/timezones.py` | A stored IANA name no longer resolves; that account's digests silently fall back to UTC. |
| `login_account_locked` | WARNING | `accounts/views.py` | One or two is a person who forgot a password. A spike is a credential-stuffing campaign. |
| `admin_login_account_locked` | WARNING | `core/admin_login.py` | The **admin door**, so this one alerts on any occurrence rather than a spike. `/admin/login/` has a handful of human users and `role=admin` forces `is_superuser`, so one lockout is either an operator needing the break-glass or someone guessing at the control plane. The `has_account` field separates the two. |
| `background_timer_cap` | WARNING | `core/background.py` | The in-process timer cap was hit. Costs punctuality only (the cron sweep still delivers), but it means something is arming far more timers than expected. |
| `recaptcha_unreachable` | WARNING | `inquiries/recaptcha.py` | Verification **failed open**: Google could not be reached and the submission was allowed anyway. That is the deliberate trade for lead capture — a lost lead is worse than a spam one — but for the duration the public form has no bot filter at all. There is no circuit breaker here (unlike Brevo), so this is one line per submission: it alerts on a burst, not on one. |
| `recaptcha_bad_score` | ERROR | `inquiries/recaptcha.py` | Google returned a `score` that will not parse as a float. **Also fails open.** This should be impossible against a working v3 key, so a single occurrence means the siteverify response shape changed underneath us — alerts on any. |

### P3 — dashboard only

`rate_limited`, `admin_login_rate_limited`, `throttle_near_limit`,
`login_tier_exhausted`, `reset_code_rejected`, `inquiry_dedupe_hit`,
`event_details_dispatched`, `notifications_purged`, `reset_tokens_pruned`,
`brevo_recovered`, `brevo_send_deferred`, `health_dependency_down`,
`recaptcha_rejected`, `recaptcha_low_score`, `recaptcha_action_mismatch`,
`private_file_read_refused`.

Four are worth a **dashboard panel** even though they are not alerts:

- `rate_limited` broken down by `path` — the only way to tell whether a limit is
  mis-tuned before someone complains. Its `retry_after` field separates "clicking
  too fast" (60) from "hit a daily cap" (tens of thousands).
- `throttle_near_limit` — the leading signal, fired at 80% occupancy, before
  anyone is refused.
- `recaptcha_low_score` — plotted over time against its `recaptcha_threshold`
  field, the only way to see whether the threshold is mis-tuned. `0.5` in
  settings is Google's placeholder, not a measurement; if this climbs, the bar
  is quietly eating real inquiries and nobody will ever report it.
- `private_file_read_refused` (INFO, `core/file_views.py`) — a client asked
  `GET /files/<type>/<id>/` for a file on a portal that is not theirs, and was
  refused. Carries `file_type`, `object_id` and `user_id`.

  **Not an alert, and the reason is specific:** a handful of these is the
  expected shape of an ordinary bug — a frontend rendering a stale id after a
  document was deleted, or a staff member's session pointed at a portal they
  have since switched away from. Paging on that would train you to ignore it.

  What it is for is the *shape* of the curve. This endpoint mints credentials,
  so a sustained climb from one `user_id` across many `object_id`s is somebody
  walking ids with a valid token, and this log line is the only place that is
  visible — the caller sees a 404 identical to a genuine miss, by design, so
  nothing else distinguishes it. Worth a panel grouped by `user_id`, and worth a
  rule if the platform ever gets enough traffic for a baseline to mean
  something.

`health_dependency_down` (ERROR, `core/views.py`) records that
`/health/ready/` could not reach Postgres or Redis, carrying `dependency`
(`db`/`cache`) plus the driver's own message — which is deliberately **not** in
the HTTP response, since that endpoint is unauthenticated and the message names
the host, port and database role. It is listed P3 rather than alerted on for a
blunt reason: **it only fires when something actually requests `/health/ready/`,
and nothing currently does** (Render probes `/health/`, and monitors are pointed
there on purpose so they do not hold Neon awake). Wire a rule to it only if you
also start probing the endpoint.

`admin_login_rate_limited` sits in P3 for the same reason `rate_limited` does —
the limit doing its job is not an incident — but read it differently on a
dashboard. Nothing legitimate retries at `/admin/login/`, so unlike the API tiers
it has no benign explanation in a NAT'd office; its `tier` field says whether the
per-minute (`admin_login`) or daily (`admin_login_daily`) bucket filled.

The three P3 reCAPTCHA events are the filter **working**, so none of them is an
alert: `recaptcha_rejected` (Google said no), `recaptcha_low_score` (scored under
the threshold for its action) and `recaptcha_action_mismatch` (a token minted for
a different form on the shared site key). A rejection is the system doing its
job. Read `recaptcha_action_mismatch` on a dashboard rather than ignoring it,
though — one key pair covers every public form, so a sustained run of it is
someone harvesting tokens from the cheap page to replay against a costlier one,
which is the exact attack the action check exists to stop.

## Why the reCAPTCHA failures cannot be a deploy-time check

The three alerted reCAPTCHA events all describe verification **silently not
happening**, which is normally an argument for catching it at boot instead of at
runtime. That is not available here.

`recaptcha_v2_key_in_use` fires because the siteverify response carried no
`score` field, and a v2 and a v3 secret are indistinguishable as strings. Nor can
you probe for it at startup: siteverify only returns `score` on a **successful**
verification, so a dummy token tells you whether the secret is valid and never
which version it is. The first real submission after a deploy is the earliest
possible signal that the thresholds stopped running — hence a P1 rule rather than
a settings guard.

One state is invisible to every rule below: if `RECAPTCHA_SECRET_KEY` is **blank**
in production, `verify_recaptcha` returns `True` on its first line and the view
skips the call entirely, so none of these six events can ever fire. That is by
design — `config/settings.py` deliberately exempts this key from the
fail-at-boot block that covers `CACHE_REDIS_URL`, because the app must start
without it — but it means "no reCAPTCHA events at all" reads as healthy and is
not. Confirm the key is set when reading these panels; silence here is only
evidence of health if verification is switched on.

## Why the heartbeat exists

`run_scheduled` used to write only to stdout, so a cron service that was paused,
deleted, or given a mistyped schedule produced **no signal of any kind**. Nothing
failed; nothing happened. The `scheduled_group_completed` line turns that silence
into an *absence*, which Grafana can alert on with `absent_over_time`.

Each group needs its own absence window, generous enough not to fire on one
skipped run:

| Group | Cron | Absence window |
|---|---|---|
| `notification_retry` | `*/10 * * * *` | 30m |
| `daily_maintenance` | `0 8 * * *` | 26h |
| `weekly_maintenance` | `0 3 * * 1` | 8d |

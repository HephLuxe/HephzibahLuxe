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
| `brevo_outage` | ERROR | `notifications/services.py` | The email pipeline is down. Transition-only, so a multi-hour outage pages once, not once per failed send. |
| `scheduled_group_failed` | ERROR | `core/management/commands/run_scheduled.py` | A cron group had a failing task. |
| *(absence of)* `scheduled_group_completed` | INFO | same | **A cron service stopped running at all.** A failed run is visible twice over; a run that never happens produces nothing. `notification_retry` is the only retry path for failed email and the only sender of debounced event-details mail. |

### P2 — notify

| Event | Level | Emitted by | Why |
|---|---|---|---|
| `brevo_send_failed` | WARNING | `notifications/services.py` | Individual sends failing without the circuit tripping. Alerts on a burst, not one. |
| `unknown_timezone` | ERROR | `core/timezones.py` | A stored IANA name no longer resolves; that account's digests silently fall back to UTC. |
| `login_account_locked` | WARNING | `accounts/views.py` | One or two is a person who forgot a password. A spike is a credential-stuffing campaign. |
| `admin_login_account_locked` | WARNING | `core/admin_login.py` | The **admin door**, so this one alerts on any occurrence rather than a spike. `/admin/login/` has a handful of human users and `role=admin` forces `is_superuser`, so one lockout is either an operator needing the break-glass or someone guessing at the control plane. The `has_account` field separates the two. |
| `background_timer_cap` | WARNING | `core/background.py` | The in-process timer cap was hit. Costs punctuality only (the cron sweep still delivers), but it means something is arming far more timers than expected. |

### P3 — dashboard only

`rate_limited`, `admin_login_rate_limited`, `throttle_near_limit`,
`login_tier_exhausted`, `reset_code_rejected`, `inquiry_dedupe_hit`,
`event_details_dispatched`, `notifications_purged`, `reset_tokens_pruned`,
`brevo_recovered`, `brevo_send_deferred`, `health_dependency_down`.

Two are worth a **dashboard panel** even though they are not alerts:

- `rate_limited` broken down by `path` — the only way to tell whether a limit is
  mis-tuned before someone complains. Its `retry_after` field separates "clicking
  too fast" (60) from "hit a daily cap" (tens of thousands).
- `throttle_near_limit` — the leading signal, fired at 80% occupancy, before
  anyone is refused.

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

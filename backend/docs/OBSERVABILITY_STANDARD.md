# Observability Standard

The single, codebase-agnostic contract for how every backend I run emits
telemetry into my self-hosted monitoring stack. Write it once here, carry it to
every repo. Hepz is the reference implementation; the Kofis `store_front api` is
a close sibling.

> **One principle above all:** the app **emits** signals — structured logs,
> error/trace events, health — and the monitoring stack **decides** alerts. The
> app never hardcodes a Telegram / SMS / ntfy call. That keeps alerting policy
> (who gets paged, when, how loudly) out of application code and in Grafana /
> GlitchTip where it belongs.

---

## 1. The stack it feeds

Self-hosted on the home server (Proxmox `atlas`), **VM 103 `dev-infra`
@ 192.168.2.21**, Docker:

| Signal | Sink | Endpoint |
|---|---|---|
| Errors + traces | **GlitchTip** (Sentry-compatible) | `http://192.168.2.21:8002` |
| Structured logs | **Loki** (viewed in **Grafana**) | push: `http://192.168.2.21:3100/loki/api/v1/push` (basic auth) · Grafana `:3000` |
| Alerts → phone | **ntfy** | topics `grafana-alerts`, `glitchtip-issues` |

Deploys land on **VM 100 `webserver` @ 192.168.2.20**, same LAN — so a home
deploy reaches the stack directly over the LAN, no tunnel. See §7 for cloud
(Render/Railway) deploys.

Alerting is wired **in the stack**, not the app: GlitchTip issue-alert → ntfy
`glitchtip-issues`; a Grafana alert rule on a Loki query (e.g.
`{service="…"} | event="brevo_outage"`) → ntfy `grafana-alerts`.

---

## 2. What the app must implement

Everything is **env-gated**: an unset DSN / Loki URL / cache URL makes that sink
a silent no-op, so local, CI, and test stay quiet with zero config.

1. **Correlation** — an `X-Request-ID` per request (inbound header or generated),
   held in a `ContextVar`, echoed on the response, and propagated into Celery
   tasks so a request + the jobs it enqueues share one id.
   *(Hepz: `apps/core/middleware.py` + the Celery handlers in
   `apps/core/observability.py`.)*
2. **Structured logging** — one JSON object per line to stdout, stable fields
   `timestamp, level, logger, message, request_id, user_id`, plus a reserved
   **`event`** key for notable signals. A shared `scrub()` redacts secrets
   (matched by substring against a sensitive-key list) on both the log path and
   the Sentry `before_send`. Console format is allowed **only** in local dev.
   *(Hepz: `apps/core/logging.py`.)*
3. **Error / trace capture** — DSN-guarded Sentry SDK pointed at GlitchTip,
   `send_default_pii=False`, `before_send=scrub`, Django+Celery+Redis
   integrations, `traces_sample_rate` + `release` from env.
   *(Hepz: `apps/core/observability.py:init_sentry`.)*
4. **Health endpoints** — `GET /health/` (liveness, no I/O, **no auth**, no DB
   dependency) and `GET /health/ready/` (checks DB, and cache if configured;
   503 on failure), mounted **outside** any API version prefix. `/health/` is a
   hard requirement of the home-server control-panel onboarding wizard.
   *(Hepz: `apps/core/views.py` + `config/urls.py`.)*
5. **Scheduling** — `django-celery-beat` with the **DatabaseScheduler**, so
   every periodic task's timing is admin-editable and matches the home server's
   `celerybeat@.service` unit (which hardcodes that scheduler). Ship defaults via
   an idempotent `seed_periodic_tasks` management command; run it after `migrate`
   on every deploy. **A static `CELERY_BEAT_SCHEDULE` is forbidden** — the home
   `celerybeat@` unit silently ignores it, stranding every periodic task.
6. **Event taxonomy** — attach `event="<slug>"` to any log line an alert rule
   should match (e.g. `brevo_outage`, `brevo_recovered`, `brevo_send_failed`).
   Alert on the label, never on message text.

---

## 3. Environment variables (the whole contract)

| Var | Required? | Meaning |
|---|---|---|
| `LOG_FORMAT` | optional (`console`) | `json` for real deploys → Loki/Grafana; `console` locally. |
| `LOG_LEVEL` | optional (`INFO`) | Root log level. |
| `SENTRY_ENVIRONMENT` | optional (`development`) | Tags every event; **also** the Loki `environment` label. Use `homelab` / `production` / `development` to separate deploys on one stack. |
| `SENTRY_DSN` | optional (blank ⇒ off) | GlitchTip project DSN. |
| `SENTRY_TRACES_SAMPLE_RATE` | optional (`0.1`) | 0.0–1.0. |
| `SENTRY_RELEASE` | optional | Usually the git SHA, set by CI. |
| `LOKI_PUSH_URL` | optional (blank ⇒ off) | Loki push endpoint. Blank = no Loki handler is even constructed. |
| `LOKI_PUSH_USER` / `LOKI_PUSH_PASSWORD` | optional | HTTP basic auth for Loki. |
| `REDIS_URL` | recommended | Redis for Celery (broker + result backend). |
| `CACHE_REDIS_URL` | optional (blank ⇒ LocMem) | Redis for the Django cache — a **separate** DB index from the broker; backs rate-limit counters + `/health/ready`. |

Every var must appear in `.env.example` with a `(required)`/`(optional …)` marker
**and** be read in settings under the *same* name — the control panel builds its
onboarding form from `.env.example` and from the code's actual reads, and a name
mismatch silently falls back to a default.

---

## 4. Loki is a first-class prod sink (the one evolution)

The older Kofis wiring treats Loki push as *dev-only* ("unset in production; rely
on the platform log view") and keeps `python-logging-loki` in
`requirements-dev.txt`. **This standard reverses that:** Loki push is a
first-class, env-gated sink in *every* environment, so home and cloud deploys
emit identically into my own Grafana. Consequence: `python-logging-loki` is a
**main** dependency, not dev-only.

> Porting to an existing repo that followed the old rule (e.g. `default-store`):
> no code change is needed to *enable* Loki — just set `LOKI_PUSH_URL` — but make
> sure `python-logging-loki` is in the **installed** prod dependency set, or the
> handler fails to construct when the URL is set.

The app pushes logs via `logging_loki.LokiQueueHandler` (async, in-memory queue —
so it never blocks a request, but a buffered batch is lost if the process dies or
the sink is unreachable; treat a home-only sink as best-effort, see §7).

---

## 5. Adoption checklist (new repo)

1. Add deps: `sentry-sdk`, `python-json-logger`, `python-logging-loki`,
   `django-celery-beat`, `django-redis`, `requests`.
2. Copy `apps/core/{middleware,logging,observability,views}.py`; rename the
   JSON formatter to `<App>JsonFormatter`; set the Loki `service` tag.
3. Register `RequestIDMiddleware` right after `SecurityMiddleware`; import
   `apps.core.observability` from the core `AppConfig.ready()` so the Celery
   correlation handlers connect even with Sentry off.
4. Add the settings block: build `LOGGING` via `build_logging_config(...)`, call
   `init_sentry(...)` only when `SENTRY_DSN` is set, add the optional
   `django-redis` cache gated on `CACHE_REDIS_URL` (its own Redis DB, separate
   from the Celery broker).
5. Add `django_celery_beat` to `INSTALLED_APPS`, set
   `CELERY_BEAT_SCHEDULER='django_celery_beat.schedulers:DatabaseScheduler'`,
   delete any static `CELERY_BEAT_SCHEDULE`, and add a `seed_periodic_tasks`
   command with the shipped defaults.
6. Mount `/health/` + `/health/ready/` outside the API prefix (plain Django
   views — no DRF auth/throttle).
7. Add every env var to `.env.example` with markers.
8. Deploy runs `migrate` → `seed_periodic_tasks` (both idempotent).

---

## 6. Worked example — Brevo outage detection

The first real signal wired into this standard (Hepz `apps/notifications`). It
shows the intended shape: **detect, then emit; let the stack alert.**

- **Passive** — the real send path (`services.send_now`) records every failure on
  a `ServiceHealthState` row and, on the up→down transition, emits **one**
  `ERROR event=brevo_outage` + a Sentry message. Because escalation is
  transition-only, a multi-hour outage produces exactly one alert, not one per
  failed email. A success on the down→up transition emits
  `INFO event=brevo_recovered`.
- **Active** — `tasks.brevo_health_probe_task` (beat, ~5 min, admin-gated by
  `ScheduledTaskSettings`) pings Brevo's account endpoint + status page and
  updates the same `ServiceHealthState`, so an outage is caught *before* an email
  is sent.
- **Safeguard** — while `ServiceHealthState` is `down`, sends are parked without
  burning the retry budget; the hourly sweep + a drain on recovery re-deliver the
  backlog automatically.

Alert rules (in the stack, not the repo): GlitchTip fires on the new issue →
ntfy `glitchtip-issues`; a Grafana rule on `{service="hephzibah-api"} |
event="brevo_outage"` → ntfy `grafana-alerts`.

---

## 7. Getting telemetry home from cloud deploys

The app is transport-agnostic — it only needs `SENTRY_DSN` and `LOKI_PUSH_URL`
reachable. Transport is an ops/env choice, not code.

- **Home deploy (VM 100):** same LAN as VM 103 — set the `192.168.2.21` LAN URLs,
  done. No tunnel, no public exposure, no certs.
- **Cloud deploy (Render / Railway):** the stack is LAN-only and the Bell router
  permanently blocks port 80, so pick one:
  1. **Tailscale Funnel on VM 103** (recommended) — no port-forward, bypasses the
     port-80 block, auto-HTTPS, stable `*.ts.net` URL; the cloud app just pushes
     there.
  2. **Reuse VM 100's nginx (443)** as a reverse proxy to `192.168.2.21:8002/3100`
     — no new software, reuses the existing DuckDNS + DNS-01 cert pattern, but
     adds public surface and routes prod telemetry through the app VM.
  3. **A small always-on cloud VPS relay** — most production-grade; survives a
     home uplink/power outage (exactly when you want telemetry).
- **Availability caveat:** a home-only sink depends on home power/internet/dynamic
  IP, and the app's log/error buffers are in-memory (lost if the sink is
  unreachable). For a genuine production posture put the *ingress* on option 3 and
  keep the platform's native log stream as a fallback; separate environments by
  the `SENTRY_ENVIRONMENT` tag rather than duplicating the whole stack.

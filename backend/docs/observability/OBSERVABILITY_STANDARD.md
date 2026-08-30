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
   held in a `ContextVar`, echoed on the response, and carried into background
   work so a request + the jobs it dispatches share one id.
   How that propagation happens depends on where the work runs, and this is the
   part that is easy to get silently wrong:
   - **In-process (thread pool):** a `ContextVar` is **not** inherited by a new
     thread, so the dispatcher must `contextvars.copy_context()` and run the job
     inside it. Without that, every background log line loses its `request_id`
     and the app *appears* compliant because nothing errors.
   - **Across a broker:** stamp the id as a message header on publish and restore
     it into the consumer's `ContextVar` before the job body runs.
   *(Hepz: `apps/core/middleware.py` + `apps/core/background.py`. It previously
   did the broker variant, via three Celery signal handlers in
   `apps/core/observability.py`; those are gone with Celery.)*
2. **Structured logging** — one JSON object per line to stdout, stable fields
   `timestamp, level, logger, message, request_id, user_id`, plus a reserved
   **`event`** key for notable signals. A shared `scrub()` redacts secrets
   (matched by substring against a sensitive-key list) on both the log path and
   the Sentry `before_send`. Console format is allowed **only** in local dev.
   *(Hepz: `apps/core/logging.py`.)*
3. **Error / trace capture** — DSN-guarded Sentry SDK pointed at GlitchTip,
   `send_default_pii=False`, `before_send=scrub`, integrations for whatever the
   repo actually runs (Hepz: Django + Redis), `traces_sample_rate` + `release`
   from env. **Background work must be captured explicitly:** a thread that
   raises disappears without a trace, which is worse than a broker — so the
   dispatcher catches broadly, logs with `exc_info`, and lets Sentry's logging
   integration report it.
   *(Hepz: `apps/core/observability.py:init_sentry`, `apps/core/background.py`.)*
4. **Health endpoints** — `GET /health/` (liveness, no I/O, **no auth**, no DB
   dependency) and `GET /health/ready/` (checks DB, and cache if configured;
   503 on failure), mounted **outside** any API version prefix. `/health/` is a
   hard requirement of the home-server control-panel onboarding wizard.
   *(Hepz: `apps/core/views.py` + `config/urls.py`.)*
5. **Scheduling** — **the scheduler must not poll a billed store.** This clause
   replaces an earlier one that mandated `django-celery-beat` with the
   DatabaseScheduler, on the strength of admin-editable timing. That was the right
   trade against an always-on database and the wrong one against serverless
   Postgres: the scheduler's own 5-second poll of
   `django_celery_beat_periodictasks` kept the compute awake permanently, *with
   every task disabled*, which cost more than admin-editable timing was worth.

   Use the platform's own cron to invoke a command that runs one group of tasks
   and **exits** (Hepz: `manage.py run_scheduled <group>`,
   `docs/adr/0001-remove-celery.md`). Requirements:
   - Group by cadence, not one cron entry per job.
   - A failing task must not strand the rest of its group; exit **non-zero** if
     any failed, so the platform's own run history is the alerting surface.
   - Keep the per-task admin **on/off** switch (Hepz:
     `notifications.ScheduledTaskSettings`, checked as each task's first
     statement). Timing moves to the cron schedule; the kill switch should not.
   - **Every deferred task needs a durable status field and a sweep that
     re-drives it.** A task that can only be triggered once will be lost, and
     that is a telemetry problem as much as a correctness one: work that vanishes
     emits nothing at all.
6. **Event taxonomy** — attach `event="<slug>"` to any log line an alert rule
   should match (e.g. `brevo_outage`, `brevo_recovered`, `brevo_send_failed`,
   `brevo_send_deferred`, `notifications_purged`, `event_details_dispatched`,
   `reset_tokens_pruned`, `reset_code_rejected`, `unknown_timezone`,
   `inquiry_no_recipients`).

   Two of those are worth calling out as the pattern for fail-soft code paths.
   `reset_code_rejected` carries `attempt_count` and `token_burned`, so a
   distributed guessing attempt is visible as a rate rather than as individual
   noise. `unknown_timezone` fires where the code deliberately degrades to a
   default instead of raising — a silent fallback that emits nothing is
   indistinguishable from correct behaviour, which is how misconfiguration
   survives for months.
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
| `CACHE_REDIS_URL` | optional (blank ⇒ LocMem) | Redis for the Django cache; backs rate-limit counters + `/health/ready`. Give it its own DB index on a shared instance. Required whenever `DEBUG=False` (LocMem is per-process, so multi-worker rate limits silently multiply). |

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
   `django-redis`, `requests`.
2. Copy `apps/core/{middleware,logging,observability,views}.py`; rename the
   JSON formatter to `<App>JsonFormatter`; set the Loki `service` tag.
3. Register `RequestIDMiddleware` right after `SecurityMiddleware`.
4. Add the settings block: build `LOGGING` via `build_logging_config(...)`, call
   `init_sentry(...)` only when `SENTRY_DSN` is set, add the `django-redis` cache
   gated on `CACHE_REDIS_URL` (its own Redis DB index).
5. Copy `apps/core/background.py` and enable async dispatch from `wsgi.py`
   **only** — a short-lived process must run deferred work inline rather than
   hand it to a pool that dies with the command. Verify the `contextvars` copy in
   §2.1: it is the whole of correlation for background work.
6. Copy `apps/core/management/commands/run_scheduled.py`, define the groups, and
   create one platform-cron service per group.
7. Mount `/health/` + `/health/ready/` outside the API prefix (plain Django
   views — no DRF auth/throttle).
8. Add every env var to `.env.example` with markers.
9. Deploy runs `migrate` (idempotent) as the **web** service's release step only —
   never from a cron service, or two processes race a fresh database.

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

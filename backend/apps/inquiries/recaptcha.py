"""
apps/inquiries/recaptcha.py

Google reCAPTCHA **v3** verification for public endpoints. Entirely env-gated:
with no RECAPTCHA_SECRET_KEY configured this is a no-op that returns True, so
local dev, CI and tests need no config and boot is never blocked.

**Why the version matters.** A v2 checkbox token is a challenge a human solved,
so `success: true` IS the verdict and reading nothing else is correct. A v3
token is minted silently by JavaScript on page load, and there `success: true`
only means "this token parsed, has not expired, has not been redeemed before,
and was issued for this site key" — every bot that can drive a headless browser
gets one. The verdict is `score` (0.0 = almost certainly a bot, 1.0 = almost
certainly human), so a v3 integration that reads only `success` accepts
everything. That is what this module used to do.

Two more checks exist only because of v3:

* **action** — the frontend passes an action name to
  ``grecaptcha.execute(siteKey, {action: "..."})`` and Google echoes it back.
  It is checked because ONE site key covers every form on the domain: without
  it, a token minted on the cheapest public page is replayable against the most
  valuable endpoint (harvest one from the inquiry form, post it to login). This
  check is what makes sharing a key pair across surfaces safe, so any new caller
  MUST pass its own action and register it in RECAPTCHA_MIN_SCORES.

* **remoteip** — optional in Google's API and sent because it feeds their model.
  Sourced from apps.core.ratelimit.resolve_client_ip at the call site, never
  from REMOTE_ADDR directly; see that module's docstring.

`hostname` is deliberately NOT checked here. The key's domain allowlist in the
reCAPTCHA console already enforces it (as long as "Verify the origin of
reCAPTCHA solutions" stays on), and duplicating it in code is one more place to
edit the day a second domain is added.
"""

from __future__ import annotations

import logging

import requests
from django.conf import settings

logger = logging.getLogger(__name__)

RECAPTCHA_VERIFY_URL = "https://www.google.com/recaptcha/api/siteverify"
# Seconds before a hung siteverify call is abandoned — without this a stalled
# request from Google can pin a gunicorn worker on a PUBLIC endpoint.
RECAPTCHA_REQUEST_TIMEOUT = 5

# The action names this project mints tokens for. The string must match what the
# frontend passes to grecaptcha.execute() EXACTLY — a typo on either side reads
# as a replayed token and rejects every submission, so both sides quote this
# constant rather than a literal.
ACTION_SUBMIT_INQUIRY = "submit_inquiry"


def min_score_for(action: str) -> float:
    """The score `action` must clear, from settings, falling back to the default.

    Per-action rather than one global number because the right threshold is a
    function of what a false reject costs. On lead capture that cost is a lead
    the business never learns it had, so the bar stays low; on an endpoint where
    a rejected human simply retries, it can go higher. Tune from the score
    distribution in the reCAPTCHA console, not from a guess — that is why these
    are env vars.
    """
    return settings.RECAPTCHA_MIN_SCORES.get(action, settings.RECAPTCHA_MIN_SCORE_DEFAULT)


def verify_recaptcha(token: str, *, action: str, remote_ip: str | None = None) -> bool:
    """
    Return True if the token is acceptable for `action`.

    Fails OPEN on any network/transport error: losing a real lead to a Google
    outage is worse than accepting a spam one, and the endpoint is rate-limited
    regardless. Only an explicit verdict from Google — "success: false", the
    wrong action, or a score under the threshold — is a rejection.

    **A caller that is not lead capture should re-examine that.** Fail-open is
    right here because the submission is unrecoverable if wrongly dropped; on an
    authentication endpoint it means the bot defence silently disappears for the
    duration of an outage, which may or may not be the trade you want.
    """
    if not settings.RECAPTCHA_SECRET_KEY:
        return True

    payload = {"secret": settings.RECAPTCHA_SECRET_KEY, "response": token}
    # Omitted rather than sent empty when unknown: Google treats a malformed
    # remoteip as a bad request, and this field is an optional hint either way.
    if remote_ip:
        payload["remoteip"] = remote_ip

    try:
        response = requests.post(
            RECAPTCHA_VERIFY_URL,
            data=payload,
            timeout=RECAPTCHA_REQUEST_TIMEOUT,
        )
        response.raise_for_status()
        body = response.json()
    except (requests.RequestException, ValueError) as exc:
        logger.warning(
            "reCAPTCHA verification could not reach Google — allowing the submission.",
            extra={"event": "recaptcha_unreachable", "recaptcha_error": str(exc)},
        )
        return True

    if not isinstance(body, dict) or not body.get("success"):
        # error-codes distinguishes the cases that matter operationally:
        # `invalid-input-secret` is a misconfigured deploy, `timeout-or-duplicate`
        # is an expired (v3 tokens live ~2 minutes) or already-redeemed token,
        # which is what a replay looks like. Logged, never returned to the
        # caller — the 400 stays a fixed message.
        logger.info(
            "reCAPTCHA rejected a submission.",
            extra={
                "event": "recaptcha_rejected",
                "recaptcha_action": action,
                "recaptcha_error_codes": (body or {}).get("error-codes") if isinstance(body, dict) else None,
            },
        )
        return False

    # No score field means the secret belongs to a v2 key, not a v3 one. That is
    # a provisioning mistake rather than an attack, and for a v2 key `success`
    # genuinely IS the verdict (a human solved a challenge), so accept — but
    # shout, because every threshold below is silently doing nothing.
    if "score" not in body:
        logger.error(
            "reCAPTCHA returned no score — the configured secret looks like a v2 key, "
            "so score thresholds and action checks are not being enforced.",
            extra={"event": "recaptcha_v2_key_in_use", "recaptcha_action": action},
        )
        return True

    if body.get("action") != action:
        # The token was minted for a different form on this same site key. With
        # one key pair across the site this is the only thing standing between a
        # token harvested from a public page and a higher-value endpoint.
        logger.warning(
            "reCAPTCHA action mismatch — token was minted for a different form.",
            extra={
                "event": "recaptcha_action_mismatch",
                "recaptcha_expected_action": action,
                "recaptcha_returned_action": body.get("action"),
            },
        )
        return False

    threshold = min_score_for(action)
    try:
        score = float(body["score"])
    except (TypeError, ValueError):
        logger.error(
            "reCAPTCHA returned an unparseable score.",
            extra={"event": "recaptcha_bad_score", "recaptcha_score": body.get("score")},
        )
        return True

    if score < threshold:
        logger.info(
            "reCAPTCHA score below the threshold for this action.",
            extra={
                "event": "recaptcha_low_score",
                "recaptcha_action": action,
                "recaptcha_score": score,
                "recaptcha_threshold": threshold,
            },
        )
        return False

    return True

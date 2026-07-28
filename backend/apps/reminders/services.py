"""
apps/reminders/services.py

Business logic for reminders. Views stay thin: validate → call a service →
serialize → respond. Permission enforcement is the view's job (staff vs client);
these functions assume the caller is already authorised.
"""

from __future__ import annotations

from django.db.models import Case, IntegerField, QuerySet, Value, When
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from apps.core import deeplinks
from apps.core.utils import stamp_attribution

from .models import PRIORITY_WEIGHT, Reminder

# Query-param values accepted by list_reminders(status=...).
STATUS_PENDING = "pending"
STATUS_COMPLETED = "completed"
STATUS_ALL = "all"

# Query-param values accepted by list_reminders(sort=...).
SORT_PRIORITY = "priority"   # default — high → low, then soonest due
SORT_DUE = "due"             # soonest due date first
SORT_NEWEST = "newest"       # most recently created first
SORT_OLDEST = "oldest"


def list_reminders(engagement, *, status: str = STATUS_PENDING, sort: str = SORT_PRIORITY) -> QuerySet[Reminder]:
    """
    Return the reminders for an engagement, filtered by completion status and
    ordered per the requested sort. Returns an empty queryset if engagement is
    None (a portal with no active engagement is simply "all caught up").
    """
    if engagement is None:
        return Reminder.objects.none()

    qs = engagement.reminders.all()

    if status == STATUS_PENDING:
        qs = qs.filter(is_completed=False)
    elif status == STATUS_COMPLETED:
        qs = qs.filter(is_completed=True)
    # STATUS_ALL → no filter

    if sort == SORT_NEWEST:
        return qs.order_by("-created_at")
    if sort == SORT_OLDEST:
        return qs.order_by("created_at")
    if sort == SORT_DUE:
        return qs.order_by("due_date", "-created_at")

    # SORT_PRIORITY (default): annotate a numeric weight so high sorts before
    # medium before low, then break ties by soonest due date.
    weight_when = [When(priority=p, then=Value(w)) for p, w in PRIORITY_WEIGHT.items()]
    return qs.annotate(
        _weight=Case(*weight_when, default=Value(99), output_field=IntegerField())
    ).order_by("is_completed", "_weight", "due_date", "-created_at")


def _apply_target(reminder_fields: dict, engagement, target_type, target_id) -> None:
    """
    Translate the API's (target_type, target_id) into the GenericFK columns,
    in place. Passing both as None/empty clears any existing target.
    """
    from django.contrib.contenttypes.models import ContentType

    if not target_type and not target_id:
        return

    if bool(target_type) != bool(target_id):
        raise ValidationError("target_type and target_id must be provided together.")

    target = deeplinks.resolve_target(engagement, target_type, target_id)
    reminder_fields["target_content_type"] = ContentType.objects.get_for_model(target)
    reminder_fields["target_object_id"] = str(target.pk)


def create_reminder(engagement, created_by, validated_data: dict) -> Reminder:
    """
    Create a reminder for an engagement (staff action) and email the client
    immediately. Unlike payment-due/meeting-prep (which are genuinely
    periodic lookahead scans — there's no single "creation" moment to hang a
    notification off), a new reminder has a clear trigger and should notify
    right away rather than wait for the next beat tick.

    `validated_data` may carry `target_type`/`target_id` (see resolve_target) —
    they are translated into the GenericFK columns rather than stored as-is.
    """
    fields = dict(validated_data)
    target_type = fields.pop("target_type", None)
    target_id = fields.pop("target_id", None)
    _apply_target(fields, engagement, target_type, target_id)

    reminder = Reminder.objects.create(
        engagement=engagement,
        created_by=created_by,
        last_updated_by=created_by,
        **fields,
    )

    from apps.notifications.services import queue_notification

    # The email needs an ABSOLUTE url — a relative portal route is meaningless
    # in an inbox. absolute_url() returns None when FRONTEND_BASE_URL is unset,
    # and the renderer then falls back to its "log in to your portal" copy
    # rather than shipping a broken href.
    link_url = deeplinks.absolute_url(reminder.resolved_link_url)

    queue_notification(
        recipient_email=engagement.portal.user.email,
        recipient_user=engagement.portal.user,
        engagement=engagement,
        template_name="new_reminder",
        context={
            "title": reminder.title,
            "description": reminder.description,
            "priority_display": reminder.get_priority_display(),
            "due_date": str(reminder.due_date) if reminder.due_date else None,
            "link_url": link_url,
            "link_label": reminder.resolved_link_label if link_url else None,
        },
    )
    return reminder


def update_reminder(reminder: Reminder, validated_data: dict, updated_by=None) -> Reminder:
    """
    Staff edit. Handles retargeting the deep link:
      * `target_type` + `target_id` together  -> re-point at that object
      * `target_type: null` (or "")           -> clear the target entirely
      * neither key present                   -> target left untouched
    The target is re-validated against the reminder's own engagement, so an
    edit cannot smuggle in another client's object any more than a create can.
    """
    fields = dict(validated_data)
    has_target_key = "target_type" in fields or "target_id" in fields
    target_type = fields.pop("target_type", None)
    target_id = fields.pop("target_id", None)

    if has_target_key:
        if not target_type and not target_id:
            reminder.target_content_type = None
            reminder.target_object_id = None
        else:
            target_fields: dict = {}
            _apply_target(target_fields, reminder.engagement, target_type, target_id)
            reminder.target_content_type = target_fields["target_content_type"]
            reminder.target_object_id = target_fields["target_object_id"]

    for field, value in fields.items():
        setattr(reminder, field, value)

    # is_completed via PATCH must still stamp completed_at, same as the
    # dedicated /complete/ endpoint does — otherwise the two write paths
    # disagree about what a "completed" reminder looks like.
    if "is_completed" in fields:
        reminder.completed_at = timezone.now() if reminder.is_completed else None

    stamp_attribution(reminder, updated_by, creating=False)
    reminder.save()
    return reminder


def set_completed(reminder: Reminder, is_completed: bool, updated_by=None) -> Reminder:
    """Toggle a reminder's completion state, stamping completed_at."""
    reminder.is_completed = is_completed
    reminder.completed_at = timezone.now() if is_completed else None
    update_fields = ["is_completed", "completed_at", "updated_at"]
    if stamp_attribution(reminder, updated_by, creating=False):
        update_fields.append("last_updated_by")
    reminder.save(update_fields=update_fields)
    return reminder

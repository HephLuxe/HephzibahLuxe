"""
apps/reminders/models.py

The Reminders widget (sidebar, every page). A reminder is a staff-authored,
client-facing to-do scoped to an engagement — priority + optional due date +
optional deep-link into another portal page ("Complete Project Kickoff
Questionnaire"). Clients mark them complete; staff manage them.

Empty state ("You're All Caught Up") is a frontend concern — the API simply
returns an empty list when nothing is pending.
"""

from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.db import models

from apps.core import deeplinks
from apps.core.models import AttributedModel, UUIDTimestampedModel


class ReminderPriority(models.TextChoices):
    HIGH = "high", "High Priority"
    MEDIUM = "medium", "Medium Priority"
    LOW = "low", "Low Priority"


# Numeric weight for ordering (TextChoices sort alphabetically, which is wrong
# for priority). high → 0 surfaces first. Used by services.list_reminders.
PRIORITY_WEIGHT = {
    ReminderPriority.HIGH: 0,
    ReminderPriority.MEDIUM: 1,
    ReminderPriority.LOW: 2,
}


class Reminder(UUIDTimestampedModel, AttributedModel):
    engagement = models.ForeignKey(
        "portal.EventEngagement",
        on_delete=models.CASCADE,
        related_name="reminders",
        null=True,
        blank=True,
    )
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    priority = models.CharField(
        max_length=10,
        choices=ReminderPriority.choices,
        default=ReminderPriority.MEDIUM,
    )
    due_date = models.DateField(null=True, blank=True)
    is_completed = models.BooleanField(default=False)
    completed_at = models.DateTimeField(null=True, blank=True)

    # ── Deep link ────────────────────────────────────────────────
    # Preferred: point at the actual object (a conversation, a prep item, an
    # invoice, ...) and let apps.core.deeplinks derive the route. Storing the
    # target rather than a staff-typed URL is what lets us guarantee the link
    # resolves to a real row and belongs to THIS engagement — a typed string
    # can silently carry another client's id (see reminders.services.resolve_target).
    #
    # object_id is a CharField, not a UUIDField/PositiveIntegerField: targets
    # have mixed PK types (Event/BudgetPayment... vs the UUID-PK models), the
    # same reason documents.Document uses one.
    target_content_type = models.ForeignKey(
        ContentType, on_delete=models.CASCADE, null=True, blank=True, related_name="+",
    )
    target_object_id = models.CharField(max_length=255, null=True, blank=True)
    target = GenericForeignKey("target_content_type", "target_object_id")

    # Fallback for links that have no object behind them (a static page, an
    # external URL). Ignored for display when `target` is set — see link_url
    # below. CharField, not URLField: it may hold an internal portal route.
    link_url = models.CharField(max_length=500, blank=True)
    # Optional override of the target's default label (deeplinks.default_label).
    link_label = models.CharField(max_length=255, blank=True)

    order = models.IntegerField(default=0)
    # created_by / last_updated_by come from AttributedModel (apps/core/models.py).

    class Meta:
        # Default surface order: pending first, then soonest due, then newest.
        # Priority-weighted sort is applied in services when requested.
        ordering = ["is_completed", "order", "due_date", "-created_at"]
        indexes = [
            models.Index(fields=["engagement", "is_completed"]),
        ]

    def __str__(self) -> str:
        return f"{self.title} ({self.get_priority_display()})"

    @property
    def priority_weight(self) -> int:
        return PRIORITY_WEIGHT.get(self.priority, 99)

    @property
    def resolved_link_url(self) -> str | None:
        """
        The URL the client should actually be sent to.

        Derived from `target` when one is set, so a renamed portal route is a
        one-line change in core.deeplinks rather than a data migration. Falls
        back to the free-text link_url (static pages, external URLs, and every
        reminder created before targets existed).

        Returns None when the target has been deleted out from under us and
        there is no fallback — the card then renders with no link, instead of
        offering the client a link that 404s.
        """
        target = self.target
        if target is not None:
            return deeplinks.build_url(target) or (self.link_url or None)
        return self.link_url or None

    @property
    def resolved_link_label(self) -> str | None:
        """Staff's label wins; otherwise the target's sensible default."""
        if self.link_label:
            return self.link_label
        target = self.target
        if target is not None:
            return deeplinks.default_label(target)
        return None

    @property
    def target_type(self) -> str | None:
        """Public slug for the linked object's kind (e.g. "conversation")."""
        target = self.target
        return deeplinks.type_for_instance(target) if target is not None else None

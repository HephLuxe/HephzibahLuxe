import uuid
from django.db import models

from apps.core.models import AttributedModel
from apps.core.utils import budget_receipt_upload_path  # you'll add this to core/utils.py


class BudgetCategoryName(models.TextChoices):
    VENUE = "venue", "Venue & Location"
    CATERING = "catering", "Catering"
    PHOTOGRAPHY = "photography", "Photography & Videography"
    DECOR = "decor", "Decor & Design"
    MUSIC = "music", "Music & Entertainment"
    ATTIRE = "attire", "Attire & Accessories"
    STATIONERY = "stationery", "Stationery"
    SOUVENIR = "souvenir", "Souvenir"
    OTHER = "other", "Other"


class PaymentStatus(models.TextChoices):
    PAID = "paid", "Paid"
    PENDING = "pending", "Pending"


class BudgetHealthStatus(models.TextChoices):
    NOT_SET = "not_set", "Not Set"
    ON_TRACK = "on_track", "On Track"
    OVER_BUDGET = "over_budget", "Over Budget"


class EventBudget(AttributedModel):
    """One budget per event — the financial overview."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    event = models.OneToOneField("events.Event", on_delete=models.CASCADE, related_name="budget")
    total_budget = models.DecimalField(max_digits=15, decimal_places=2, default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Budget — {self.event.title}"

    def get_portal_id(self):
        return self.event.celebrant.portal.id  # for upload paths if ever needed

    @property
    def allocated(self):
        """Sum of all estimated amounts across categories."""
        return self.categories.aggregate(total=models.Sum("estimated_amount"))["total"] or 0

    @property
    def spent(self):
        """Sum of all actual amounts across categories."""
        return self.categories.aggregate(total=models.Sum("actual_amount"))["total"] or 0

    @property
    def remaining(self):
        return self.total_budget - self.spent

    @property
    def budget_health_percentage(self):
        """Percentage of total budget that has been spent."""
        if self.total_budget == 0:
            return 0
        return round((self.spent / self.total_budget) * 100, 1)

    @property
    def financial_status(self) -> str:
        """Drives the Overview tab's "On Track" / "Over Budget" badge."""
        if self.total_budget == 0:
            return BudgetHealthStatus.NOT_SET
        return (
            BudgetHealthStatus.OVER_BUDGET
            if self.spent > self.total_budget
            else BudgetHealthStatus.ON_TRACK
        )

    @property
    def payments_made_total(self):
        return self.payments.filter(status=PaymentStatus.PAID).aggregate(total=models.Sum("amount"))["total"] or 0

    @property
    def payments_pending_total(self):
        return self.payments.filter(status=PaymentStatus.PENDING).aggregate(total=models.Sum("amount"))["total"] or 0

    @property
    def payments_made_count(self):
        return self.payments.filter(status=PaymentStatus.PAID).count()

    @property
    def payments_pending_count(self):
        return self.payments.filter(status=PaymentStatus.PENDING).count()


class BudgetCategory(AttributedModel):
    """One row per category in the Budget by Category table."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    budget = models.ForeignKey(EventBudget, on_delete=models.CASCADE, related_name="categories")
    category = models.CharField(max_length=20, choices=BudgetCategoryName.choices)
    estimated_amount = models.DecimalField(max_digits=15, decimal_places=2, default=0)
    actual_amount = models.DecimalField(max_digits=15, decimal_places=2, default=0)
    notes = models.TextField(blank=True)   # staff-written budget note
    order = models.IntegerField(default=0)
    updated_at = models.DateTimeField(auto_now=True)

    created_at = models.DateTimeField(auto_now_add=True, null=True)

    class Meta:
        ordering = ["order", "category"]
        unique_together = ("budget", "category")  # one row per category per event

    def __str__(self):
        return f"{self.get_category_display()} — {self.budget.event.title}"

    @property
    def variance(self):
        """
        Positive = over budget (spent more than estimated, shown red),
        negative = under budget (spent less than estimated, shown green).
        Verified against the Figma's 8-row worked example (e.g. Venue:
        estimated 4,000,000 / actual 3,000,000 → -1,000,000, shown green;
        Catering: estimated 1,500,000 / actual 3,000,000 → +1,500,000, shown
        red) — actual minus estimated, not the other way around.
        """
        return self.actual_amount - self.estimated_amount


class BudgetPayment(AttributedModel):
    """One row per payment in the Payment History table."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    budget = models.ForeignKey(EventBudget, on_delete=models.CASCADE, related_name="payments")
    payment_date = models.DateField()
    vendor_item = models.CharField(max_length=255)      # "Crystal Event Decor"
    category = models.CharField(max_length=20, choices=BudgetCategoryName.choices)
    purpose = models.CharField(max_length=255)          # "Deposit", "Full Payment", "Booking Fee"
    amount = models.DecimalField(max_digits=15, decimal_places=2)
    status = models.CharField(max_length=10, choices=PaymentStatus.choices, default=PaymentStatus.PENDING)
    receipt = models.FileField(upload_to=budget_receipt_upload_path, blank=True, null=True, max_length=500)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-payment_date"]

    def __str__(self):
        return f"{self.vendor_item} — {self.amount} ({self.status})"
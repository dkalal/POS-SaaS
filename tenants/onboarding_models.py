from django.db import models

from core.models import TimeStampedModel


class OnboardingProgress(TimeStampedModel):
    tenant = models.OneToOneField("tenants.Tenant", on_delete=models.PROTECT, related_name="onboarding")
    current_step = models.PositiveSmallIntegerField(default=1)
    completed_steps = models.JSONField(default=list, blank=True)
    skipped_steps = models.JSONField(default=list, blank=True)
    completed_at = models.DateTimeField(blank=True, null=True)

    def mark_step(self, step, *, skipped=False):
        values = set(self.completed_steps or [])
        skipped_values = set(self.skipped_steps or [])
        values.add(step)
        if skipped:
            skipped_values.add(step)
        self.completed_steps = sorted(values)
        self.skipped_steps = sorted(skipped_values)
        self.current_step = min(step + 1, 5)

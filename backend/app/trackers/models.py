from django.db import models
from django.utils import timezone
from django.conf import settings


class Tracker(models.Model):
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name="trackers",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
    )
    name = models.CharField(max_length=200)
    source_url = models.URLField(max_length=1000)
    current_url = models.URLField(max_length=1000, blank=True)
    check_interval_minutes = models.PositiveIntegerField(default=60)
    last_checked_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at"]

    def __str__(self):
        return self.name

    @property
    def target_url(self):
        return self.current_url or self.source_url

    @property
    def is_due(self):
        if not self.last_checked_at:
            return True
        next_check = self.last_checked_at + timezone.timedelta(
            minutes=self.check_interval_minutes
        )
        return next_check <= timezone.now()


class Entry(models.Model):
    tracker = models.ForeignKey(
        Tracker, related_name="entries", on_delete=models.CASCADE
    )
    title = models.CharField(max_length=500)
    url = models.URLField(max_length=1000)
    summary = models.TextField(blank=True)
    is_new = models.BooleanField(default=True)
    first_seen_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-first_seen_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["tracker", "url"], name="unique_entry_per_tracker_url"
            )
        ]

    def __str__(self):
        return self.title

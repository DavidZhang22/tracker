from django.core.management.base import BaseCommand

from trackers.services import refresh_due_trackers


class Command(BaseCommand):
    help = "Refresh trackers whose configured interval has elapsed."

    def handle(self, *args, **options):
        refreshed = refresh_due_trackers()
        total_new = sum(len(entries) for _tracker, entries in refreshed)
        self.stdout.write(
            self.style.SUCCESS(
                f"Refreshed {len(refreshed)} tracker(s), found {total_new} new entry(s)."
            )
        )

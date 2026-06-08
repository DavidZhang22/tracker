from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    initial = True

    dependencies = []

    operations = [
        migrations.CreateModel(
            name="Tracker",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(max_length=200)),
                ("source_url", models.URLField(max_length=1000)),
                ("current_url", models.URLField(blank=True, max_length=1000)),
                ("check_interval_minutes", models.PositiveIntegerField(default=60)),
                ("last_checked_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={"ordering": ["-updated_at"]},
        ),
        migrations.CreateModel(
            name="Entry",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("title", models.CharField(max_length=500)),
                ("url", models.URLField(max_length=1000)),
                ("summary", models.TextField(blank=True)),
                ("is_new", models.BooleanField(default=True)),
                ("first_seen_at", models.DateTimeField(auto_now_add=True)),
                ("tracker", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="entries", to="trackers.tracker")),
            ],
            options={"ordering": ["-first_seen_at"]},
        ),
        migrations.AddConstraint(
            model_name="entry",
            constraint=models.UniqueConstraint(fields=("tracker", "url"), name="unique_entry_per_tracker_url"),
        ),
    ]

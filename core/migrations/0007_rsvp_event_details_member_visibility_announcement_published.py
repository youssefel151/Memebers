from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [("core", "0006_event_registration_open")]
    operations = [
        migrations.AddField("member", "interests", models.CharField(blank=True, max_length=300)),
        migrations.AddField("member", "location", models.CharField(blank=True, max_length=120)),
        migrations.AddField("member", "directory_visible", models.BooleanField(default=True)),
        migrations.AddField("event", "description", models.TextField(blank=True)),
        migrations.AddField("event", "capacity", models.PositiveIntegerField(blank=True, null=True)),
        migrations.AddField("announcement", "published", models.BooleanField(default=True)),
        migrations.CreateModel(
            name="EventRegistration",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("event", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="member_registrations", to="core.event")),
                ("member", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="event_registrations", to="core.member")),
            ],
            options={"ordering": ["created_at"], "constraints": [models.UniqueConstraint(fields=("member", "event"), name="unique_member_event_registration")]},
        ),
    ]

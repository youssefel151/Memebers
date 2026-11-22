# Generated for event-by-event guest registration control.
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0005_guest_status"),
    ]

    operations = [
        migrations.AddField(
            model_name="event",
            name="registration_open",
            field=models.BooleanField(default=False),
        ),
    ]

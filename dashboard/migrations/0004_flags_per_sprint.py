"""US-41 (#123): flags and disputes belong to a sprint instead of a reporting week.

Existing flags and disputes refer to alpha weeks, which no sprint stands for, so they're
deleted first. Flags come back when compute_flags next runs for a sprint.
"""

import django.db.models.deletion
from django.db import migrations, models


def delete_weekly_flags_and_disputes(apps, schema_editor):
    apps.get_model("dashboard", "FlagDispute").objects.all().delete()
    apps.get_model("dashboard", "Flag").objects.all().delete()


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0005_user_must_change_password"),
        ("dashboard", "0003_flagdispute"),
    ]

    operations = [
        migrations.RunPython(delete_weekly_flags_and_disputes, migrations.RunPython.noop),
        migrations.RemoveConstraint(
            model_name="flagdispute", name="dashboard_flagdispute_one_per_flag"
        ),
        migrations.RemoveField(model_name="flag", name="week_start"),
        migrations.RemoveField(model_name="flagdispute", name="week_start"),
        # Both tables are empty now, so the new columns need no default.
        migrations.AddField(
            model_name="flag",
            name="sprint",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="flags",
                to="core.sprint",
            ),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name="flagdispute",
            name="sprint",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="flag_disputes",
                to="core.sprint",
            ),
            preserve_default=False,
        ),
        migrations.AddConstraint(
            model_name="flagdispute",
            constraint=models.UniqueConstraint(
                fields=["team", "student", "rule", "sprint"],
                name="dashboard_flagdispute_one_per_flag",
            ),
        ),
    ]

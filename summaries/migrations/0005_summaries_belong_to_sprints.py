import django.db.models.deletion
from django.db import migrations, models


def delete_weekly_summaries(apps, schema_editor):
    """Delete the alpha's weekly summaries. Their claims and validation runs go with them; the
    artifacts they cited stay."""
    apps.get_model("summaries", "Summary").objects.filter(sprint__isnull=True).delete()


class Migration(migrations.Migration):
    """T-21 (#125): every summary is for a sprint. This deletes data, so it can't be undone:
    back up the database first."""

    dependencies = [
        ("core", "0006_requirements_belong_to_sprints"),
        ("summaries", "0004_summary_week_or_sprint"),
    ]

    operations = [
        migrations.RunPython(delete_weekly_summaries, migrations.RunPython.noop),
        migrations.RemoveConstraint(
            model_name="summary",
            name="summaries_summary_week_or_sprint",
        ),
        migrations.RemoveConstraint(
            model_name="summary",
            name="summaries_summary_unique",
        ),
        migrations.RemoveField(
            model_name="summary",
            name="week_start",
        ),
        migrations.AlterField(
            model_name="summary",
            name="sprint",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="summaries",
                to="core.sprint",
            ),
        ),
    ]

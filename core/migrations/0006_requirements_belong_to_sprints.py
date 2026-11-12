import django.db.models.deletion
from django.db import migrations, models


def delete_requirements_without_a_sprint(apps, schema_editor):
    """Requirements never belong to a project as a whole, so one with no sprint has nothing
    left to belong to. The weekly summaries that could cite one are deleted too (T-21, #125)."""
    apps.get_model("core", "Requirement").objects.filter(sprint__isnull=True).delete()


class Migration(migrations.Migration):
    """T-21 (#125): a requirement belongs to a sprint only. This deletes data, so it can't be
    undone: back up the database first."""

    dependencies = [
        ("core", "0005_user_must_change_password"),
    ]

    operations = [
        migrations.RunPython(delete_requirements_without_a_sprint, migrations.RunPython.noop),
        migrations.RemoveConstraint(
            model_name="requirement",
            name="core_requirement_unique_without_sprint",
        ),
        migrations.RemoveField(
            model_name="requirement",
            name="project",
        ),
        migrations.AlterField(
            model_name="requirement",
            name="sprint",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="requirements",
                to="core.sprint",
            ),
        ),
    ]

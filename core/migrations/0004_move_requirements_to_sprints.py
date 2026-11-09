from datetime import timedelta

from django.db import migrations
from django.utils import timezone


def move_requirements_to_sprints(apps, schema_editor):
    """Give each project with requirements a 7-day "Sprint 1" holding all of them."""
    Project = apps.get_model("core", "Project")
    Requirement = apps.get_model("core", "Requirement")
    Sprint = apps.get_model("core", "Sprint")

    today = timezone.localdate()
    for project in Project.objects.filter(requirements__isnull=False).distinct():
        sprint = Sprint.objects.create(
            project=project,
            name="Sprint 1",
            start_date=today,
            end_date=today + timedelta(days=6),
        )
        Requirement.objects.filter(project=project).update(sprint=sprint)


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0003_sprint"),
    ]

    operations = [
        # Going back needs nothing: 0003's reverse drops the sprints and the column.
        migrations.RunPython(move_requirements_to_sprints, migrations.RunPython.noop),
    ]

from django.db import transaction

from core.models import Team

from .models import Flag

# Each rule takes a team and returns unsaved Flags. The rules themselves are a
# separate story; until then no flags are raised.
RULES = []


def compute_flags(team: Team) -> list[Flag]:
    """Run every rule for the team and replace its earlier flags with the new ones.

    If a rule fails, nothing is saved and the earlier flags are kept.
    """
    with transaction.atomic():
        flags = [flag for rule in RULES for flag in rule(team)]
        team.flags.all().delete()
        return Flag.objects.bulk_create(flags)

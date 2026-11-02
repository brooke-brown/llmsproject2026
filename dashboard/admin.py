from django.contrib import admin

from .models import Flag


@admin.register(Flag)
class FlagAdmin(admin.ModelAdmin):
    """Read-only: flags are derived data that compute_flags replaces.

    Delete stays allowed because Django checks it before cascading a team or user
    delete, and refusing it would block those.
    """

    list_display = ("team", "user", "rule", "severity", "week_start", "created_at")
    list_filter = ("severity", "rule")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

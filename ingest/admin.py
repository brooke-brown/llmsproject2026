from django.contrib import admin

from ingest.models import Artifact
from ingest.services import only_unattributed_commits


class AttributionFilter(admin.SimpleListFilter):
    title = "attribution"
    parameter_name = "attribution"

    def lookups(self, request, model_admin):
        return [("unattributed", "Unattributed commits")]

    def queryset(self, request, queryset):
        if self.value() == "unattributed":
            return only_unattributed_commits(queryset)
        return queryset


@admin.register(Artifact)
class ArtifactAdmin(admin.ModelAdmin):
    """Read-only: artifacts mirror GitHub, and the next ingest would overwrite an edit.

    The alpha pages don't show unattributed commits, so this is where to find them.

    Delete stays allowed because Django checks it before cascading a team or repo delete,
    and refusing it would block those.
    """

    list_display = (
        "artifact_id",
        "type",
        "timestamp",
        "author_login",
        "git_author_name",
        "git_author_email",
    )
    list_filter = (AttributionFilter, "type", "repo")
    list_select_related = ("repo",)
    search_fields = ("artifact_id", "author_login", "git_author_name", "git_author_email")
    date_hierarchy = "timestamp"
    ordering = ("-timestamp", "-pk")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

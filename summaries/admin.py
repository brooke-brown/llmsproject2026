"""Admin pages for summaries, claims and validation runs.

They're for debugging and spot checks, not editing, so everything is read-only: nobody
can change claim text that has already passed the validator.

Only a whole summary can be deleted, which removes its claims and logs together. A single
claim or run can't be: deleting a claim would silently change a validated summary's "N
couldn't be verified" note, and deleting a run would change the first-pass and drop-rate
metrics. Removing the permission also removes the bulk "Delete selected" action.
"""

from django.contrib import admin
from django.utils.text import Truncator

from summaries.models import Claim, Summary, ValidationRun


class ReadOnlyAdmin(admin.ModelAdmin):
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


# The only admin pages that may delete claims and runs: those that delete a summary.
SUMMARY_DELETE_VIEWS = {"summaries_summary_delete", "summaries_summary_changelist"}


class CascadeOnlyDeleteAdmin(ReadOnlyAdmin):
    """Read-only, and deletable only as part of deleting a whole summary.

    Django asks the admin of every related model whether it may be deleted before it
    cascades a delete, so refusing outright would also stop a summary being deleted. This
    allows it only on the summary's own delete page and list action. On a claim's or a
    run's own pages it's refused, and the bulk "Delete selected" action isn't offered.
    """

    def has_delete_permission(self, request, obj=None):
        match = request.resolver_match
        if match is None or match.url_name not in SUMMARY_DELETE_VIEWS:
            return False
        return super().has_delete_permission(request, obj)


class ClaimInline(admin.TabularInline):
    model = Claim
    fields = ("text", "category", "significance", "status", "requirement_ids")
    extra = 0
    show_change_link = True

    def has_add_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Summary)
class SummaryAdmin(ReadOnlyAdmin):
    list_display = ("user", "team", "sprint", "unverified_count", "created_at")
    list_filter = ("team", "sprint")
    search_fields = ("user__github_login", "user__username")
    ordering = ("-sprint__start_date", "user__github_login")
    inlines = [ClaimInline]


@admin.register(Claim)
class ClaimAdmin(CascadeOnlyDeleteAdmin):
    list_display = ("short_text", "summary", "status", "category", "significance")
    list_filter = ("status", "category", "significance")
    search_fields = ("text",)

    @admin.display(description="Claim")
    def short_text(self, claim):
        return Truncator(claim.text).chars(80)


@admin.register(ValidationRun)
class ValidationRunAdmin(CascadeOnlyDeleteAdmin):
    list_display = (
        "summary",
        "created_at",
        "claims_total",
        "first_pass",
        "dropped",
        "retries",
    )
    list_filter = ("created_at",)

    @admin.display(description="First-pass rate")
    def first_pass(self, run):
        return f"{run.first_pass_rate:.0%}"

    @admin.display(description="Drop rate")
    def dropped(self, run):
        return f"{run.drop_rate:.0%}"

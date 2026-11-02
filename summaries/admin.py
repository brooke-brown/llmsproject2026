"""Admin pages for summaries, claims and validation runs.

They're for debugging and spot checks, not editing, so everything is read-only: nobody
can change claim text that has already passed the validator. Deleting is still allowed,
so a bad test summary can be cleared.
"""

from django.contrib import admin
from django.utils.text import Truncator

from summaries.models import Claim, Summary, ValidationRun


class ReadOnlyAdmin(admin.ModelAdmin):
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


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
    list_display = ("user", "team", "week_start", "unverified_count", "created_at")
    list_filter = ("team", "week_start")
    search_fields = ("user__github_login", "user__username")
    ordering = ("-week_start", "user__github_login")
    inlines = [ClaimInline]


@admin.register(Claim)
class ClaimAdmin(ReadOnlyAdmin):
    list_display = ("short_text", "summary", "status", "category", "significance")
    list_filter = ("status", "category", "significance")
    search_fields = ("text",)

    @admin.display(description="Claim")
    def short_text(self, claim):
        return Truncator(claim.text).chars(80)


@admin.register(ValidationRun)
class ValidationRunAdmin(ReadOnlyAdmin):
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

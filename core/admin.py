from urllib.parse import urlencode

from django.contrib import admin, messages
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import path, reverse
from django.utils.translation import ngettext

from core.forms import (
    AdminAddUserForm,
    AdminSetPasswordForm,
    RepoForm,
    RepoInlineFormSet,
    RequirementImportForm,
)
from core.models import (
    Course,
    Enrollment,
    Project,
    Repo,
    Requirement,
    Sprint,
    Team,
    TeamMembership,
    User,
)
from core.services import (
    RequirementImportError,
    import_requirements,
    import_sprint_requirements,
)


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    add_form = AdminAddUserForm
    change_password_form = AdminSetPasswordForm
    list_display =("username", "github_login", "email", "first_name", "last_name", "is_staff")
    search_fields = ("github_login", *BaseUserAdmin.search_fields)
    fieldsets = (
        (None, {"fields": ("username", "github_login", "password", "must_change_password")}),
        *BaseUserAdmin.fieldsets[1:],
    )
    add_fieldsets = (
        (
            None,
            {
                "classes": ("wide",),
                "fields": (
                    "username",
                    "first_name",
                    "last_name",
                    "github_login",
                    "usable_password",
                    "password1",
                    "password2",
                ),
            },
        ),
    )


@admin.register(Course)
class CourseAdmin(admin.ModelAdmin):
    search_fields = ("name",)


@admin.register(Enrollment)
class EnrollmentAdmin(admin.ModelAdmin):
    list_display = ("user", "course", "role")
    list_filter = ("course", "role")
    search_fields = ("user__github_login", "user__username")
    autocomplete_fields = ("user",)


@admin.register(Project)
class ProjectAdmin(admin.ModelAdmin):
    list_display = ("name", "course")
    list_filter = ("course",)
    search_fields = ("name",)

    def get_urls(self):
        return [
            path(
                "<int:project_id>/import-requirements/",
                self.admin_site.admin_view(self.import_requirements_view),
                name="core_project_import_requirements",
            ),
            *super().get_urls(),
        ]

    def import_requirements_view(self, request, project_id):
        if not request.user.has_perm("core.add_requirement"):
            raise PermissionDenied
        project = get_object_or_404(Project, pk=project_id)
        return _requirements_import_page(
            self,
            request,
            import_into=lambda text: import_requirements(project, text),
            changelist_filter={"project__id__exact": project.pk},
            template="admin/core/project/import_requirements.html",
            title=f"Import requirements for {project.name}",
            context={"project": project},
        )


@admin.register(Sprint)
class SprintAdmin(admin.ModelAdmin):
    list_display = ("name", "project", "start_date", "end_date")
    list_filter = ("project",)
    search_fields = ("name",)
    ordering = ("project", "start_date")

    def get_urls(self):
        return [
            path(
                "<int:sprint_id>/import-requirements/",
                self.admin_site.admin_view(self.import_requirements_view),
                name="core_sprint_import_requirements",
            ),
            *super().get_urls(),
        ]

    def import_requirements_view(self, request, sprint_id):
        if not request.user.has_perm("core.add_requirement"):
            raise PermissionDenied
        sprint = get_object_or_404(Sprint, pk=sprint_id)
        return _requirements_import_page(
            self,
            request,
            import_into=lambda text: import_sprint_requirements(sprint, text),
            changelist_filter={"sprint__id__exact": sprint.pk},
            template="admin/core/sprint/import_requirements.html",
            title=f"Import requirements for {sprint.name}",
            context={"sprint": sprint},
        )


def _requirements_import_page(
    model_admin, request, *, import_into, changelist_filter, template, title, context
):
    """Show the import form, or import what it submitted and go to the new requirements."""
    if request.method == "POST":
        form = RequirementImportForm(request.POST, request.FILES)
    else:
        form = RequirementImportForm()
    if form.is_valid():
        try:
            requirements = import_into(form.cleaned_data["text"])
        except RequirementImportError as error:
            form.add_error(None, str(error))
        else:
            count = len(requirements)
            noun = ngettext("requirement", "requirements", count)
            messages.success(request, f"Imported {count} {noun}. You can edit them here.")
            query = urlencode(changelist_filter)
            return redirect(f"{reverse('admin:core_requirement_changelist')}?{query}")

    return render(
        request,
        template,
        {
            **model_admin.admin_site.each_context(request),
            "title": title,
            "opts": model_admin.opts,
            "form": form,
            **context,
        },
    )


@admin.register(Requirement)
class RequirementAdmin(admin.ModelAdmin):
    list_display = ("number", "text", "project", "sprint")
    list_display_links = ("number", "text")
    list_filter = ("project", "sprint")
    search_fields = ("text",)
    ordering = ("project", "sprint", "number")


class TeamMembershipInline(admin.TabularInline):
    model = TeamMembership
    autocomplete_fields = ("user",)
    extra = 1


class RepoInline(admin.TabularInline):
    model = Repo
    form = RepoForm
    formset = RepoInlineFormSet
    fields = ("url", "full_name")
    readonly_fields = ("full_name",)
    extra = 1


@admin.register(Team)
class TeamAdmin(admin.ModelAdmin):
    list_display = ("name", "project")
    list_filter = ("project",)
    search_fields = ("name",)
    inlines = [TeamMembershipInline, RepoInline]


@admin.register(TeamMembership)
class TeamMembershipAdmin(admin.ModelAdmin):
    list_display = ("user", "team")
    list_filter = ("team__project",)
    search_fields = ("user__github_login", "user__username", "team__name")
    autocomplete_fields = ("user",)


@admin.register(Repo)
class RepoAdmin(admin.ModelAdmin):
    form = RepoForm
    fields = ("team", "url", "full_name")
    readonly_fields = ("full_name",)
    list_display = ("full_name", "team")
    list_filter = ("team__project",)
    search_fields = ("full_name",)

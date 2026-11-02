from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin

from core.forms import RepoForm, RepoInlineFormSet
from core.models import Course, Enrollment, Project, Repo, Team, TeamMembership, User


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    list_display = ("username", "github_login", "email", "first_name", "last_name", "is_staff")
    search_fields = ("github_login", *BaseUserAdmin.search_fields)
    fieldsets = (
        (None, {"fields": ("username", "github_login", "password")}),
        *BaseUserAdmin.fieldsets[1:],
    )
    add_fieldsets = (
        (
            None,
            {
                "classes": ("wide",),
                "fields": ("username", "github_login", "usable_password", "password1", "password2"),
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

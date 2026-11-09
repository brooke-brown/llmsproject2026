from django.urls import path

from . import views

app_name = "dashboard"

urlpatterns = [
    path("", views.home, name="home"),
    path("student/", views.student_home, name="student_home"),
    path("courses/<int:course_id>/", views.course_overview, name="course_overview"),
    path(
        "courses/<int:course_id>/generating/",
        views.course_generating_status,
        name="course_generating_status",
    ),
    path("teams/<int:team_id>/", views.team, name="team"),
    path(
        "teams/<int:team_id>/generate/",
        views.generate_team_summaries,
        name="generate_team_summaries",
    ),
    path(
        "teams/<int:team_id>/retry/",
        views.retry_failed_summaries,
        name="retry_failed_summaries",
    ),
    path("summaries/<int:summary_id>/", views.summary, name="summary"),
    path("courses/<int:course_id>/projects/new/", views.project_create, name="project_create"),
    path("projects/<int:project_id>/setup/", views.project_setup, name="project_setup"),
    path("projects/<int:project_id>/delete/", views.project_delete, name="project_delete"),
    path("projects/<int:project_id>/rename/", views.project_rename, name="project_rename"),
    path(
        "projects/<int:project_id>/requirements/",
        views.project_requirements,
        name="project_requirements",
    ),
    path(
        "projects/<int:project_id>/requirements/import/",
        views.requirements_import,
        name="requirements_import",
    ),
    path(
        "projects/<int:project_id>/requirements/add/",
        views.requirement_add,
        name="requirement_add",
    ),
    path("requirements/<int:requirement_id>/edit/", views.requirement_edit, name="requirement_edit"),
    path(
        "requirements/<int:requirement_id>/delete/",
        views.requirement_delete,
        name="requirement_delete",
    ),
    path("projects/<int:project_id>/teams/new/", views.team_create, name="team_create"),
    path("teams/<int:team_id>/rename/", views.team_rename, name="team_rename"),
    path("teams/<int:team_id>/delete/", views.team_delete, name="team_delete"),
    path("teams/<int:team_id>/students/add/", views.student_add, name="student_add"),
    path("memberships/<int:membership_id>/edit/", views.membership_edit, name="membership_edit"),
    path(
        "memberships/<int:membership_id>/remove/",
        views.membership_remove,
        name="membership_remove",
    ),
    path("teams/<int:team_id>/repos/add/", views.repo_add, name="repo_add"),
    path("repos/<int:repo_id>/change/", views.repo_change, name="repo_change"),
    path("repos/<int:repo_id>/remove/", views.repo_remove, name="repo_remove"),
]

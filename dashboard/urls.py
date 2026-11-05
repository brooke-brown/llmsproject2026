from django.urls import path

from . import views

app_name = "dashboard"

urlpatterns = [
    path("", views.home, name="home"),
    path("courses/<int:course_id>/", views.course_overview, name="course_overview"),
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
]

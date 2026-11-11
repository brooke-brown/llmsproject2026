from django.urls import path

from . import views

app_name = "contributions"

urlpatterns = [
    path("projects/<int:project_id>/contributions/", views.contribution_list, name="list"),
    path("projects/<int:project_id>/contributions/add/", views.contribution_add, name="add"),
    path(
        "projects/<int:project_id>/contributions/team/",
        views.team_contribution_list,
        name="team_list",
    ),
    path("contributions/<int:contribution_id>/edit/", views.contribution_edit, name="edit"),
    path("contributions/<int:contribution_id>/delete/", views.contribution_delete, name="delete"),
    path("contributions/<int:contribution_id>/confirm/", views.contribution_confirm, name="confirm"),
    path(
        "contributions/<int:contribution_id>/unconfirm/",
        views.contribution_unconfirm,
        name="unconfirm",
    ),
]

from django.urls import path

from . import views

app_name = "peereval"

urlpatterns = [
    path(
        "projects/<int:project_id>/peer-evaluations/new/",
        views.evaluation_create,
        name="evaluation_create",
    ),
    path(
        "peer-evaluations/<int:evaluation_id>/change/",
        views.evaluation_change,
        name="evaluation_change",
    ),
    path(
        "peer-evaluations/<int:evaluation_id>/close/",
        views.evaluation_close,
        name="evaluation_close",
    ),
    path(
        "peer-evaluations/<int:evaluation_id>/delete/",
        views.evaluation_delete,
        name="evaluation_delete",
    ),
]

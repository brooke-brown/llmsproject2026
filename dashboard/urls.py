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
]

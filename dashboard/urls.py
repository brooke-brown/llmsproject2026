from django.urls import path

from . import views

app_name = "dashboard"

urlpatterns = [
    path("", views.home, name="home"),
    path("courses/<int:course_id>/", views.course_overview, name="course_overview"),
    path("teams/<int:team_id>/", views.team, name="team"),
    path(
        "teams/<int:team_id>/students/<int:user_id>/generate/",
        views.generate_summary,
        name="generate_summary",
    ),
    path("summaries/<int:summary_id>/", views.summary, name="summary"),
]

from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render

from . import services


@login_required
def home(request):
    courses = list(services.staff_courses(request.user))
    if len(courses) == 1:
        return redirect("dashboard:course_overview", course_id=courses[0].id)
    return render(request, "dashboard/home.html", {"courses": courses})


@login_required
def course_overview(request, course_id):
    course = services.get_course_for_staff(request.user, course_id)
    projects = services.course_projects_with_teams(course)
    return render(
        request, "dashboard/course_overview.html", {"course": course, "projects": projects}
    )


@login_required
def team(request, team_id):
    team = services.get_team_for_staff(request.user, team_id)
    return render(
        request,
        "dashboard/team.html",
        {
            "team": team,
            "repos": services.team_repos(team),
            "memberships": services.team_memberships_with_summaries(team),
        },
    )


@login_required
def summary(request, summary_id):
    summary = services.get_summary_for_staff(request.user, summary_id)
    return render(
        request,
        "dashboard/summary.html",
        {
            "summary": summary,
            "week_end": services.week_end(summary.week_start),
            "claims": services.verified_claims(summary),
            "unverified_count": summary.unverified_count,
        },
    )

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST

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
            "team_flags": services.team_wide_flags(team),
            "memberships": services.team_memberships_with_summaries(team),
            "default_week_start": services.last_completed_week_start(),
        },
    )


@login_required
@require_POST
def generate_summary(request, team_id, user_id):
    team, student = services.get_team_student_for_staff(request.user, team_id, user_id)
    try:
        week_start = services.parse_week_start(request.POST.get("week_start"))
        summary = services.generate_student_summary(student, team, week_start)
    except services.GenerationError as error:
        messages.error(request, str(error))
        return redirect("dashboard:team", team_id=team.id)
    return redirect("dashboard:summary", summary_id=summary.id)


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

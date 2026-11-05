from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST

from . import services


@login_required
def home(request):
    courses = services.course_cards(request.user)
    if len(courses) == 1:
        return redirect("dashboard:course_overview", course_id=courses[0].id)
    return render(request, "dashboard/home.html", {"courses": courses})


@login_required
def course_overview(request, course_id):
    course = services.get_course_for_staff(request.user, course_id)
    return render(
        request,
        "dashboard/course_overview.html",
        {
            "course": course,
            "projects": services.course_projects_with_teams(course),
            "default_week_start": services.last_completed_week_start(),
            "retry_team_ids": services.team_ids_to_retry(request.session),
        },
    )


@login_required
def team(request, team_id):
    team = services.get_team_for_staff(request.user, team_id)
    memberships, share_week = services.add_contribution_shares(
        team, services.team_memberships_with_summaries(team)
    )
    return render(
        request,
        "dashboard/team.html",
        {
            "team": team,
            "repos": services.team_repos(team),
            "team_flags": services.team_wide_flags(team),
            "memberships": memberships,
            "legend": services.pie_legend(memberships),
            "share_week": share_week,
            "team_status": services.team_status(team),
            "equal_share": round(100 / len(memberships)) if memberships else None,
        },
    )


def _show_generation(request, generation):
    """Remember who to retry, say how the run went, and return to the course overview."""
    services.remember_retry(request.session, generation)
    add_message = messages.success if generation.succeeded else messages.error
    add_message(request, generation.message)
    return redirect("dashboard:course_overview", course_id=generation.team.project.course_id)


@login_required
@require_POST
def generate_team_summaries(request, team_id):
    team = services.get_team_for_staff(request.user, team_id)
    try:
        week_start = services.parse_week_start(request.POST.get("week_start"))
        generation = services.generate_team_summaries(team, week_start)
    except services.GenerationError as error:
        # The overview lists every team, so the message says which one it's about.
        messages.error(request, f"{team.name}: {error}")
        return redirect("dashboard:course_overview", course_id=team.project.course_id)
    return _show_generation(request, generation)


@login_required
@require_POST
def retry_failed_summaries(request, team_id):
    team = services.get_team_for_staff(request.user, team_id)
    generation = services.retry_failed_summaries(team, request.session)
    if generation is None:
        messages.info(request, f"{team.name} has no failed summaries to retry.")
        return redirect("dashboard:course_overview", course_id=team.project.course_id)
    return _show_generation(request, generation)


@login_required
def summary(request, summary_id):
    summary = services.get_summary_for_staff(request.user, summary_id)
    claims = services.verified_claims(summary)
    return render(
        request,
        "dashboard/summary.html",
        {
            "summary": summary,
            "week_end": services.week_end(summary.week_start),
            "claims": claims,
            "tied_count": sum(1 for claim in claims if claim.requirement_ids),
            "unverified_count": summary.unverified_count,
            "weeks": services.student_weeks(summary),
        },
    )

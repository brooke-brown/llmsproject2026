from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.views import PasswordChangeView
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse_lazy
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.translation import ngettext
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from core import services as setup
from core.forms import ChangePasswordForm, RequirementImportForm
from summaries.models import Summary

from . import services
from .models import FlagDispute


@login_required
def home(request):
    # Login lands here, so this is also what sends each person to the right place.
    courses = services.course_cards(request.user)
    student_courses = services.student_courses(request.user)
    if student_courses and not courses:
        return redirect("dashboard:student_home")
    if len(courses) == 1 and not student_courses:
        return redirect("dashboard:course_overview", course_id=courses[0].id)
    return render(
        request, "dashboard/home.html", {"courses": courses, "student_courses": student_courses}
    )


@login_required
def student_home(request):
    services.check_any_course_student(request.user)
    return render(
        request,
        "dashboard/student_home.html",
        {"courses": services.student_home_courses(request.user)},
    )


class ChangePasswordView(PasswordChangeView):
    # Not Django's default name: the admin ships a template with that name, and it comes first.
    template_name = "dashboard/password_change.html"
    form_class = ChangePasswordForm
    success_url = reverse_lazy("dashboard:home")

    def form_valid(self, form):
        response = super().form_valid(form)
        messages.success(self.request, "Your password was changed.")
        return response


@login_required
def course_overview(request, course_id):
    course = services.get_course_for_staff(request.user, course_id)
    for result in services.take_run_results(request.session):
        add_message = messages.success if result["succeeded"] else messages.error
        add_message(request, result["message"])
    return render(
        request,
        "dashboard/course_overview.html",
        {
            "course": course,
            "projects": services.course_projects_with_teams(course),
            "default_week_start": services.last_completed_week_start(),
            "current_week_start": services.current_week_start(),
            "generating_team_ids": services.generating_team_ids(course),
            "retry_team_ids": services.team_ids_to_retry(request.session),
            "can_set_up": services.is_course_instructor(request.user, course),
        },
    )


@login_required
@require_GET
def course_generating_status(request, course_id):
    course = services.get_course_for_staff(request.user, course_id)
    return JsonResponse({"team_ids": services.generating_team_ids(course)})


@login_required
def team(request, team_id):
    team = services.get_team_for_staff(request.user, team_id)
    memberships, share_week = services.add_contribution_shares(
        team, services.team_memberships_with_summaries(team)
    )
    services.add_disputes(memberships)
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
            "share_week_note": services.share_week_note(team, share_week) if share_week else "",
            "team_status": services.team_status(team),
            "equal_share": round(100 / len(memberships)) if memberships else None,
            "can_set_up": services.is_course_instructor(request.user, team.project.course),
            "editing": request.GET.get("edit") == "1",
            "changing_repo_id": services.parse_id(request.GET.get("change_repo")),
            "editing_membership_id": services.parse_id(request.GET.get("edit_student")),
        },
    )


def _redirect_back(request, default):
    """Return to the page a form came from, if it named one on this site, or else `default`."""
    next_url = request.POST.get("next", "")
    if url_has_allowed_host_and_scheme(
        next_url, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    ):
        return redirect(next_url)
    return default


def _show_refusal(request, team, error):
    """Say why nothing ran. The overview lists every team, so the message says which one."""
    messages.error(request, f"{team.name}: {error}")
    return redirect("dashboard:course_overview", course_id=team.project.course_id)


def _show_generation(request, generation):
    """Keep how the run went for the course overview, and go there."""
    services.save_run_result(request.session, generation)
    return redirect("dashboard:course_overview", course_id=generation.team.project.course_id)


@login_required
@require_POST
def generate_team_summaries(request, team_id):
    team = services.get_team_for_staff(request.user, team_id)
    try:
        week_start = services.parse_week_start(request.POST.get("week_start"))
        generation = services.generate_team_summaries(team, week_start)
    except services.GenerationError as error:
        return _show_refusal(request, team, error)
    return _show_generation(request, generation)


@login_required
@require_POST
def retry_failed_summaries(request, team_id):
    team = services.get_team_for_staff(request.user, team_id)
    try:
        generation = services.retry_failed_summaries(team, request.session)
    except services.GenerationError as error:
        return _show_refusal(request, team, error)
    if generation is None:
        messages.info(request, f"{team.name} has no failed summaries to retry.")
        return redirect("dashboard:course_overview", course_id=team.project.course_id)
    return _show_generation(request, generation)


@login_required
@require_POST
def dispute_decide(request, dispute_id):
    dispute = services.get_dispute_for_instructor(request.user, dispute_id)
    decision = request.POST.get("decision", "")
    try:
        services.decide_dispute(dispute, decision, request.POST.get("note", ""), request.user)
    except services.DisputeError as error:
        messages.error(request, str(error))
    else:
        verb = "Kept" if decision == "keep" else "Withdrew"
        messages.success(request, f"{verb} the flag about {dispute.student.display_name}.")
    return redirect("dashboard:team", team_id=dispute.team_id)


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


@login_required
@require_http_methods(["GET", "POST"])
def project_create(request, course_id):
    course = services.get_course_for_instructor(request.user, course_id)
    name = request.POST.get("name", "")
    if request.method == "POST":
        try:
            project = setup.create_project(course, name)
        except setup.ProjectSetupError as error:
            # Rendered here rather than redirected, so what was typed is still there.
            messages.error(request, str(error))
        else:
            messages.success(request, f"Created {project.name}. Now add its teams and requirements.")
            return redirect("dashboard:project_setup", project_id=project.id)
    return render(request, "dashboard/project_new.html", {"course": course, "name": name})


@login_required
def project_setup(request, project_id):
    project = services.get_project_for_instructor(request.user, project_id)
    return render(
        request,
        "dashboard/project_setup.html",
        {
            "project": project,
            "teams": services.project_setup_teams(project),
            "requirement_count": project.requirements.count(),
            "summary_count": Summary.objects.filter(team__project=project).count(),
            "dispute_count": FlagDispute.objects.filter(team__project=project).count(),
            "editing": request.GET.get("edit") == "1",
        },
    )


@login_required
@require_POST
def project_rename(request, project_id):
    project = services.get_project_for_instructor(request.user, project_id)
    try:
        setup.rename_project(project, request.POST.get("name", ""))
    except setup.ProjectSetupError as error:
        messages.error(request, str(error))
    else:
        messages.success(request, f"Renamed the project to {project.name}.")
    return _redirect_back(
        request, redirect("dashboard:course_overview", course_id=project.course_id)
    )


@login_required
@require_POST
def project_delete(request, project_id):
    project = services.get_project_for_instructor(request.user, project_id)
    setup.delete_project(project)
    messages.success(request, f"Deleted {project.name}.")
    return redirect("dashboard:course_overview", course_id=project.course_id)


def _render_requirements(request, project, import_form=None):
    return render(
        request,
        "dashboard/project_requirements.html",
        {
            "project": project,
            "requirements": project.requirements.order_by("number"),
            "editing_requirement_id": services.parse_id(request.GET.get("edit")),
            "import_form": import_form or RequirementImportForm(),
        },
    )


@login_required
def project_requirements(request, project_id):
    project = services.get_project_for_instructor(request.user, project_id)
    return _render_requirements(request, project)


@login_required
@require_POST
def requirements_import(request, project_id):
    project = services.get_project_for_instructor(request.user, project_id)
    # Checked first, so a refused import doesn't spend time reading the PDF.
    if project.requirements.exists():
        messages.error(request, setup.ALREADY_HAS_REQUIREMENTS)
        return redirect("dashboard:project_requirements", project_id=project.id)

    form = RequirementImportForm(request.POST, request.FILES)
    if form.is_valid():
        try:
            requirements = setup.import_requirements(project, form.cleaned_data["text"])
        except setup.RequirementImportError as error:
            # Not a form error: if another import just won, the page has no form to show it.
            messages.error(request, str(error))
        else:
            count = len(requirements)
            noun = ngettext("requirement", "requirements", count)
            messages.success(request, f"Imported {count} {noun}.")
            return redirect("dashboard:project_requirements", project_id=project.id)
    # Rendered here rather than redirected, so the pasted text is still there to try again.
    return _render_requirements(request, project, form)


def _change_requirement(request, project_id, change, *args):
    """Run a change, say if it was refused, and return to the requirements page."""
    try:
        change(*args)
    except setup.ProjectSetupError as error:
        messages.error(request, str(error))
    return redirect("dashboard:project_requirements", project_id=project_id)


@login_required
@require_POST
def requirement_add(request, project_id):
    project = services.get_project_for_instructor(request.user, project_id)
    text = request.POST.get("text", "")
    return _change_requirement(request, project.id, setup.add_requirement, project, text)


@login_required
@require_POST
def requirement_edit(request, requirement_id):
    requirement = services.get_requirement_for_instructor(request.user, requirement_id)
    text = request.POST.get("text", "")
    return _change_requirement(
        request, requirement.project_id, setup.edit_requirement, requirement, text
    )


@login_required
@require_POST
def requirement_delete(request, requirement_id):
    requirement = services.get_requirement_for_instructor(request.user, requirement_id)
    return _change_requirement(
        request, requirement.project_id, setup.delete_requirement, requirement
    )


@login_required
@require_POST
def team_create(request, project_id):
    project = services.get_project_for_instructor(request.user, project_id)
    try:
        team = setup.create_team(project, request.POST.get("name", ""))
    except setup.ProjectSetupError as error:
        messages.error(request, str(error))
    else:
        messages.success(request, f"Created {team.name}.")
    return _redirect_back(
        request, redirect("dashboard:course_overview", course_id=project.course_id)
    )


def _change_team(request, team_id, success, change, *args):
    """Run a change, say how it went, and return to the team page."""
    try:
        change(*args)
    except setup.ProjectSetupError as error:
        messages.error(request, str(error))
    else:
        messages.success(request, success)
    return redirect("dashboard:team", team_id=team_id)


@login_required
@require_POST
def team_rename(request, team_id):
    team = services.get_team_for_instructor(request.user, team_id)
    name = request.POST.get("name", "")
    success = f"Renamed the team to {name.strip()}."
    return _change_team(request, team.id, success, setup.rename_team, team, name)


@login_required
@require_POST
def team_delete(request, team_id):
    team = services.get_team_for_instructor(request.user, team_id)
    setup.delete_team(team)
    messages.success(request, f"Deleted {team.name}.")
    return redirect("dashboard:course_overview", course_id=team.project.course_id)


@login_required
@require_POST
def student_add(request, team_id):
    team = services.get_team_for_instructor(request.user, team_id)
    github_login = request.POST.get("github_login", "")
    return _change_team(
        request,
        team.id,
        f"Added {github_login.strip()} to {team.name}.",
        setup.add_student,
        team,
        request.POST.get("full_name", ""),
        github_login,
        request.POST.get("email", ""),
    )


@login_required
@require_POST
def membership_edit(request, membership_id):
    membership = services.get_membership_for_instructor(request.user, membership_id)
    return _change_team(
        request,
        membership.team_id,
        "Updated the student's name.",
        setup.edit_student,
        membership.user,
        request.POST.get("full_name", ""),
    )


@login_required
@require_POST
def membership_remove(request, membership_id):
    membership = services.get_membership_for_instructor(request.user, membership_id)
    success = f"Removed {membership.user.display_name} from {membership.team.name}."
    return _change_team(request, membership.team_id, success, services.remove_student, membership)


@login_required
@require_POST
def repo_add(request, team_id):
    team = services.get_team_for_instructor(request.user, team_id)
    url = request.POST.get("url", "")
    return _change_team(request, team.id, "Added the repo.", setup.add_repo, team, url)


@login_required
@require_POST
def repo_change(request, repo_id):
    repo = services.get_repo_for_instructor(request.user, repo_id)
    url = request.POST.get("url", "")
    return _change_team(request, repo.team_id, "Changed the repo.", setup.change_repo, repo, url)


@login_required
@require_POST
def repo_remove(request, repo_id):
    repo = services.get_repo_for_instructor(request.user, repo_id)
    success = f"Removed {repo.full_name}."
    return _change_team(request, repo.team_id, success, setup.remove_repo, repo)

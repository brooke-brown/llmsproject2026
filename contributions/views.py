from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST

from . import services
from .forms import ContributionForm
from .models import Contribution


def _back_to_list(team):
    return redirect("contributions:list", project_id=team.project_id)


def _refuse_locked(request, contribution):
    messages.error(request, services.LOCKED_MESSAGE)
    return _back_to_list(contribution.team)


@login_required
def contribution_list(request, project_id):
    team = services.get_team_for_project_student(request.user, project_id)
    return render(
        request,
        "contributions/list.html",
        {
            "project": team.project,
            "contributions": services.contributions_for_list(team, request.user),
            "locked_message": services.LOCKED_MESSAGE,
        },
    )


def _save_or_show_form(request, contribution, success_message):
    """Handle the add and edit forms, which differ only in the contribution they start from."""
    if request.method == "POST":
        form = ContributionForm(request.POST, instance=contribution)
        if form.is_valid():
            try:
                services.save_contribution(form)
            except services.ContributionLocked:
                return _refuse_locked(request, contribution)
            messages.success(request, success_message)
            return _back_to_list(contribution.team)
    else:
        form = ContributionForm(instance=contribution)
    return render(
        request,
        "contributions/form.html",
        {"project": contribution.team.project, "form": form, "contribution": contribution},
    )


@login_required
def contribution_add(request, project_id):
    team = services.get_team_for_project_student(request.user, project_id)
    contribution = Contribution(team=team, student=request.user)
    return _save_or_show_form(request, contribution, "Added your contribution.")


@login_required
def contribution_edit(request, contribution_id):
    contribution = services.get_own_contribution(request.user, contribution_id)
    if services.is_locked(contribution):
        return _refuse_locked(request, contribution)
    return _save_or_show_form(request, contribution, "Saved your changes.")


@login_required
def contribution_delete(request, contribution_id):
    contribution = services.get_own_contribution(request.user, contribution_id)
    if services.is_locked(contribution):
        return _refuse_locked(request, contribution)
    if request.method == "POST":
        try:
            services.delete_contribution(contribution)
        except services.ContributionLocked:
            return _refuse_locked(request, contribution)
        messages.success(request, "Deleted your contribution.")
        return _back_to_list(contribution.team)
    return render(
        request,
        "contributions/confirm_delete.html",
        {"project": contribution.team.project, "contribution": contribution},
    )


@login_required
def team_contribution_list(request, project_id):
    team = services.get_team_for_project_student(request.user, project_id)
    return render(
        request,
        "contributions/team_list.html",
        {
            "project": team.project,
            "contributions": services.teammates_contributions_for_list(team, request.user),
        },
    )


@login_required
@require_POST
def contribution_confirm(request, contribution_id):
    contribution = services.get_team_contribution(request.user, contribution_id)
    try:
        services.confirm_contribution(
            contribution, request.user, request.POST.get("updated_at", "")
        )
    except services.ContributionChanged:
        messages.error(request, services.CHANGED_MESSAGE)
    return redirect("contributions:team_list", project_id=contribution.team.project_id)


@login_required
@require_POST
def contribution_unconfirm(request, contribution_id):
    contribution = services.get_team_contribution(request.user, contribution_id)
    services.undo_confirmation(contribution, request.user)
    return redirect("contributions:team_list", project_id=contribution.team.project_id)

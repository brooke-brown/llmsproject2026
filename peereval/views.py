from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render
from django.utils import formats, timezone
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from dashboard.services import get_project_for_instructor

from . import services
from .forms import COMMENT_MAX_LENGTH, ClosingTimeForm, EvaluationForm, RatingForm


def _setup_page(evaluation):
    return redirect("dashboard:project_setup", project_id=evaluation.project_id)


def _when(moment):
    return formats.date_format(timezone.localtime(moment), "M j, Y, g:i A")


@login_required
@require_http_methods(["GET", "POST"])
def evaluation_create(request, project_id):
    project = get_project_for_instructor(request.user, project_id)
    form = EvaluationForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            evaluation = services.create_evaluation(
                project, request.user, form.cleaned_data["name"], form.cleaned_data["closes_at"]
            )
        except services.EvaluationError as error:
            # Rendered here rather than redirected, so what was typed is still there.
            form.add_error(None, str(error))
        else:
            messages.success(
                request,
                f"Opened {evaluation.name}. It closes {_when(evaluation.closes_at)}.",
            )
            return _setup_page(evaluation)
    return render(request, "peereval/evaluation_new.html", {"project": project, "form": form})


@login_required
@require_POST
def evaluation_change(request, evaluation_id):
    evaluation = services.get_evaluation_for_instructor(request.user, evaluation_id)
    form = ClosingTimeForm(request.POST)
    try:
        if not form.is_valid():
            raise services.EvaluationError(form.errors["closes_at"][0])
        evaluation = services.change_closing_time(evaluation, form.cleaned_data["closes_at"])
    except services.EvaluationError as error:
        messages.error(request, str(error))
    else:
        messages.success(request, f"{evaluation.name} now closes {_when(evaluation.closes_at)}.")
    return _setup_page(evaluation)


@login_required
@require_POST
def evaluation_close(request, evaluation_id):
    evaluation = services.get_evaluation_for_instructor(request.user, evaluation_id)
    try:
        services.close_now(evaluation)
    except services.EvaluationError as error:
        messages.error(request, str(error))
    else:
        messages.success(request, f"Closed {evaluation.name}. Students can no longer rate.")
    return _setup_page(evaluation)


@login_required
@require_POST
def evaluation_delete(request, evaluation_id):
    evaluation = services.get_evaluation_for_instructor(request.user, evaluation_id)
    try:
        services.delete_evaluation(evaluation)
    except services.EvaluationError as error:
        messages.error(request, str(error))
    else:
        messages.success(request, f"Deleted {evaluation.name}.")
    return _setup_page(evaluation)


@login_required
@require_GET
def evaluation_detail(request, evaluation_id):
    evaluation, team = services.get_evaluation_for_student(request.user, evaluation_id)
    return render(
        request,
        "peereval/evaluation.html",
        {
            "evaluation": evaluation,
            "is_open": services.is_open(evaluation),
            "teammates": services.teammates_with_status(evaluation, team, request.user),
        },
    )


@login_required
@require_http_methods(["GET", "POST"])
def rate(request, evaluation_id, ratee_id):
    evaluation, team = services.get_evaluation_for_student(request.user, evaluation_id)
    ratee = services.get_teammate(team, request.user, ratee_id)
    back = redirect("peereval:evaluation", evaluation_id=evaluation.id)
    # A closed evaluation never shows the form, so a student's answers aren't shown afterwards.
    # save_rating checks again under the lock, for a page left open past the closing time.
    if not services.is_open(evaluation):
        messages.error(request, services.closed_message(evaluation))
        return back
    if request.method == "POST":
        form = RatingForm(request.POST)
        if form.is_valid():
            try:
                services.save_rating(
                    evaluation, request.user, ratee, form.scores(), form.cleaned_data["comment"]
                )
            except services.EvaluationError as error:
                messages.error(request, str(error))
            else:
                messages.success(request, f"Saved your rating of {ratee.display_name}.")
            return back
    else:
        form = RatingForm(rating=services.own_rating(evaluation, request.user, ratee))
    return render(
        request,
        "peereval/rate.html",
        {
            "evaluation": evaluation,
            "ratee": ratee,
            "form": form,
            "comment_max_length": COMMENT_MAX_LENGTH,
        },
    )

from django import forms

from .questions import MAX_SCORE, MIN_SCORE, QUESTIONS

# A datetime-local input has no time zone, so Django reads it in TIME_ZONE (America/New_York).
DATETIME_LOCAL = "%Y-%m-%dT%H:%M"
CLOSES_AT_ERRORS = {
    "required": "Enter a closing date and time.",
    "invalid": "Enter a closing date and time.",
}


def closes_at_field():
    return forms.DateTimeField(
        label="Closes at (New York time)",
        input_formats=[DATETIME_LOCAL],
        widget=forms.DateTimeInput(attrs={"type": "datetime-local"}, format=DATETIME_LOCAL),
        error_messages=CLOSES_AT_ERRORS,
    )


class EvaluationForm(forms.Form):
    """Only the shape of the input: the rules, such as one open at a time, are in services."""

    name = forms.CharField(
        label="Name", max_length=100, error_messages={"required": "Enter a name."}
    )
    closes_at = closes_at_field()


class ClosingTimeForm(forms.Form):
    closes_at = closes_at_field()


COMMENT_MAX_LENGTH = 1000


def _score_choices(question):
    """1 to 5, with the question's words for the two ends on 1 and 5."""
    labels = {MIN_SCORE: question.label_1, MAX_SCORE: question.label_5}
    return [
        (score, f"{score}: {labels[score]}" if score in labels else str(score))
        for score in range(MIN_SCORE, MAX_SCORE + 1)
    ]


class RatingForm(forms.Form):
    """One field per T-17 question, named by its key, then an optional comment."""

    comment = forms.CharField(
        label="Comment",
        required=False,
        max_length=COMMENT_MAX_LENGTH,
        widget=forms.Textarea(attrs={"rows": 4}),
        error_messages={
            "max_length": f"Keep the comment to {COMMENT_MAX_LENGTH:,} characters or fewer."
        },
    )

    def __init__(self, *args, rating=None, **kwargs):
        if rating is not None:
            kwargs.setdefault("initial", {**rating.scores, "comment": rating.comment})
        super().__init__(*args, **kwargs)
        comment = self.fields.pop("comment")
        for question in QUESTIONS:
            answer_message = f"Answer “{question.text}”."
            self.fields[question.key] = forms.TypedChoiceField(
                label=question.text,
                choices=_score_choices(question),
                coerce=int,
                widget=forms.RadioSelect,
                error_messages={"required": answer_message, "invalid_choice": answer_message},
            )
        self.fields["comment"] = comment

    def question_fields(self):
        return [self[question.key] for question in QUESTIONS]

    def scores(self):
        return {question.key: self.cleaned_data[question.key] for question in QUESTIONS}

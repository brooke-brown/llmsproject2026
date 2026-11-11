from django import forms

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

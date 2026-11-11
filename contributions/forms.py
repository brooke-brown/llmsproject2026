from django import forms
from django.utils import timezone

from ingest.services import REPORTING_TIME_ZONE

from .models import MAX_DESCRIPTION_LENGTH, MAX_HOURS, MIN_HOURS, Contribution

HOURS_LIMITS = f"Hours must be between {MIN_HOURS} and {MAX_HOURS}."


class SingleLineBreakCharField(forms.CharField):
    """Counts a line break as one character. Browsers count it as one toward `maxlength`
    but post it as two, so otherwise text the browser allowed could be over the limit."""

    def to_python(self, value):
        return super().to_python(value).replace("\r\n", "\n")


class ContributionForm(forms.ModelForm):
    """The view passes an instance with `team` and `student` set, so a student can't choose
    them, and the model's own checks, such as no future dates, run on the bound values."""

    description = SingleLineBreakCharField(
        max_length=MAX_DESCRIPTION_LENGTH, widget=forms.Textarea(attrs={"rows": 4})
    )
    # No max_digits: the limits below already cap it, and it would add a second, vaguer error.
    hours = forms.DecimalField(
        label="Hours spent",
        required=False,
        min_value=MIN_HOURS,
        max_value=MAX_HOURS,
        decimal_places=2,
        error_messages={
            "min_value": HOURS_LIMITS,
            "max_value": HOURS_LIMITS,
            "max_decimal_places": "Enter hours to at most two decimal places.",
        },
    )

    class Meta:
        model = Contribution
        fields = ["date", "category", "description", "hours"]
        widgets = {"date": forms.DateInput(format="%Y-%m-%d", attrs={"type": "date"})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        today = timezone.localdate(timezone=REPORTING_TIME_ZONE)
        self.fields["date"].widget.attrs["max"] = today.isoformat()

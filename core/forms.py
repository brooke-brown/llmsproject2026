from django import forms
from django.core.exceptions import ValidationError

from core.models import Repo
from core.services import (
    DUPLICATE_REPO_MESSAGE,
    RequirementImportError,
    extract_pdf_text,
    github_repo_full_name,
)


class RepoForm(forms.ModelForm):
    """Takes a repo's URL and fills in `full_name` from it."""

    # Not a URLField, which would answer `git@github.com:...` with a generic error.
    url = forms.CharField(
        label="GitHub repo URL", max_length=200, help_text="Such as https://github.com/acme/app"
    )

    class Meta:
        model = Repo
        fields = ["team", "url"]

    def clean_url(self):
        self.parsed_full_name = github_repo_full_name(self.cleaned_data["url"])
        return self.cleaned_data["url"]

    def clean(self):
        cleaned_data = super().clean()
        if self.instance.pk and "team" in self.changed_data and self.instance.artifacts.exists():
            # The validator finds artifacts through repo__team, so the new team could cite them.
            self.add_error(
                "team",
                "This repo has artifacts, so it can't move to another team. "
                "Add the repo to the other team separately.",
            )
            return cleaned_data
        if "url" not in cleaned_data:
            return cleaned_data

        full_name = self.parsed_full_name
        stored = self.instance.full_name
        if self.instance.pk and full_name != stored and self.instance.artifacts.exists():
            # Artifact IDs start with full_name and are compared to it exactly.
            if full_name.lower() != stored.lower():
                self.add_error(
                    "url",
                    "This repo has artifacts, so it can't point at a different repo. "
                    "Add the other repo separately.",
                )
                return cleaned_data
            full_name = stored

        # `team` can be unsaved on the team page, and then has no stored repos to clash with.
        team = cleaned_data.get("team")
        if team is not None and team.pk is not None:
            duplicate = Repo.objects.filter(team=team, full_name__iexact=full_name)
            if duplicate.exclude(pk=self.instance.pk).exists():
                # The model's constraint is skipped because full_name isn't a form field.
                self.add_error("url", DUPLICATE_REPO_MESSAGE)
                return cleaned_data

        self.instance.full_name = full_name
        return cleaned_data


class RepoInlineFormSet(forms.BaseInlineFormSet):
    """Catches a repo listed twice in one save, which no stored row can reveal yet."""

    def clean(self):
        super().clean()
        seen = set()
        for form in self.forms:
            if self._should_delete_form(form) or not hasattr(form, "parsed_full_name"):
                continue
            full_name = form.parsed_full_name.lower()
            if full_name in seen:
                raise ValidationError(DUPLICATE_REPO_MESSAGE)
            seen.add(full_name)


class RequirementImportForm(forms.Form):
    pdf = forms.FileField(
        label="Requirements PDF",
        required=False,
        widget=forms.FileInput(attrs={"accept": ".pdf,application/pdf"}),
        help_text="Upload the project's requirements as a PDF, or paste them below.",
    )
    # Not stripped, so the LLM and the source check see exactly what was pasted.
    text = forms.CharField(
        label="Requirements text",
        required=False,
        strip=False,
        widget=forms.Textarea(attrs={"rows": 20, "cols": 80}),
        help_text="Paste the project's requirements. Each one must stay worded as written here.",
    )

    def clean(self):
        """Leave the document to import in `text`, whichever way it arrived."""
        cleaned_data = super().clean()
        if "pdf" in self.errors:
            return cleaned_data

        pdf = cleaned_data.get("pdf")
        has_text = bool(cleaned_data.get("text", "").strip())
        if pdf and has_text:
            # Using one and ignoring the other could import the wrong document.
            self.add_error(None, "Upload a PDF or paste text, not both.")
        elif pdf:
            try:
                cleaned_data["text"] = extract_pdf_text(pdf)
            except RequirementImportError as error:
                self.add_error("pdf", str(error))
        elif not has_text:
            self.add_error("text", "Upload a PDF or paste the project's requirements.")
        return cleaned_data

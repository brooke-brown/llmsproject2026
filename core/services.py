import re

from django.core.exceptions import ValidationError

# GitHub's limits: 39-character owners of letters, digits and hyphens, and 100-character
# repo names. Matching the whole URL also rejects look-alike hosts, credentials, ports,
# query strings and paths below the repo.
GITHUB_REPO_URL = re.compile(
    r"https://github\.com/([a-z0-9-]{1,39})/([a-z0-9._-]{1,100}?)(?:\.git)?/?\Z", re.IGNORECASE
)


def github_repo_full_name(url: str) -> str:
    """Return `owner/repo` for a GitHub repo's HTTPS URL, or raise ValidationError."""
    match = GITHUB_REPO_URL.match(url)
    if match:
        owner, repo = match.groups()
        if repo not in (".", "..") and not repo.lower().endswith(".git"):
            return f"{owner}/{repo}"
    raise ValidationError("Enter a GitHub repo URL, such as https://github.com/acme/app.")

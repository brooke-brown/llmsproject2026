from urllib.parse import urlencode

from django.conf import settings
from django.http import JsonResponse
from django.shortcuts import redirect
from django.urls import reverse


class ForcePasswordChangeMiddleware:
    """Hold a user who must change their password on the page for it, from every other page
    but logout and static files. Here rather than in views, so new pages are covered too."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.user.is_authenticated and request.user.must_change_password:
            exempt = (reverse("password_change"), reverse("logout"), reverse("admin:logout"))
            if request.path not in exempt and not request.path.startswith(settings.STATIC_URL):
                return self._hold(request)
        return self.get_response(request)

    def _hold(self, request):
        if not _is_page_visit(request):
            # A redirect would hand a script an HTML page, and a POST can't be replayed after.
            return JsonResponse({"error": "password_change_required"}, status=403)
        url = reverse("password_change")
        # Sent back to the login page, a logged-in user would just see the login form again.
        if request.path != reverse("login"):
            url += "?" + urlencode({"next": request.get_full_path()})
        return redirect(url)


def _is_page_visit(request):
    """Whether someone is opening a page. Browsers ask for text/html then, and fetch() doesn't."""
    accept = request.headers.get("Accept")
    return request.method in ("GET", "HEAD") and (accept is None or "text/html" in accept)

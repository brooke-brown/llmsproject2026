from django.conf import settings
from django.shortcuts import redirect
from django.urls import reverse


class ForcePasswordChangeMiddleware:
    """Send a user who must change their password to the page for it, from every other page
    but logout and static files. Here rather than in views, so new pages are covered too."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.user.is_authenticated and request.user.must_change_password:
            exempt = (reverse("password_change"), reverse("logout"), reverse("admin:logout"))
            if request.path not in exempt and not request.path.startswith(settings.STATIC_URL):
                return redirect("password_change")
        return self.get_response(request)

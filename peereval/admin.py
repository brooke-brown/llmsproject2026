from django.contrib import admin

from .models import PeerEvaluation, PeerRating


@admin.register(PeerEvaluation)
class PeerEvaluationAdmin(admin.ModelAdmin):
    list_display = ("name", "project", "closes_at", "created_by", "created_at")
    readonly_fields = ("created_at",)


@admin.register(PeerRating)
class PeerRatingAdmin(admin.ModelAdmin):
    # Scores and comments stay off the list so they're only seen by opening a rating.
    list_display = ("evaluation", "rater", "ratee", "updated_at")
    readonly_fields = ("submitted_at", "updated_at")

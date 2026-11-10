from django.contrib import admin

from .models import Contribution, Corroboration


@admin.register(Contribution)
class ContributionAdmin(admin.ModelAdmin):
    list_display = ("team", "student", "date", "category", "hours", "updated_at")
    list_filter = ("category",)
    readonly_fields = ("created_at", "updated_at")


@admin.register(Corroboration)
class CorroborationAdmin(admin.ModelAdmin):
    list_display = ("contribution", "teammate", "created_at")
    readonly_fields = ("created_at",)

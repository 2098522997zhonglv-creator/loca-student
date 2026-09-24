from django.contrib import admin

from .models import UserBehaviorEvent


@admin.register(UserBehaviorEvent)
class UserBehaviorEventAdmin(admin.ModelAdmin):
    list_display = ("id", "user", "event_type", "summary_text", "created_at")
    list_filter = ("event_type",)
    search_fields = ("summary_text", "vector_id", "user__username")
    readonly_fields = ("created_at", "vector_id")

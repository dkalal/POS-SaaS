from django.contrib import admin

from api.models import APIKey


@admin.register(APIKey)
class APIKeyAdmin(admin.ModelAdmin):
    list_display = ("label", "tenant", "key_prefix", "can_view_cost", "is_active", "revoked_at", "last_used_at")
    list_filter = ("tenant", "can_view_cost", "is_active")
    search_fields = ("label", "key_prefix", "tenant__name")
    readonly_fields = ("key_prefix", "key_hash", "revoked_at", "last_used_at", "created_at", "updated_at")
    actions = ["revoke_selected_keys"]

    @admin.action(description="Revoke selected API keys immediately")
    def revoke_selected_keys(self, request, queryset):
        for api_key in queryset:
            api_key.revoke()


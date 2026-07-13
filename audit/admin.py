from django.contrib import admin

from audit.models import AuditEvent


@admin.register(AuditEvent)
class AuditEventAdmin(admin.ModelAdmin):
    list_display = ("created_at", "tenant", "actor", "action", "target_label")
    list_filter = ("action", "tenant")
    search_fields = ("target_label", "actor__username")
    readonly_fields = (
        "tenant",
        "actor",
        "action",
        "target_content_type",
        "target_object_id",
        "target_label",
        "before_data",
        "after_data",
        "metadata",
        "created_at",
        "updated_at",
    )

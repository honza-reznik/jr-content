from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin

from .models import OAuthConnection, Session, TwoFactorEvent, User


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    list_display = ("email", "name", "is_active", "is_staff", "created_at")
    list_filter = ("is_active", "is_staff", "totp_enabled")
    search_fields = ("email", "name")
    ordering = ("-created_at",)
    fieldsets = (
        (None, {"fields": ("email", "password")}),
        ("Personal info", {"fields": ("name", "avatar")}),
        ("Permissions", {"fields": ("is_active", "is_staff", "is_superuser")}),
        ("2FA", {"fields": ("totp_enabled", "totp_enabled_at")}),
    )
    # JR Content P2-B17: 2FA changes only through Settings → Security or the owner's audited reset.
    readonly_fields = ("totp_enabled", "totp_enabled_at")
    add_fieldsets = ((None, {"classes": ("wide",), "fields": ("email", "password1", "password2")}),)


@admin.register(OAuthConnection)
class OAuthConnectionAdmin(admin.ModelAdmin):
    list_display = ("user", "provider", "provider_email", "created_at")
    list_filter = ("provider",)


@admin.register(Session)
class SessionAdmin(admin.ModelAdmin):
    list_display = ("user", "device_info", "ip_address", "last_active_at", "expires_at")
    list_filter = ("created_at",)


# JR Content: přidáno 2026-11-14 (blok P2-B17), AGPL-3.0. Auditní záznamy 2FA jen ke čtení.
@admin.register(TwoFactorEvent)
class TwoFactorEventAdmin(admin.ModelAdmin):
    list_display = ("created_at", "user", "action", "actor_email", "organization", "ip_address")
    list_filter = ("action",)
    search_fields = ("user__email", "actor_email")
    readonly_fields = tuple(f.name for f in TwoFactorEvent._meta.fields)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

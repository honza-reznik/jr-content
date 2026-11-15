import uuid

from django.contrib.auth.models import AbstractBaseUser, BaseUserManager, PermissionsMixin
from django.db import models

from apps.common.encryption import EncryptedJSONField, EncryptedTextField


class UserManager(BaseUserManager):
    def create_user(self, email, password=None, **extra_fields):
        if not email:
            raise ValueError("Email is required")
        email = self.normalize_email(email)
        user = self.model(email=email, **extra_fields)
        if password:
            user.set_password(password)
        user.save(using=self._db)
        return user

    def create_superuser(self, email, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        return self.create_user(email, password, **extra_fields)


class User(AbstractBaseUser, PermissionsMixin):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    email = models.EmailField(unique=True)
    name = models.CharField(max_length=255, blank=True, default="")
    avatar = models.ImageField(upload_to="avatars/%Y/%m/", blank=True)
    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False)

    # 2FA fields (JR Content P2-B17: used by apps.accounts.twofactor).
    # totp_secret: base32 secret, encrypted; also holds the pending secret while setup is unconfirmed.
    # totp_recovery_codes: keyed hashes of the unused recovery codes, never the codes themselves.
    totp_secret = EncryptedTextField(blank=True, null=True)
    totp_recovery_codes = EncryptedJSONField(blank=True, null=True)
    totp_enabled = models.BooleanField(default=False)
    totp_enabled_at = models.DateTimeField(blank=True, null=True)
    # Last accepted TOTP time step; codes for this step or earlier are refused (no replay).
    totp_last_counter = models.BigIntegerField(blank=True, null=True)

    # Workspace persistence
    last_workspace_id = models.UUIDField(blank=True, null=True)

    # Terms of Service acceptance (null = not yet accepted)
    tos_accepted_at = models.DateTimeField(blank=True, null=True)

    # When the sidebar's Trustpilot card was last closed. It stays hidden until
    # the next login moves last_login past this. Kept here rather than in the
    # session because SESSION_SAVE_EVERY_REQUEST lets any request in flight
    # (the notification poll, an autosave) write its stale copy back over it.
    review_banner_dismissed_at = models.DateTimeField(blank=True, null=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = UserManager()

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = []

    class Meta:
        db_table = "accounts_user"

    def __str__(self):
        return self.email

    @property
    def display_name(self):
        if self.name:
            return self.name
        if self.email:
            return self.email.split("@")[0]
        return "User"


class TwoFactorEvent(models.Model):
    """Audit trail of two-factor changes (JR Content P2-B17)."""

    class Action(models.TextChoices):
        ENABLED = "enabled", "Two-factor authentication turned on"
        DISABLED = "disabled", "Two-factor authentication turned off"
        RESET_BY_ADMIN = "reset_by_admin", "Two-factor authentication reset by an organization owner"
        RECOVERY_CODES_REGENERATED = "recovery_codes_regenerated", "New recovery codes created"
        RECOVERY_CODE_USED = "recovery_code_used", "Recovery code used"
        LOCKED = "locked", "Too many incorrect codes"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="two_factor_events")
    action = models.CharField(max_length=40, choices=Action.choices)
    actor = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name="two_factor_actions")
    # Kept when the actor's account is later deleted.
    actor_email = models.CharField(max_length=254, blank=True, default="")
    organization = models.ForeignKey(
        "organizations.Organization", on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    ip_address = models.GenericIPAddressField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "accounts_two_factor_event"
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.user} {self.action} {self.created_at:%Y-%m-%d %H:%M}"


class OAuthConnection(models.Model):
    class Provider(models.TextChoices):
        GOOGLE = "google", "Google"
        GITHUB = "github", "GitHub"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="oauth_connections")
    provider = models.CharField(max_length=20, choices=Provider.choices)
    provider_user_id = models.CharField(max_length=255)
    provider_email = models.EmailField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "accounts_oauth_connection"
        unique_together = [("provider", "provider_user_id")]

    def __str__(self):
        return f"{self.user.email} - {self.provider}"


class Session(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="user_sessions")
    token_hash = models.CharField(max_length=64, unique=True, db_index=True)
    device_info = models.CharField(max_length=500, blank=True, default="")
    ip_address = models.GenericIPAddressField(blank=True, null=True)
    last_active_at = models.DateTimeField(auto_now=True)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()

    class Meta:
        db_table = "accounts_session"

    def __str__(self):
        return f"Session for {self.user.email}"

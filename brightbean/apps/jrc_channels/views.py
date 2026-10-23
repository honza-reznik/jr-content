from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods, require_POST

from apps.members.models import WorkspaceMembership
from apps.social_accounts.models import SocialAccount

from .forms import AddChannelForm, ChannelDetailsForm
from .models import ChannelProfile
from .services import create_manual_channel, set_channel_enabled


def _membership(request, workspace_id, *, editing=False):
    membership = get_object_or_404(
        WorkspaceMembership.objects.select_related("workspace", "custom_role"),
        user=request.user,
        workspace_id=workspace_id,
    )
    if editing and not membership.effective_permissions.get("manage_social_accounts"):
        raise PermissionDenied("You do not have permission to manage channels.")
    return membership


def _account(workspace_id, account_id):
    return get_object_or_404(
        SocialAccount.objects.select_related("jrc_channel"),
        pk=account_id,
        workspace_id=workspace_id,
        jrc_channel__isnull=False,
    )


def _profile(request, membership, *, add_form=None, edit_form=None, editing_id=None):
    accounts = list(
        SocialAccount.objects.filter(workspace=membership.workspace, jrc_channel__isnull=False)
        .select_related("jrc_channel")
        .order_by("created_at", "id")
    )
    channels = []
    for account in accounts:
        channels.append(
            {
                "account": account,
                "profile": account.jrc_channel,
                "is_editing": account.pk == editing_id,
                "form": edit_form
                if account.pk == editing_id
                else ChannelDetailsForm(
                    initial={
                        "name": account.jrc_channel.display_name or account.account_name,
                        "profile_url": account.jrc_channel.profile_url,
                    },
                    auto_id=f"channel-{account.pk}-%s",
                ),
            }
        )
    return render(
        request,
        "jrc_channels/profile.html",
        {
            "workspace": membership.workspace,
            "settings_active": "profile",
            "can_manage_channels": membership.effective_permissions.get("manage_social_accounts", False),
            "channels": channels,
            "enabled_count": sum(a.jrc_channel.is_enabled for a in accounts),
            "add_form": add_form if add_form is not None else AddChannelForm(auto_id="add-%s"),
        },
    )


@login_required
@require_http_methods(["GET", "POST"])
def client_profile(request, workspace_id):
    membership = _membership(request, workspace_id, editing=request.method == "POST")
    if request.method == "POST":
        form = AddChannelForm(request.POST, auto_id="add-%s")
        if form.is_valid():
            values = form.cleaned_data
            create_manual_channel(
                membership.workspace,
                values["platform"],
                values["name"],
                display_name=values["name"],
                profile_url=values["profile_url"],
            )
            messages.success(request, f"{values['name']} added.")
            return redirect("jrc_channels:profile", workspace_id=workspace_id)
        return _profile(request, membership, add_form=form)
    return _profile(request, membership)


@login_required
@require_POST
def channel_edit(request, workspace_id, account_id):
    membership = _membership(request, workspace_id, editing=True)
    account = _account(workspace_id, account_id)
    form = ChannelDetailsForm(request.POST, auto_id=f"channel-{account.pk}-%s")
    if not form.is_valid():
        return _profile(request, membership, edit_form=form, editing_id=account.pk)
    with transaction.atomic():
        # Use the same lock order as set_channel_enabled to keep edits and toggles safe.
        account = SocialAccount.objects.select_for_update().get(pk=account.pk)
        profile = ChannelProfile.objects.select_for_update().get(social_account=account)
        account.account_name = form.cleaned_data["name"]
        account.save(update_fields=["account_name", "updated_at"])
        profile.display_name = form.cleaned_data["name"]
        profile.profile_url = form.cleaned_data["profile_url"]
        profile.save(update_fields=["display_name", "profile_url", "updated_at"])
    messages.success(request, "Channel details saved.")
    return redirect("jrc_channels:profile", workspace_id=workspace_id)


@login_required
@require_POST
def channel_toggle(request, workspace_id, account_id):
    _membership(request, workspace_id, editing=True)
    account = _account(workspace_id, account_id)
    value = request.POST.get("enabled")
    if value not in {"true", "false"}:
        messages.error(request, "Choose whether to turn this channel on or off.")
    else:
        set_channel_enabled(account, enabled=value == "true")
        messages.success(
            request, "Channel turned on." if value == "true" else "Channel turned off. Your posts are kept."
        )
    return redirect("jrc_channels:profile", workspace_id=workspace_id)

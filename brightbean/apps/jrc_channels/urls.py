from django.urls import path

from . import views

app_name = "jrc_channels"

urlpatterns = [
    path("colour/", views.workspace_color, name="color"),
    path("channels/<uuid:account_id>/icon/", views.channel_icon, name="icon"),
    path("channels/<uuid:account_id>/colour/", views.channel_color, name="channel_color"),
    path("channels/<uuid:account_id>/favicon/", views.channel_favicon, name="favicon"),
    path("", views.client_profile, name="profile"),
    path("channels/<uuid:account_id>/edit/", views.channel_edit, name="edit"),
    path("channels/<uuid:account_id>/toggle/", views.channel_toggle, name="toggle"),
]

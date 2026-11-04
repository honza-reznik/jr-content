# JR Content: přidáno 2026-11-04 (blok P1-B10), AGPL-3.0.
from django.urls import path

from . import views

app_name = "jrc_thumbnails"
_base = "workspace/<uuid:workspace_id>/content/<uuid:post_id>/channels/<uuid:platform_post_id>/"
urlpatterns = [
    path(_base + "thumbnail/", views.upload, name="upload"),
    path(_base + "thumbnails/<uuid:thumbnail_id>/download/", views.download, name="download"),
]

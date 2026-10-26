# JR Content: přidáno 2026-10-23 (blok F2-B10), AGPL-3.0.
from django.urls import path

from . import views

app_name = "jrc_ui"
urlpatterns = [
    path("", views.about, name="about"),
    path("license/", views.license_text, name="license"),
    path("source-code/", views.source_code, name="source_code"),
]

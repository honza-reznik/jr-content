from django.urls import path

from . import views

app_name = "jrc_production"

urlpatterns = [
    path("content/<uuid:post_id>/", views.content_detail, name="content_detail"),
]

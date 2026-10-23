from django.urls import path

from . import views

app_name = "jrc_texts"
urlpatterns = [
    path("workspace/<uuid:workspace_id>/content/<uuid:post_id>/script/", views.script, name="script"),
]

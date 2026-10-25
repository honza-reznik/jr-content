from django.urls import path

from . import upload_views as views

app_name = "jrc_video"
_base = "workspace/<uuid:workspace_id>/content/<uuid:post_id>/video/"
urlpatterns = [
    path(_base + "start/", views.start, name="upload_start"),
    path(_base + "status/", views.status, name="upload_status"),
    path(_base + "versions/<uuid:version_id>/resume/", views.resume, name="upload_resume"),
    path(_base + "versions/<uuid:version_id>/part/", views.part, name="upload_part"),
    path(_base + "versions/<uuid:version_id>/complete/", views.complete, name="upload_complete"),
    path(_base + "versions/<uuid:version_id>/abort/", views.abort, name="upload_abort"),
    path(_base + "versions/<uuid:version_id>/final/", views.final, name="video_final"),
    path(_base + "channels/<uuid:platform_post_id>/final/download/", views.final_download, name="final_download"),
]

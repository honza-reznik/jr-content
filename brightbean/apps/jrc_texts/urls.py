from django.urls import path

from . import views

app_name = "jrc_texts"
urlpatterns = [
    path("workspace/<uuid:workspace_id>/content/<uuid:post_id>/script/", views.script, name="script"),
    # P1-B08: the Concept tab (same editor rules as the script) and "Use as script draft".
    path("workspace/<uuid:workspace_id>/content/<uuid:post_id>/concept/", views.concept, name="concept"),
    path(
        "workspace/<uuid:workspace_id>/content/<uuid:post_id>/concept/to-script/",
        views.concept_to_script,
        name="concept_to_script",
    ),
]

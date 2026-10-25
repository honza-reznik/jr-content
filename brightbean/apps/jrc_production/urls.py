from django.urls import path

from . import views

app_name = "jrc_production"
urlpatterns = [
    path("board/", views.board, name="board"),
    path("board/create/", views.create, name="create"),
    path("workspace/<uuid:workspace_id>/board/<uuid:post_id>/move/", views.move, name="move"),
    path("workspace/<uuid:workspace_id>/content/<uuid:post_id>/", views.content_detail, name="content_detail"),
    path(
        "workspace/<uuid:workspace_id>/content/<uuid:post_id>/approval/",
        views.approval_decision,
        name="approval_decision",
    ),
    path(
        "workspace/<uuid:workspace_id>/content/<uuid:post_id>/comments/",
        views.add_content_comment,
        name="add_content_comment",
    ),
    path(
        "workspace/<uuid:workspace_id>/content/<uuid:post_id>/comments/<uuid:comment_id>/reply/",
        views.reply_to_content_comment,
        name="reply_to_content_comment",
    ),
    path(
        "workspace/<uuid:workspace_id>/content/<uuid:post_id>/comments/<uuid:comment_id>/resolve/",
        views.resolve_content_comment,
        name="resolve_content_comment",
    ),
]

from django.urls import path

from . import board_actions, version_comments, views

app_name = "jrc_production"
urlpatterns = [
    path(
        "workspace/<uuid:workspace_id>/content/<uuid:post_id>/version-comments/",
        version_comments.panel,
        name="version_comments",
    ),
    path(
        "workspace/<uuid:workspace_id>/content/<uuid:post_id>/version-comments/<uuid:comment_id>/",
        version_comments.version,
        name="comment_version",
    ),
    path("board/actions/", board_actions.batch, name="board_actions"),
    path("board/trash/", views.trash, name="trash"),
    path(
        "workspace/<uuid:workspace_id>/content/<uuid:post_id>/proposed-date/", views.proposed_date, name="proposed_date"
    ),
    path("workspace/<uuid:workspace_id>/content/<uuid:post_id>/category/", views.category, name="category"),
    path("board/", views.board, name="board"),
    path("board/new/", views.new_content, name="new_content"),
    path("board/create/", views.create, name="create"),
    path("board/idea/", views.quick_idea, name="quick_idea"),
    path("workspace/<uuid:workspace_id>/board/<uuid:post_id>/move/", views.move, name="move"),
    path("workspace/<uuid:workspace_id>/content/<uuid:post_id>/", views.content_detail, name="content_detail"),
    path("workspace/<uuid:workspace_id>/content/<uuid:post_id>/rename/", views.rename, name="rename"),
    # P2-B08: channel tags and automatic caption saving without a page load.
    path("workspace/<uuid:workspace_id>/content/<uuid:post_id>/channels/", views.channels, name="channels"),
    path(
        "workspace/<uuid:workspace_id>/content/<uuid:post_id>/captions/<uuid:platform_post_id>/autosave/",
        views.caption_autosave,
        name="caption_autosave",
    ),
    path(
        "workspace/<uuid:workspace_id>/content/<uuid:post_id>/shotlist/",
        views.shotlist_action,
        name="shotlist_action",
    ),
    path(
        "workspace/<uuid:workspace_id>/content/<uuid:post_id>/publish/",
        views.mark_published,
        name="mark_published",
    ),
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

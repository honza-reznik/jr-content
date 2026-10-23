from django.urls import path

from . import views

app_name = "jrc_production"
urlpatterns = [
    path("board/", views.board, name="board"),
    path("board/create/", views.create, name="create"),
    path("workspace/<uuid:workspace_id>/board/<uuid:post_id>/move/", views.move, name="move"),
]

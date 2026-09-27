from django.urls import path

from . import views

app_name = "posts"

urlpatterns = [
    path("compose/", views.compose_view, name="compose"),
    path("saved/", views.saved_view, name="saved"),
    path("t/<slug:slug>/", views.topic_view, name="topic"),
    path("p/<int:pk>/", views.detail_view, name="detail"),
    path("p/<int:pk>/delete/", views.post_delete_view, name="delete"),
    path("p/<int:pk>/vote/", views.post_vote_view, name="vote"),
    path("p/<int:pk>/bookmark/", views.bookmark_view, name="bookmark"),
    path("p/<int:pk>/poll/", views.poll_vote_view, name="poll-vote"),
    path("p/<int:pk>/comment/", views.comment_create_view, name="comment"),
    path("p/<int:pk>/files/<int:attachment_id>/", views.attachment_download_view, name="attachment"),
    path("c/<int:pk>/delete/", views.comment_delete_view, name="comment-delete"),
    path("c/<int:pk>/vote/", views.comment_vote_view, name="comment-vote"),
    path("c/<int:pk>/accept/", views.accept_answer_view, name="accept"),
]

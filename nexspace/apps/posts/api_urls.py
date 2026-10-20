from django.urls import path

from . import api_views as v

urlpatterns = [
    path("posts/", v.PostListCreateAPIView.as_view(), name="api-posts"),
    path("posts/<int:pk>/", v.PostDetailAPIView.as_view(), name="api-post"),
    path("posts/<int:pk>/vote/", v.PostVoteAPIView.as_view(), name="api-post-vote"),
    path("posts/<int:pk>/bookmark/", v.PostBookmarkAPIView.as_view(), name="api-post-bookmark"),
    path("posts/<int:pk>/poll-vote/", v.PollVoteAPIView.as_view(), name="api-poll-vote"),
    path("posts/<int:pk>/comments/", v.CommentListCreateAPIView.as_view(), name="api-comments"),
    path("comments/<int:pk>/", v.CommentDetailAPIView.as_view(), name="api-comment"),
    path("comments/<int:pk>/vote/", v.CommentVoteAPIView.as_view(), name="api-comment-vote"),
    path("comments/<int:pk>/accept/", v.AcceptAnswerAPIView.as_view(), name="api-comment-accept"),
    path("bookmarks/", v.BookmarkListAPIView.as_view(), name="api-bookmarks"),
    path("users/<str:username>/follow/", v.UserFollowAPIView.as_view(), name="api-user-follow"),
    path("topics/<slug:slug>/follow/", v.TopicFollowAPIView.as_view(), name="api-topic-follow"),
]

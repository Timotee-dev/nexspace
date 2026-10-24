from django.urls import path

from . import api

app_name = "mobile"

urlpatterns = [
    path("auth/login/", api.LoginAPIView.as_view(), name="login"),
    path("auth/signup/", api.SignupAPIView.as_view(), name="signup"),
    path("auth/logout/", api.LogoutAPIView.as_view(), name="logout"),
    path("auth/verify/", api.VerifyCodeAPIView.as_view(), name="verify"),
    path("auth/resend/", api.ResendCodeAPIView.as_view(), name="resend"),
    path("auth/password-reset/", api.PasswordResetAPIView.as_view(), name="password-reset"),
    path("auth/devices/", api.DevicesAPIView.as_view(), name="devices"),
    path("me/", api.MeAPIView.as_view(), name="me"),
    path("departments/", api.DepartmentsAPIView.as_view(), name="departments"),
    path("counts/", api.CountsAPIView.as_view(), name="counts"),
    path("courses/", api.CoursesAPIView.as_view(), name="courses"),
    path("courses/<slug:slug>/", api.CourseDetailAPIView.as_view(), name="course"),
    path("spaces/", api.SpacesAPIView.as_view(), name="spaces"),
    path("spaces/<int:pk>/join/", api.SpaceJoinAPIView.as_view(), name="space-join"),
    path("spaces/<int:pk>/posts/", api.SpacePostsAPIView.as_view(), name="space-posts"),
    path("calendar/", api.CalendarAPIView.as_view(), name="calendar"),
    path("announcements/", api.AnnouncementsAPIView.as_view(), name="announcements"),
    path("conversations/", api.ConversationsAPIView.as_view(), name="conversations"),
    path("conversations/start/<str:username>/", api.StartConversationAPIView.as_view(), name="start"),
    path("conversations/<int:pk>/messages/", api.ConversationMessagesAPIView.as_view(), name="messages"),
    path("conversations/<int:pk>/messages/<int:message_id>/", api.DeleteMessageAPIView.as_view(), name="message"),
    path("users/<str:username>/", api.ProfileAPIView.as_view(), name="profile"),
    path("posts/<int:pk>/repost/", api.RepostAPIView.as_view(), name="repost"),
    path("posts/<int:pk>/edit/", api.EditPostAPIView.as_view(), name="edit-post"),
    path("reports/", api.ReportAPIView.as_view(), name="report"),
    path("files/<str:token>/", api.file_view, name="file"),
]

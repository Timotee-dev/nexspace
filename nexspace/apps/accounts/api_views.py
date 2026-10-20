from django.contrib.auth import authenticate, login, logout
from django.core.cache import cache
from django.http import Http404
from django.shortcuts import get_object_or_404
from drf_spectacular.utils import extend_schema
from rest_framework import generics, status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core import ratelimit
from apps.core.throttles import AuthRateThrottle

from . import services
from .models import User
from .serializers import LoginSerializer, MeSerializer, PublicProfileSerializer, SignupSerializer


def _error(code, message, http_status, fields=None):
    return Response({"error": {"code": code, "message": message, "fields": fields or {}}}, status=http_status)


class SignupAPIView(APIView):
    permission_classes = [AllowAny]
    throttle_classes = [AuthRateThrottle]

    @extend_schema(request=SignupSerializer, responses={201: MeSerializer})
    def post(self, request):
        serializer = SignupSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        user = services.register_user(
            email=data["email"], password=data["password"], full_name=data["full_name"],
            department=data["department"], level=data["level"], matric_number=data.get("matric_number"),
        )
        services.send_verification_email(user)
        login(request, user, backend="django.contrib.auth.backends.ModelBackend")
        return Response(MeSerializer(user).data, status=status.HTTP_201_CREATED)


class LoginAPIView(APIView):
    permission_classes = [AllowAny]
    throttle_classes = [AuthRateThrottle]

    @extend_schema(request=LoginSerializer, responses={200: MeSerializer})
    def post(self, request):
        serializer = LoginSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        email = serializer.validated_data["email"].strip().lower()
        if ratelimit.is_locked(request, email):
            return _error("locked", "Too many failed attempts. Try again in 15 minutes.", 429)
        user = authenticate(request, email=email, password=serializer.validated_data["password"])
        if user is None:
            ratelimit.record_failure(request, email)
            return _error("invalid_credentials", "That email and password don't match an account.", 400)
        ratelimit.clear_failures(request, email)
        login(request, user)
        return Response(MeSerializer(user).data)


class LogoutAPIView(APIView):
    @extend_schema(request=None, responses={204: None})
    def post(self, request):
        logout(request)
        return Response(status=status.HTTP_204_NO_CONTENT)


class MeAPIView(generics.RetrieveUpdateAPIView):
    """The signed-in user's account and profile. Editing your own profile is allowed before verification."""

    serializer_class = MeSerializer
    http_method_names = ["get", "patch", "head", "options"]

    def get_object(self):
        return User.objects.select_related("profile", "department").get(pk=self.request.user.pk)


class ResendVerificationAPIView(APIView):
    @extend_schema(request=None, responses={202: None})
    def post(self, request):
        if request.user.email_verified:
            return _error("already_verified", "Your email is already verified.", 400)
        if not cache.add(f"resend-verify:{request.user.pk}", 1, 60):
            return _error("throttled", "Wait a minute before requesting another email.", 429)
        services.send_verification_email(request.user)
        return Response({"detail": "Verification email sent."}, status=status.HTTP_202_ACCEPTED)


class PublicProfileAPIView(generics.RetrieveAPIView):
    serializer_class = PublicProfileSerializer
    permission_classes = [IsAuthenticated]

    def get_object(self):
        user = get_object_or_404(
            User.objects.select_related("profile", "department").prefetch_related("profile__interests"),
            username=self.kwargs["username"].lower(),
            is_active=True,
        )
        if not user.profile.can_be_viewed_by(self.request.user):
            raise Http404
        return user

from django.contrib.auth import password_validation
from rest_framework import serializers

from apps.academics.models import Department, Level
from apps.topics.models import Topic

from .models import RESERVED_USERNAMES, Profile, User


class SignupSerializer(serializers.Serializer):
    full_name = serializers.CharField(max_length=120)
    email = serializers.EmailField()
    password = serializers.CharField(write_only=True)
    confirm_password = serializers.CharField(write_only=True)
    department = serializers.PrimaryKeyRelatedField(queryset=Department.objects.filter(is_active=True))
    level = serializers.ChoiceField(choices=Level.choices)
    matric_number = serializers.CharField(max_length=30, required=False, allow_blank=True, allow_null=True)

    def validate_email(self, value):
        value = value.strip().lower()
        if User.objects.filter(email=value).exists():
            raise serializers.ValidationError("An account with this email already exists.")
        return value

    def validate_matric_number(self, value):
        value = (value or "").strip().upper()
        if value and User.objects.filter(matric_number=value).exists():
            raise serializers.ValidationError("This matric number is already linked to another account.")
        return value or None

    def validate(self, attrs):
        if attrs["password"] != attrs["confirm_password"]:
            raise serializers.ValidationError({"confirm_password": "Passwords don't match."})
        candidate = User(email=attrs["email"], full_name=attrs["full_name"])
        try:
            password_validation.validate_password(attrs["password"], candidate)
        except Exception as exc:  # django ValidationError → DRF field error
            raise serializers.ValidationError({"password": list(getattr(exc, "messages", [str(exc)]))})
        return attrs


class LoginSerializer(serializers.Serializer):
    email = serializers.EmailField()
    password = serializers.CharField(write_only=True)


class MeSerializer(serializers.ModelSerializer):
    """The signed-in user's own account + profile. Editable fields are explicit."""

    department = serializers.SerializerMethodField()
    bio = serializers.CharField(source="profile.bio", max_length=280, required=False, allow_blank=True)
    skills = serializers.ListField(
        source="profile.skills", child=serializers.CharField(max_length=40), max_length=15, required=False
    )
    github_url = serializers.URLField(source="profile.github_url", required=False, allow_blank=True)
    linkedin_url = serializers.URLField(source="profile.linkedin_url", required=False, allow_blank=True)
    portfolio_url = serializers.URLField(source="profile.portfolio_url", required=False, allow_blank=True)
    theme = serializers.ChoiceField(source="profile.theme", choices=Profile.Theme.choices, required=False)
    visibility = serializers.ChoiceField(
        source="profile.visibility", choices=Profile.Visibility.choices, required=False
    )
    show_matric_number = serializers.BooleanField(source="profile.show_matric_number", required=False)
    show_social_links = serializers.BooleanField(source="profile.show_social_links", required=False)
    show_joined_spaces = serializers.BooleanField(source="profile.show_joined_spaces", required=False)
    interests = serializers.SlugRelatedField(
        source="profile.interests", many=True, slug_field="slug",
        queryset=Topic.objects.filter(is_active=True), required=False,
    )
    nexscore = serializers.IntegerField(source="profile.nexscore", read_only=True)
    avatar_url = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = [
            "id", "email", "full_name", "username", "department", "level", "matric_number",
            "email_verified", "onboarding_completed", "avatar_url", "bio", "skills",
            "github_url", "linkedin_url", "portfolio_url", "interests", "nexscore", "theme",
            "visibility", "show_matric_number", "show_social_links", "show_joined_spaces",
        ]
        read_only_fields = ["id", "email", "email_verified", "onboarding_completed"]

    def get_department(self, obj) -> dict | None:
        if not obj.department:
            return None
        return {"id": obj.department_id, "name": obj.department.name, "code": obj.department.code}

    def get_avatar_url(self, obj) -> str | None:
        return obj.profile.avatar.url if obj.profile.avatar else None

    def validate_username(self, value):
        value = value.lower()
        if value in RESERVED_USERNAMES:
            raise serializers.ValidationError("That username is reserved.")
        if User.objects.filter(username=value).exclude(pk=self.instance.pk).exists():
            raise serializers.ValidationError("That username is taken.")
        return value

    def validate_matric_number(self, value):
        value = (value or "").strip().upper()
        if value and User.objects.filter(matric_number=value).exclude(pk=self.instance.pk).exists():
            raise serializers.ValidationError("This matric number is already linked to another account.")
        return value or None

    def update(self, instance, validated_data):
        profile_data = validated_data.pop("profile", {})
        interests = profile_data.pop("interests", None)
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        instance.save()
        profile = instance.profile
        for attr, value in profile_data.items():
            setattr(profile, attr, value)
        profile.save()
        if interests is not None:
            profile.interests.set(interests)
        return instance


class PublicProfileSerializer(serializers.ModelSerializer):
    """What other users may see. Privacy settings are applied in to_representation."""

    department = serializers.CharField(source="department.name", default=None)
    level = serializers.CharField(source="level_label")
    avatar_url = serializers.SerializerMethodField()
    bio = serializers.CharField(source="profile.bio")
    skills = serializers.ListField(source="profile.skills")
    nexscore = serializers.IntegerField(source="profile.nexscore")
    interests = serializers.SlugRelatedField(source="profile.interests", many=True, read_only=True, slug_field="name")

    class Meta:
        model = User
        fields = ["username", "full_name", "department", "level", "avatar_url", "bio", "skills", "nexscore", "interests"]

    def get_avatar_url(self, obj) -> str | None:
        return obj.profile.avatar.url if obj.profile.avatar else None

    def to_representation(self, instance):
        data = super().to_representation(instance)
        viewer = self.context["request"].user
        profile = instance.profile
        is_owner = viewer.pk == instance.pk
        if instance.matric_number and (is_owner or profile.show_matric_number):
            data["matric_number"] = instance.matric_number
        if is_owner or profile.show_social_links:
            data["links"] = {
                "github": profile.github_url or None,
                "linkedin": profile.linkedin_url or None,
                "portfolio": profile.portfolio_url or None,
            }
        return data

from django import forms
from django.contrib.auth import authenticate, password_validation

from apps.academics.models import Department, Level
from apps.topics.models import Topic

from .models import RESERVED_USERNAMES, Profile, User

MAX_SKILLS = 15


class DepartmentChoiceField(forms.ModelChoiceField):
    def label_from_instance(self, obj):
        return f"{obj.name} · {obj.faculty.university.short_name}"


class SignupForm(forms.Form):
    ACCOUNT_TYPES = [("student", "Student"), ("staff", "Staff (HOD, lecturer, adviser, exam officer)")]

    account_type = forms.ChoiceField(choices=ACCOUNT_TYPES, initial="student", widget=forms.RadioSelect,
                                     label="I am a", required=False)
    full_name = forms.CharField(max_length=120, widget=forms.TextInput(attrs={"autocomplete": "name"}))
    email = forms.EmailField(widget=forms.EmailInput(attrs={"autocomplete": "email"}))
    department = DepartmentChoiceField(
        queryset=Department.objects.filter(is_active=True).select_related("faculty__university"),
        empty_label="Choose your department",
    )
    level = forms.TypedChoiceField(choices=[("", "Choose your level"), *Level.choices], coerce=int,
                                   required=False, empty_value=None)
    matric_number = forms.CharField(
        max_length=30, required=False, help_text="Optional. Only you can see it unless you choose to show it."
    )
    # Staff only
    position = forms.ChoiceField(choices=[("", "Choose your position")], required=False)
    title = forms.ChoiceField(choices=[], required=False)
    staff_id = forms.CharField(max_length=30, required=False, label="Staff ID",
                               help_text="Optional, but it helps your HOD verify you quickly.")
    courses = forms.ModelMultipleChoiceField(queryset=None, required=False, widget=forms.CheckboxSelectMultiple,
                                             label="Courses you teach")
    adviser_level = forms.TypedChoiceField(choices=[("", "Choose the level you advise"), *Level.choices],
                                           coerce=int, required=False, empty_value=None, label="Level you advise")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from apps.academics.models import Course

        from .models import StaffProfile

        self.fields["position"].choices = [("", "Choose your position"), *StaffProfile.Position.choices]
        self.fields["title"].choices = [(v, l) for v, l in StaffProfile.Title.choices]
        self.fields["courses"].queryset = Course.objects.filter(is_active=True).select_related("department")
        self.fields["courses"].label_from_instance = lambda c: f"{c.code} — {c.title}"
    password = forms.CharField(widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}))
    confirm_password = forms.CharField(widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}))

    def clean_email(self):
        email = self.cleaned_data["email"].strip().lower()
        if User.objects.filter(email=email).exists():
            raise forms.ValidationError("An account with this email already exists. Log in instead.")
        return email

    def clean_full_name(self):
        name = " ".join(self.cleaned_data["full_name"].split())
        if len(name) < 2:
            raise forms.ValidationError("Enter your full name.")
        return name

    def clean_matric_number(self):
        value = self.cleaned_data.get("matric_number", "").strip().upper()
        if value and User.objects.filter(matric_number=value).exists():
            raise forms.ValidationError("This matric number is already linked to another account.")
        return value or None

    def clean(self):
        data = super().clean()
        data["account_type"] = data.get("account_type") or "student"
        if data["account_type"] == "staff":
            position = data.get("position")
            if not position:
                self.add_error("position", "Choose your position.")
            if position == "lecturer":
                dept = data.get("department")
                # Optional: the department may not have added its courses yet, or the course may be new.
                # The HOD or platform admin links lecturers to courses when verifying them.
                data["courses"] = [c for c in data.get("courses") or [] if dept and c.department_id == dept.pk]
            if position == "level_adviser" and not data.get("adviser_level"):
                self.add_error("adviser_level", "Choose the level you advise.")
            data["level"] = None
        elif not data.get("level"):
            self.add_error("level", "Choose your level.")
        password, confirm = data.get("password"), data.get("confirm_password")
        if password and confirm and password != confirm:
            self.add_error("confirm_password", "Passwords don't match.")
        if password:
            candidate = User(email=data.get("email", ""), full_name=data.get("full_name", ""))
            try:
                password_validation.validate_password(password, candidate)
            except forms.ValidationError as exc:
                self.add_error("password", exc)
        return data


class LoginForm(forms.Form):
    email = forms.EmailField(widget=forms.EmailInput(attrs={"autocomplete": "email", "autofocus": True}))
    password = forms.CharField(widget=forms.PasswordInput(attrs={"autocomplete": "current-password"}))

    def __init__(self, request=None, *args, **kwargs):
        self.request = request
        self.user = None
        super().__init__(*args, **kwargs)

    def clean(self):
        data = super().clean()
        email, password = data.get("email", "").strip().lower(), data.get("password")
        if email and password:
            self.user = authenticate(self.request, email=email, password=password)
            if self.user is None:
                raise forms.ValidationError("That email and password don't match an account.")
        return data


class ProfileForm(forms.Form):
    full_name = forms.CharField(max_length=120)
    username = forms.RegexField(
        regex=r"^[a-z0-9_]{3,30}$",
        error_messages={"invalid": "Use 3–30 lowercase letters, numbers or underscores."},
    )
    level = forms.TypedChoiceField(choices=Level.choices, coerce=int, required=False, empty_value=None)
    matric_number = forms.CharField(max_length=30, required=False)
    avatar = forms.ImageField(required=False)
    remove_avatar = forms.BooleanField(required=False)
    bio = forms.CharField(max_length=280, required=False, widget=forms.Textarea(attrs={"rows": 3}))
    skills = forms.CharField(
        required=False, help_text="Separate skills with commas, e.g. Python, UI design, Research"
    )
    github_url = forms.URLField(required=False, label="GitHub")
    linkedin_url = forms.URLField(required=False, label="LinkedIn")
    portfolio_url = forms.URLField(required=False, label="Portfolio")

    def __init__(self, *args, user, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)
        if user.staff:  # staff don't belong to a level
            del self.fields["level"]
            del self.fields["matric_number"]
        else:
            self.fields["level"].required = True

    @classmethod
    def initial_for(cls, user):
        p = user.profile
        return {
            "full_name": user.full_name,
            "username": user.username,
            "level": user.level,
            "matric_number": user.matric_number or "",
            "bio": p.bio,
            "skills": ", ".join(p.skills),
            "github_url": p.github_url,
            "linkedin_url": p.linkedin_url,
            "portfolio_url": p.portfolio_url,
        }

    def clean_username(self):
        username = self.cleaned_data["username"].lower()
        if username in RESERVED_USERNAMES:
            raise forms.ValidationError("That username is reserved. Try another.")
        if User.objects.filter(username=username).exclude(pk=self.user.pk).exists():
            raise forms.ValidationError("That username is taken.")
        return username

    def clean_matric_number(self):
        value = (self.cleaned_data.get("matric_number") or "").strip().upper()
        if value and User.objects.filter(matric_number=value).exclude(pk=self.user.pk).exists():
            raise forms.ValidationError("This matric number is already linked to another account.")
        return value or None

    def clean_avatar(self):
        from apps.core.uploads import validate_image_upload

        avatar = self.cleaned_data.get("avatar")
        if avatar and hasattr(avatar, "size") and avatar is not self.user.profile.avatar:
            validate_image_upload(avatar)
        return avatar

    def clean_skills(self):
        raw = self.cleaned_data.get("skills", "")
        skills, seen = [], set()
        for item in raw.split(","):
            skill = " ".join(item.split())[:40]
            if skill and skill.lower() not in seen:
                seen.add(skill.lower())
                skills.append(skill)
        if len(skills) > MAX_SKILLS:
            raise forms.ValidationError(f"List up to {MAX_SKILLS} skills.")
        return skills

    def save(self):
        user, profile, data = self.user, self.user.profile, self.cleaned_data
        user.full_name = data["full_name"]
        user.username = data["username"]
        if "level" in self.fields:
            user.level = data["level"]
            user.matric_number = data["matric_number"]
        user.save()
        if data.get("remove_avatar") and profile.avatar:
            profile.avatar.delete(save=False)
            profile.avatar = ""
        elif data.get("avatar"):
            from apps.core.uploads import optimize_image

            profile.avatar = optimize_image(data["avatar"])
        for field in ("bio", "skills", "github_url", "linkedin_url", "portfolio_url"):
            setattr(profile, field, data[field])
        profile.save()
        return user


class PrivacyForm(forms.ModelForm):
    class Meta:
        model = Profile
        fields = ["visibility", "allow_messages_from", "show_matric_number", "show_social_links", "show_joined_spaces"]
        widgets = {"visibility": forms.RadioSelect, "allow_messages_from": forms.RadioSelect}
        labels = {
            "visibility": "Who can see your profile",
            "allow_messages_from": "Who can send you direct messages",
            "show_matric_number": "Show my matric number on my profile",
            "show_social_links": "Show my GitHub, LinkedIn and portfolio links",
            "show_joined_spaces": "Show the Spaces I've joined",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["allow_messages_from"].required = False

    def clean_allow_messages_from(self):
        return self.cleaned_data.get("allow_messages_from") or self.instance.allow_messages_from


class AppearanceForm(forms.ModelForm):
    class Meta:
        model = Profile
        fields = ["theme"]
        widgets = {"theme": forms.RadioSelect}
        labels = {"theme": "Theme"}


class InterestsForm(forms.Form):
    interests = forms.ModelMultipleChoiceField(
        queryset=Topic.objects.filter(is_active=True),
        widget=forms.CheckboxSelectMultiple,
        required=False,
    )


class LevelForm(forms.Form):
    level = forms.TypedChoiceField(choices=Level.choices, coerce=int, widget=forms.RadioSelect)

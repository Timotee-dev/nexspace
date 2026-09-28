from django import forms

from apps.academics.models import Course, Level
from apps.spaces.models import Space

from .models import AcademicEvent, Announcement, Audience

DT = forms.DateTimeInput(attrs={"type": "datetime-local"}, format="%Y-%m-%dT%H:%M")


class AudienceMixin(forms.Form):
    audience = forms.ChoiceField(choices=Audience.choices, label="Who is this for?")
    target_level = forms.TypedChoiceField(choices=[("", "Choose a level"), *Level.choices], coerce=int,
                                          required=False, empty_value=None, label="Level")
    target_course = forms.ModelChoiceField(queryset=Course.objects.none(), required=False, label="Course",
                                           empty_label="Choose a course")
    target_space = forms.ModelChoiceField(queryset=Space.objects.none(), required=False, label="Space",
                                          empty_label="Choose a Space")

    def setup_audience(self, user, scope):
        """Offer only the audiences this person may publish to (see notices.services.publish_scope)."""
        choices = []
        if scope["department"]:
            choices.append((Audience.DEPARTMENT, Audience.DEPARTMENT.label))
        if scope["levels"]:
            choices.append((Audience.LEVEL, Audience.LEVEL.label))
            self.fields["target_level"].choices = [("", "Choose a level")] + [
                (v, l) for v, l in Level.choices if v in scope["levels"]]
        else:
            del self.fields["target_level"]
        if scope["courses"]:
            choices.append((Audience.COURSE, Audience.COURSE.label))
            self.fields["target_course"].queryset = Course.objects.filter(pk__in=scope["courses"])
        else:
            del self.fields["target_course"]
        if scope["spaces"]:
            choices.append((Audience.SPACE, Audience.SPACE.label))
            self.fields["target_space"].queryset = Space.objects.filter(department_id=user.department_id)
        else:
            del self.fields["target_space"]
        self.fields["audience"].choices = choices


class AnnouncementForm(AudienceMixin):
    title = forms.CharField(max_length=150)
    body = forms.CharField(max_length=3000, widget=forms.Textarea(attrs={"rows": 6}), label="Message")
    priority = forms.ChoiceField(choices=Announcement.Priority.choices, initial="normal")
    expires_at = forms.DateTimeField(required=False, widget=DT, label="Expires (optional)",
                                     help_text="After this it moves to the archive.")
    attachment = forms.FileField(required=False, help_text="Optional PDF, Word or PowerPoint file")


class EventForm(AudienceMixin):
    title = forms.CharField(max_length=150)
    kind = forms.ChoiceField(choices=AcademicEvent.Kind.choices, label="Type")
    starts_at = forms.DateTimeField(widget=DT, label="Starts")
    ends_at = forms.DateTimeField(widget=DT, required=False, label="Ends (optional)")
    location = forms.CharField(max_length=150, required=False, label="Location (optional)")
    description = forms.CharField(max_length=1500, required=False, widget=forms.Textarea(attrs={"rows": 3}),
                                  label="Details (optional)")

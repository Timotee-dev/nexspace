from django import forms
from django.utils import timezone

from apps.topics.models import Topic

from .models import BODY_MAX, COMMENT_MAX, MAX_TOPICS, OpportunityDetails, Post

DATETIME_WIDGET = forms.DateTimeInput(attrs={"type": "datetime-local"}, format="%Y-%m-%dT%H:%M")


class ComposerForm(forms.Form):
    kind = forms.ChoiceField(choices=[c for c in Post.Kind.choices if c[0] != Post.Kind.REPOST],
                             initial=Post.Kind.POST, widget=forms.RadioSelect)
    title = forms.CharField(max_length=150, required=False)
    body = forms.CharField(max_length=BODY_MAX, required=False, widget=forms.Textarea(attrs={"rows": 5}))
    topics = forms.ModelMultipleChoiceField(
        queryset=Topic.objects.filter(is_active=True), required=False, widget=forms.CheckboxSelectMultiple
    )
    space = forms.ModelChoiceField(queryset=None, required=False, empty_label="No Space (department feed)",
                                   label="Post in")
    is_anonymous = forms.BooleanField(required=False, label="Post anonymously")
    is_official = forms.BooleanField(required=False, label="Official department update")

    poll_multiple = forms.BooleanField(required=False, label="Allow picking more than one option")
    poll_closes_at = forms.DateTimeField(required=False, widget=DATETIME_WIDGET, label="Closes (optional)")

    event_starts_at = forms.DateTimeField(required=False, widget=DATETIME_WIDGET, label="Starts")
    event_ends_at = forms.DateTimeField(required=False, widget=DATETIME_WIDGET, label="Ends (optional)")
    event_location = forms.CharField(max_length=150, required=False, label="Location (optional)")

    opp_organization = forms.CharField(max_length=120, required=False, label="Organization")
    opp_category = forms.ChoiceField(
        choices=[("", "Choose a category"), *OpportunityDetails.Category.choices], required=False, label="Category"
    )
    opp_deadline = forms.DateField(required=False, widget=forms.DateInput(attrs={"type": "date"}), label="Deadline (optional)")
    opp_apply_url = forms.URLField(required=False, label="Application link (optional)")
    opp_location = forms.CharField(max_length=120, required=False, label="Location or 'Remote' (optional)")

    def __init__(self, *args, poll_options=None, can_post_official=False, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        from apps.spaces.models import Space

        self.fields["space"].queryset = (
            Space.objects.filter(memberships__user=user).order_by("-is_official", "name") if user else Space.objects.none()
        )
        self.poll_options = poll_options or []
        if not can_post_official:
            del self.fields["is_official"]

    def clean_topics(self):
        topics = self.cleaned_data["topics"]
        if len(topics) > MAX_TOPICS:
            raise forms.ValidationError(f"Pick up to {MAX_TOPICS} topics.")
        return topics

    def clean(self):
        data = super().clean()
        kind = data.get("kind")
        ends, starts = data.get("event_ends_at"), data.get("event_starts_at")
        if kind == Post.Kind.EVENT and starts and ends and ends <= starts:
            self.add_error("event_ends_at", "The end must be after the start.")
        if kind == Post.Kind.OPPORTUNITY and data.get("opp_deadline") and data["opp_deadline"] < timezone.localdate():
            self.add_error("opp_deadline", "The deadline has already passed.")
        return data

    def service_kwargs(self):
        d = self.cleaned_data
        kind = d["kind"]
        kwargs = {
            "kind": kind, "title": d.get("title", ""), "body": d.get("body", ""), "topics": d.get("topics", []),
            "is_anonymous": d.get("is_anonymous", False) and kind in Post.ANONYMOUS_KINDS,
            "is_official": d.get("is_official", False),
            "space": d.get("space"),
        }
        if kind == Post.Kind.POLL:
            kwargs.update(poll_options=self.poll_options, poll_multiple=d.get("poll_multiple", False),
                          poll_closes_at=d.get("poll_closes_at"))
        elif kind == Post.Kind.EVENT:
            kwargs["event"] = {"starts_at": d.get("event_starts_at"), "ends_at": d.get("event_ends_at"),
                               "location": d.get("event_location", "")} if d.get("event_starts_at") else None
        elif kind == Post.Kind.OPPORTUNITY:
            kwargs["opportunity"] = {
                "organization": d.get("opp_organization", ""), "category": d.get("opp_category", ""),
                "deadline": d.get("opp_deadline"), "apply_url": d.get("opp_apply_url", ""),
                "location": d.get("opp_location", ""),
            }
        return kwargs


class CommentForm(forms.Form):
    body = forms.CharField(max_length=COMMENT_MAX, widget=forms.Textarea(attrs={"rows": 2}))
    parent = forms.IntegerField(required=False, widget=forms.HiddenInput)

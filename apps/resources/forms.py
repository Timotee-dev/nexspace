from django import forms

from apps.academics.models import AcademicSession, Course, Semester

from .models import Resource


class ResourceUploadForm(forms.Form):
    course = forms.ModelChoiceField(queryset=Course.objects.none(), empty_label="Choose a course")
    title = forms.CharField(max_length=150, help_text="e.g. CSC 301 Exam 2023/2024")
    resource_type = forms.ChoiceField(choices=Resource.Type.choices, label="Type")
    exam_type = forms.ChoiceField(choices=[("", "Not applicable"), *Resource.ExamType.choices], required=False,
                                  label="Exam type (past questions)")
    session = forms.ModelChoiceField(queryset=AcademicSession.objects.none(), required=False,
                                     empty_label="Unknown", label="Academic session")
    semester = forms.TypedChoiceField(choices=[("", "Course default"), *Semester.choices], coerce=int,
                                      required=False, empty_value=None)
    description = forms.CharField(max_length=1000, required=False, widget=forms.Textarea(attrs={"rows": 3}))
    file = forms.FileField(help_text="PDF, Word or PowerPoint")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from django.conf import settings

        self.fields["file"].help_text = f"PDF, Word or PowerPoint, up to {settings.MAX_DOCUMENT_MB} MB"

    def __init__(self, *args, user, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["course"].queryset = Course.objects.filter(department_id=user.department_id, is_active=True)
        uni = user.department.faculty.university_id if user.department_id else None
        self.fields["session"].queryset = AcademicSession.objects.filter(university_id=uni)


class RatingForm(forms.Form):
    stars = forms.TypedChoiceField(choices=[(i, f"{i} star{'s' if i > 1 else ''}") for i in range(5, 0, -1)],
                                   coerce=int, widget=forms.RadioSelect)
    review = forms.CharField(max_length=500, required=False, widget=forms.Textarea(attrs={"rows": 2}),
                             label="Review (optional)")

from django import forms

from .models import Event, Member, Responsibility, Project, Task, EventFeedback, LearningResource, JoinApplication, BudgetEntry


class EventForm(forms.ModelForm):
    capacity = forms.IntegerField(required=False, min_value=1)
    date = forms.DateTimeField(
        input_formats=["%Y-%m-%dT%H:%M"],
        widget=forms.DateTimeInput(
            format="%Y-%m-%dT%H:%M",
            attrs={"type": "datetime-local"},
        ),
    )

    class Meta:
        model = Event
        fields = ("title", "date", "place", "description", "capacity", "registration_open")


class AnnouncementForm(forms.ModelForm):
    class Meta:
        from .models import Announcement
        model = Announcement
        fields = ("title", "message", "event", "published")
        widgets = {"message": forms.Textarea(attrs={"rows": 5})}


class MemberPermissionsForm(forms.ModelForm):
    class Meta:
        model = Member
        fields = (
            "can_manage_members",
            "can_manage_events",
            "can_manage_guests",
            "can_scan",
            "can_manage_announcements",
            "can_manage_permissions",
            "can_access_events",
            "can_access_projects",
            "can_access_calendar",
            "can_access_directory",
            "can_access_messages",
            "can_access_resources",
            "can_access_notifications",
        )
        labels = {
            "can_manage_members": "Manage members",
            "can_manage_events": "Create and edit events and view reports",
            "can_manage_guests": "Review guest requests",
            "can_scan": "Use the check-in scanner",
            "can_manage_announcements": "Create, edit, and delete announcements",
            "can_manage_permissions": "Assign app permissions to members",
            "can_access_events": "View events and RSVP",
            "can_access_projects": "Use projects and tasks",
            "can_access_calendar": "View the calendar",
            "can_access_directory": "View the member directory",
            "can_access_messages": "Send and read messages",
            "can_access_resources": "Use the learning library",
            "can_access_notifications": "View notifications",
        }

class ResponsibilityForm(forms.ModelForm):
    members = forms.ModelMultipleChoiceField(
        queryset=Member.objects.none(),
        required=False,
        label="Assigned members",
        widget=forms.CheckboxSelectMultiple,
    )

    class Meta:
        model = Responsibility
        fields = ("name", "description", "members")
        widgets = {"description": forms.Textarea(attrs={"rows": 3})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["members"].queryset = Member.objects.select_related("user").order_by(
            "user__first_name", "user__last_name", "user__username"
        )


class ProjectForm(forms.ModelForm):
    class Meta:
        model = Project
        fields = ("title", "description", "objectives", "leader", "members", "status", "start_date", "due_date", "public")
        widgets = {"start_date": forms.DateInput(attrs={"type": "date"}), "due_date": forms.DateInput(attrs={"type": "date"}), "members": forms.CheckboxSelectMultiple}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        members = Member.objects.select_related("user").order_by("user__first_name", "user__last_name")
        self.fields["leader"].queryset = members
        self.fields["members"].queryset = members


class TaskForm(forms.ModelForm):
    class Meta:
        model = Task
        fields = ("title", "description", "assignees", "priority", "status", "due_date")
        widgets = {"due_date": forms.DateInput(attrs={"type": "date"}), "assignees": forms.CheckboxSelectMultiple}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["assignees"].queryset = Member.objects.select_related("user").order_by("user__first_name", "user__last_name")


class EventFeedbackForm(forms.ModelForm):
    class Meta:
        model = EventFeedback
        fields = ("rating", "comments")
        widgets = {"comments": forms.Textarea(attrs={"rows": 4})}


class LearningResourceForm(forms.ModelForm):
    class Meta:
        model = LearningResource
        fields = ("title", "description", "topic", "url", "file")
        labels = {"url": "Resource link", "file": "Upload a file"}
        help_texts = {
            "url": "Optional when you upload a file.",
            "file": "Optional when you add a link. PDF, PPT, or PPTX up to 15 MB.",
        }
        widgets = {"description": forms.Textarea(attrs={"rows": 3})}

    def clean_file(self):
        upload = self.cleaned_data.get("file")
        if not upload:
            return upload
        if upload.size > 15 * 1024 * 1024:
            raise forms.ValidationError("Files must be 15 MB or smaller.")
        extension = upload.name.rsplit(".", 1)[-1].lower() if "." in upload.name else ""
        signature = upload.read(8)
        upload.seek(0)
        valid = (
            (extension == "pdf" and signature.startswith(b"%PDF-"))
            or (extension == "pptx" and signature.startswith(b"PK\x03\x04"))
            or (extension == "ppt" and signature.startswith(bytes.fromhex("D0CF11E0A1B11AE1")))
        )
        if not valid:
            raise forms.ValidationError("Upload a valid PDF, PowerPoint presentation, or PPTX file.")
        return upload

    def clean(self):
        cleaned = super().clean()
        if not cleaned.get("url") and not cleaned.get("file"):
            raise forms.ValidationError("Add a web link or upload a PDF or slide deck.")
        return cleaned


class JoinApplicationForm(forms.ModelForm):
    class Meta:
        model = JoinApplication
        fields = ("first_name", "last_name", "email", "phone", "department", "motivation")
        widgets = {"motivation": forms.Textarea(attrs={"rows": 5})}


class BudgetEntryForm(forms.ModelForm):
    class Meta:
        model = BudgetEntry
        fields = ("entry_type", "category", "description", "amount", "date")
        widgets = {"date": forms.DateInput(attrs={"type": "date"})}

    def clean_amount(self):
        amount = self.cleaned_data["amount"]
        if amount <= 0:
            raise forms.ValidationError("Enter an amount greater than zero.")
        return amount

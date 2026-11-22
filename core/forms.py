from django import forms

from .models import Event, Member


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
        )
        labels = {
            "can_manage_members": "Manage members",
            "can_manage_events": "Create and edit events and view reports",
            "can_manage_guests": "Review guest requests",
            "can_scan": "Use the check-in scanner",
            "can_manage_announcements": "Create, edit, and delete announcements",
            "can_manage_permissions": "Assign app permissions to members",
        }

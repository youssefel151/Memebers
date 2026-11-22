import uuid
from django.db import models
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError


class Member(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE)
    phone = models.CharField(max_length=30, blank=True)
    qr_token = models.UUIDField(default=uuid.uuid4, unique=True)
    interests = models.CharField(max_length=300, blank=True)
    location = models.CharField(max_length=120, blank=True)
    directory_visible = models.BooleanField(default=True)
    can_manage_members = models.BooleanField(default=False)
    can_manage_events = models.BooleanField(default=False)
    can_manage_guests = models.BooleanField(default=False)
    can_scan = models.BooleanField(default=False)
    can_manage_announcements = models.BooleanField(default=False)
    can_manage_permissions = models.BooleanField(default=False)

    def __str__(self):
        return self.user.get_full_name() or self.user.username


class Event(models.Model):
    title = models.CharField(max_length=200)
    date = models.DateTimeField()
    place = models.CharField(max_length=200, blank=True)
    registration_open = models.BooleanField(default=False)
    description = models.TextField(blank=True)
    capacity = models.PositiveIntegerField(null=True, blank=True)

    def clean(self):
        if self.capacity is not None and self.capacity < 1:
            raise ValidationError({"capacity": "Enter a capacity of at least 1, or leave it blank for unlimited capacity."})
        if self.pk and self.capacity is not None:
            approved_guests = self.guests.filter(status="approved").count()
            member_rsvps = self.member_registrations.count()
            if self.capacity < approved_guests + member_rsvps:
                raise ValidationError({"capacity": "Capacity cannot be lower than the current number of approved attendees."})

    def __str__(self):
        return self.title


class Attendance(models.Model):
    member = models.ForeignKey(Member, on_delete=models.CASCADE)
    event = models.ForeignKey(Event, on_delete=models.CASCADE)
    scanned_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("member", "event")


class EventRegistration(models.Model):
    member = models.ForeignKey(Member, on_delete=models.CASCADE, related_name="event_registrations")
    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name="member_registrations")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=("member", "event"), name="unique_member_event_registration")]
        ordering = ["created_at"]


class Announcement(models.Model):
    title = models.CharField(max_length=200)
    message = models.TextField()
    event = models.ForeignKey(Event, on_delete=models.SET_NULL, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    published = models.BooleanField(default=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.title


class Message(models.Model):
    sender = models.ForeignKey(User, on_delete=models.CASCADE, related_name="sent_messages")
    recipient = models.ForeignKey(User, on_delete=models.CASCADE, related_name="received_messages")
    body = models.TextField(max_length=2000)
    created_at = models.DateTimeField(auto_now_add=True)
    is_read = models.BooleanField(default=False)

    class Meta:
        ordering = ["created_at"]

    def __str__(self):
        return f"{self.sender} -> {self.recipient}"


class Guest(models.Model):
    STATUS_CHOICES = [
        ("pending", "Pending"),
        ("approved", "Approved"),
        ("rejected", "Rejected"),
    ]

    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name="guests")
    name = models.CharField(max_length=200)
    phone = models.CharField(max_length=30, blank=True)
    email = models.EmailField(blank=True)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default="pending")
    qr_token = models.UUIDField(default=uuid.uuid4, unique=True)
    checked_in_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.name} ({self.event.title})"

import uuid
from django.db import models
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.core.validators import FileExtensionValidator


class Member(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE)
    phone = models.CharField(max_length=30, blank=True)
    qr_token = models.UUIDField(default=uuid.uuid4, unique=True)
    interests = models.CharField(max_length=300, blank=True)
    location = models.CharField(max_length=120, blank=True)
    directory_visible = models.BooleanField(default=True)
    public_team_visible = models.BooleanField(default=True)
    can_manage_members = models.BooleanField(default=False)
    can_manage_events = models.BooleanField(default=False)
    can_manage_guests = models.BooleanField(default=False)
    can_scan = models.BooleanField(default=False)
    can_manage_announcements = models.BooleanField(default=False)
    can_manage_permissions = models.BooleanField(default=False)
    can_access_events = models.BooleanField(default=True)
    can_access_projects = models.BooleanField(default=True)
    can_access_calendar = models.BooleanField(default=True)
    can_access_directory = models.BooleanField(default=True)
    can_access_messages = models.BooleanField(default=True)
    can_access_resources = models.BooleanField(default=True)
    can_access_notifications = models.BooleanField(default=True)

    def __str__(self):
        return self.user.get_full_name() or self.user.username


class Responsibility(models.Model):
    name = models.CharField(max_length=120, unique=True)
    description = models.TextField(blank=True)
    members = models.ManyToManyField(Member, related_name="responsibilities", blank=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


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


class Project(models.Model):
    STATUS_CHOICES = [("planned", "Planned"), ("active", "In progress"), ("on_hold", "On hold"), ("completed", "Completed")]
    title = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    objectives = models.TextField(blank=True)
    leader = models.ForeignKey(Member, on_delete=models.SET_NULL, null=True, blank=True, related_name="led_projects")
    members = models.ManyToManyField(Member, blank=True, related_name="projects")
    status = models.CharField(max_length=12, choices=STATUS_CHOICES, default="planned")
    start_date = models.DateField(null=True, blank=True)
    due_date = models.DateField(null=True, blank=True)
    public = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["due_date", "title"]

    def __str__(self):
        return self.title


class Task(models.Model):
    PRIORITY_CHOICES = [("low", "Low"), ("normal", "Normal"), ("high", "High")]
    STATUS_CHOICES = [("todo", "To do"), ("active", "In progress"), ("done", "Done")]
    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="tasks")
    title = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    assignees = models.ManyToManyField(Member, blank=True, related_name="assigned_tasks")
    priority = models.CharField(max_length=8, choices=PRIORITY_CHOICES, default="normal")
    status = models.CharField(max_length=8, choices=STATUS_CHOICES, default="todo")
    due_date = models.DateField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["status", "due_date", "title"]

    def __str__(self):
        return self.title


class EventFeedback(models.Model):
    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name="feedback")
    member = models.ForeignKey(Member, on_delete=models.CASCADE, related_name="event_feedback")
    rating = models.PositiveSmallIntegerField(choices=[(i, str(i)) for i in range(1, 6)])
    comments = models.TextField(max_length=2000, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=("event", "member"), name="unique_member_event_feedback")]
        ordering = ["-created_at"]


class LearningResource(models.Model):
    title = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    topic = models.CharField(max_length=100, blank=True)
    url = models.URLField(max_length=500, blank=True)
    file = models.FileField(upload_to="resources/%Y/%m/", blank=True, validators=[FileExtensionValidator(["pdf", "ppt", "pptx"])])
    contributor = models.ForeignKey(Member, on_delete=models.SET_NULL, null=True, blank=True, related_name="resources")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["topic", "title"]

    def __str__(self):
        return self.title


class JoinApplication(models.Model):
    STATUS_CHOICES = [("pending", "Pending"), ("accepted", "Accepted"), ("rejected", "Rejected")]
    first_name = models.CharField(max_length=100)
    last_name = models.CharField(max_length=100, blank=True)
    email = models.EmailField()
    phone = models.CharField(max_length=30, blank=True)
    department = models.CharField(max_length=150, blank=True)
    motivation = models.TextField(max_length=3000)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default="pending")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.first_name} {self.last_name} ({self.email})"


class Notification(models.Model):
    recipient = models.ForeignKey(User, on_delete=models.CASCADE, related_name="notifications")
    title = models.CharField(max_length=200)
    message = models.CharField(max_length=500, blank=True)
    url = models.CharField(max_length=500, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    read_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.title} → {self.recipient}"


class Partner(models.Model):
    name = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    website = models.URLField(max_length=500, blank=True)
    logo_url = models.URLField(max_length=500, blank=True)
    active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class GalleryItem(models.Model):
    MEDIA_CHOICES = [("image", "Image"), ("video", "Video")]
    title = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    media_type = models.CharField(max_length=5, choices=MEDIA_CHOICES, default="image")
    media_url = models.URLField(max_length=500)
    published = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.title


class BudgetEntry(models.Model):
    ENTRY_CHOICES = [("income", "Income"), ("expense", "Expense")]
    entry_type = models.CharField(max_length=8, choices=ENTRY_CHOICES)
    category = models.CharField(max_length=100)
    description = models.CharField(max_length=250, blank=True)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    date = models.DateField()
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name="budget_entries")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-date", "-created_at"]

    def __str__(self):
        return f"{self.get_entry_type_display()}: {self.category} ({self.amount})"

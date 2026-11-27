from django.contrib import admin
from django.utils.html import format_html
from .models import Member, Event, Attendance, Announcement, Message, Guest, Responsibility, Project, Task, EventFeedback, LearningResource, JoinApplication, Notification, Partner, GalleryItem, BudgetEntry
from .admin_site import admin_site


@admin.action(description="Reject selected guests")
def reject_guests(modeladmin, request, queryset):
    queryset.update(status="rejected")


@admin.register(Guest, site=admin_site)
class GuestAdmin(admin.ModelAdmin):
    list_display = ("name", "event", "email", "phone", "status", "checked_in_at", "created_at")
    list_filter = ("status", "event")
    search_fields = ("name", "email", "phone")
    readonly_fields = ("status", "checked_in_at")
    actions = [reject_guests]


admin_site.register(Member)
admin_site.register(Attendance)
admin_site.register(Announcement)
admin_site.register(Message)


@admin.register(Event, site=admin_site)
class EventAdmin(admin.ModelAdmin):
    list_display = ("title", "date", "place", "registration_open", "public_registration_link")
    list_editable = ("registration_open",)
    list_filter = ("registration_open", "date")
    search_fields = ("title", "place")

    @admin.display(description="Public registration link")
    def public_registration_link(self, obj):
        if not obj.registration_open:
            return "Open registration to share"
        return format_html(
            '<a href="/register/?event={}" >Open share link </a>',
            obj.pk,
        )


@admin.register(Responsibility, site=admin_site)
class ResponsibilityAdmin(admin.ModelAdmin):
    list_display = ("name",)
    search_fields = ("name", "description", "members__user__first_name", "members__user__last_name")
    filter_horizontal = ("members",)


for model in (Project, Task, EventFeedback, LearningResource, JoinApplication, Notification, Partner, GalleryItem, BudgetEntry):
    admin_site.register(model)

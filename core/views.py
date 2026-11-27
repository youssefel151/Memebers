import io
import json
from urllib.parse import quote
import secrets
import csv

import qrcode
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.contrib import messages
from django.core.exceptions import ValidationError, PermissionDenied
from django.core.validators import validate_email
from django.core.mail import send_mail
from django.conf import settings
from django.db import transaction
from django.db.models import Q, Sum, Prefetch
from django.core.paginator import Paginator
from django.http import HttpResponse, Http404, JsonResponse, FileResponse
from django.shortcuts import render, redirect, get_object_or_404
from django.utils import timezone
from django.views.decorators.http import require_POST
from django.views.decorators.cache import never_cache
from .models import Member, Event, Attendance, Announcement, Message, Guest, EventRegistration, Responsibility, Project, Task, EventFeedback, LearningResource, JoinApplication, Notification, Partner, GalleryItem, BudgetEntry
from .forms import EventForm, AnnouncementForm, MemberPermissionsForm, ResponsibilityForm, ProjectForm, TaskForm, EventFeedbackForm, LearningResourceForm, JoinApplicationForm, BudgetEntryForm
from .permissions import capability_required, has_capability


def _qr_png(text):
    img = qrcode.make(str(text))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return HttpResponse(buf.getvalue(), content_type="image/png")


def _event_is_full(event, exclude_guest_id=None):
    if not event.capacity:
        return False
    approved_guests = event.guests.filter(status="approved")
    if exclude_guest_id:
        approved_guests = approved_guests.exclude(pk=exclude_guest_id)
    occupied = event.member_registrations.count() + approved_guests.count()
    return occupied >= event.capacity


def _safe_csv_cell(value):
    if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@", "\t", "\r")):
        return "'" + value
    return value


def _can_manage_projects(user):
    return has_capability(user, "can_manage_members") or has_capability(user, "can_manage_events")


def landing(request):
    now = timezone.now()
    upcoming = Event.objects.filter(registration_open=True, date__gte=now).order_by("date")[:3]
    return render(request, "core/landing.html", {"upcoming": upcoming, "member_count": Member.objects.count(), "upcoming_count": Event.objects.filter(date__gte=now).count(), "open_registration_count": Event.objects.filter(date__gte=now, registration_open=True).count()})


def member_qr(request, token):
    if not Member.objects.filter(qr_token=token).exists():
        raise Http404
    return _qr_png(token)


def guest_qr(request, token):
    if not Guest.objects.filter(qr_token=token, status="approved").exists():
        raise Http404
    return _qr_png(token)


def invite(request, token):
    guest = get_object_or_404(Guest.objects.select_related("event"), qr_token=token)
    return render(request, "core/invite.html", {"guest": guest})


def register(request):
    events = Event.objects.filter(
        registration_open=True, date__gte=timezone.now()
    ).order_by("date")
    error = ""
    done = request.GET.get("done") == "1"
    selected_event = request.GET.get("event", "")
    if not events.filter(id=selected_event).exists():
        selected_event = ""
    if not selected_event and events.count() == 1:
        selected_event = str(events.first().id)
    values = {"name": "", "email": "", "phone": "", "event": selected_event}

    if request.method == "POST":
        if request.POST.get("website"):
            return redirect("/register/?done=1")

        name = request.POST.get("name", "").strip()[:200]
        email = request.POST.get("email", "").strip().lower()
        phone = request.POST.get("phone", "").strip()[:30]
        values = {
            "name": name,
            "email": email,
            "phone": phone,
            "event": request.POST.get("event", ""),
        }

        try:
            event = events.get(id=request.POST.get("event"))
        except (Event.DoesNotExist, ValueError, TypeError):
            event = None

        try:
            validate_email(email)
            email_ok = True
        except ValidationError:
            email_ok = False

        if not name:
            error = "Please enter your name."
        elif not email_ok:
            error = "Please enter a valid email address."
        elif event is None:
            error = "Please choose an event that is open for registration."
        else:
            with transaction.atomic():
                event = Event.objects.select_for_update().filter(
                    pk=event.pk, registration_open=True, date__gte=timezone.now()
                ).first()
                if event is None:
                    error = "Please choose an event that is open for registration."
                elif _event_is_full(event):
                    error = "This event has reached capacity."
                elif Guest.objects.filter(event=event, email__iexact=email).exists():
                    error = "This email is already registered for this event."
                else:
                    Guest.objects.create(
                        event=event, name=name, email=email, phone=phone, status="pending"
                    )
            if not error:
                return redirect("/register/?done=1")

    return render(
        request,
        "core/register.html",
        {
            "events": events,
            "error": error,
            "values": values,
            "done": done,
            "selected_event": events.filter(id=values["event"]).first() if values["event"] else None,
            "event_picker": not (events.filter(id=values["event"]).exists() if values["event"] else False) and events.count() > 1,
        },
    )


@capability_required("can_manage_members")
def members_list(request):
    if request.method == "POST":
        if request.POST.get("action") == "team_visibility":
            member = get_object_or_404(Member.objects.select_related("user"), pk=request.POST.get("member_id"))
            if member.user.is_superuser:
                messages.error(request, "Public team visibility cannot be changed for a superuser here.")
            else:
                member.public_team_visible = "public_team_visible" in request.POST
                member.save(update_fields=["public_team_visible"])
                messages.success(request, f"Public team visibility updated for {member}.")
            return redirect("members")
        if not has_capability(request.user, "can_manage_permissions"):
            raise PermissionDenied
        member = get_object_or_404(Member.objects.select_related("user"), pk=request.POST.get("member_id"))
        if member.user.is_superuser or member.user_id == request.user.id:
            messages.error(request, "You cannot change your own app permissions or a superuser's access here.")
        else:
            form = MemberPermissionsForm(request.POST, instance=member)
            if form.is_valid():
                form.save()
                messages.success(request, f"App permissions updated for {member}.")
            else:
                messages.error(request, "Please review the permission settings and try again.")
        return redirect("members")
    members = Member.objects.select_related("user").prefetch_related("responsibilities").order_by("user__first_name", "user__last_name")
    query = request.GET.get("q", "").strip()
    if query:
        members = members.filter(Q(user__first_name__icontains=query) | Q(user__last_name__icontains=query) | Q(user__email__icontains=query))
    page = Paginator(members, 25).get_page(request.GET.get("page"))
    for member in page:
        member.permissions_form = MemberPermissionsForm(instance=member)
    return render(request, "core/members.html", {"members": page, "query": query})


@capability_required("can_manage_permissions")
def responsibilities_list(request):
    create_form = ResponsibilityForm(prefix="new")
    bound_edit = None
    bound_edit_id = None

    if request.method == "POST":
        action = request.POST.get("action")
        if action == "create":
            create_form = ResponsibilityForm(request.POST, prefix="new")
            if create_form.is_valid():
                responsibility = create_form.save()
                messages.success(request, f"Responsibility '{responsibility.name}' was added.")
                return redirect("responsibilities")
            messages.error(request, "Please correct the responsibility details below.")
        elif action == "update":
            responsibility = get_object_or_404(Responsibility, pk=request.POST.get("responsibility_id"))
            bound_edit = ResponsibilityForm(
                request.POST, instance=responsibility, prefix=f"r{responsibility.pk}"
            )
            bound_edit_id = responsibility.pk
            if bound_edit.is_valid():
                bound_edit.save()
                messages.success(request, f"Responsibility '{responsibility.name}' was updated.")
                return redirect("responsibilities")
            messages.error(request, "Please correct the responsibility details below.")

    rows = []
    for responsibility in Responsibility.objects.prefetch_related("members__user"):
        form = bound_edit if responsibility.pk == bound_edit_id else ResponsibilityForm(
            instance=responsibility, prefix=f"r{responsibility.pk}"
        )
        rows.append({"responsibility": responsibility, "form": form})
    return render(request, "core/responsibilities.html", {
        "create_form": create_form,
        "responsibility_rows": rows,
    })


@capability_required("can_manage_permissions")
def responsibility_delete(request, responsibility_id):
    responsibility = get_object_or_404(Responsibility, pk=responsibility_id)
    if request.method == "POST":
        name = responsibility.name
        responsibility.delete()
        messages.success(request, f"Responsibility '{name}' was deleted.")
        return redirect("responsibilities")
    return render(request, "core/confirm_delete.html", {
        "title": "Delete responsibility",
        "object_name": responsibility.name,
        "warning": "This removes this responsibility and its assignments from all members.",
        "cancel_url": "/responsibilities/",
    })


@capability_required("can_manage_members")
def member_remove(request, member_id):
    member = get_object_or_404(Member.objects.select_related("user"), pk=member_id)
    if member.user.is_superuser or member.user_id == request.user.id:
        messages.error(request, "You cannot remove your own account or a superuser here.")
        return redirect("members")
    if request.method == "POST":
        name = str(member)
        member.user.delete()
        messages.success(request, f"{name} and their sign-in account were removed.")
        return redirect("members")
    return render(request, "core/confirm_delete.html", {
        "title": "Remove member",
        "object_name": str(member),
        "warning": "This permanently removes the member account, messages, RSVPs, and attendance records.",
        "cancel_url": "/members/",
    })


@capability_required("can_manage_guests")
def guests_list(request):
    if request.method == "POST":
        guest = get_object_or_404(Guest, id=request.POST.get("guest_id"))
        action = request.POST.get("action")
        if action == "approve":
            with transaction.atomic():
                event = Event.objects.select_for_update().get(pk=guest.event_id)
                guest = Guest.objects.select_for_update().get(pk=guest.pk)
                if _event_is_full(event, exclude_guest_id=guest.pk):
                    messages.error(request, f"{event.title} is at capacity; {guest.name} was not approved.")
                    return redirect("guests")
                guest.status = "approved"
                guest.save(update_fields=["status"])
            sent = False
            if guest.email:
                link = request.build_absolute_uri(f"/invite/{guest.qr_token}/")
                body = f"Hello {guest.name},\n\nYour request for {guest.event.title} has been approved. Event time: {timezone.localtime(guest.event.date):%d/%m/%Y %H:%M}.\nYour invitation and QR pass: {link}\n\nPlease show the QR at the entrance."
                sent = send_mail(f"Your invitation: {guest.event.title}", body, None, [guest.email], fail_silently=True)
            mail_result = (" Invitation email was written to the application console; configure SMTP to deliver it." if sent and "console.EmailBackend" in settings.EMAIL_BACKEND else " Invitation email sent." if sent else " Email could not be sent; use the invitation link below." if guest.email else "")
            messages.success(request, f"{guest.name} is approved." + mail_result)
        elif action == "reject":
            guest.status = "rejected"
            guest.save(update_fields=["status"])
            messages.success(request, f"{guest.name} was declined.")
        else:
            messages.error(request, "Choose approve or decline to update this guest.")
        return redirect("guests")

    guests = Guest.objects.select_related("event").order_by("-event__date", "status", "name")
    status_filter = request.GET.get("status", "")
    event_filter = request.GET.get("event", "")
    query = request.GET.get("q", "").strip()
    if status_filter in {"pending", "approved", "rejected"}:
        guests = guests.filter(status=status_filter)
    if event_filter.isdigit():
        guests = guests.filter(event_id=int(event_filter))
    if query:
        guests = guests.filter(Q(name__icontains=query) | Q(email__icontains=query) | Q(phone__icontains=query))
    event_groups = []
    matching_events = Event.objects.filter(guests__in=guests).distinct().order_by("date")
    for event in matching_events:
        event_guests = guests.filter(event=event)
        page = Paginator(event_guests, 25).get_page(request.GET.get(f"page_{event.id}"))
        rows = []
        for guest in page:
            link = request.build_absolute_uri(f"/invite/{guest.qr_token}/") if guest.status == "approved" else ""
            mailto = ""
            if link and guest.email:
                subject = f"Your invitation: {guest.event.title}"
                body = (
                    f"Hello {guest.name},\n\nYour registration for {guest.event.title} on "
                    f"{timezone.localtime(guest.event.date):%d/%m/%Y %H:%M} has been approved.\n\n"
                    f"Your personal invitation and QR code are here:\n{link}\n\n"
                    "Please show the QR code at the entrance.\n"
                )
                mailto = f"mailto:{guest.email}?subject={quote(subject)}&body={quote(body)}"
            rows.append({"guest": guest, "link": link, "mailto": mailto})
        event_groups.append({
            "event": event,
            "rows": rows,
            "page": page,
            "total": event_guests.count(),
            "approved": event_guests.filter(status="approved").count(),
            "checked_in": event_guests.filter(checked_in_at__isnull=False).count(),
        })
    return render(request, "core/guests.html", {"event_groups": event_groups, "status_filter": status_filter, "event_filter": event_filter, "query": query, "events": Event.objects.order_by("date")})


@capability_required("can_manage_events")
def event_management(request):
    if request.method == "POST":
        event = get_object_or_404(Event, id=request.POST.get("event_id"))
        if request.POST.get("action") == "toggle_registration":
            event.registration_open = not event.registration_open
            event.save(update_fields=["registration_open"])
            state = "open" if event.registration_open else "paused"
            _notify_members(f"Registration {state}: {event.title}", f"Registration has been {state} for this event.", f"/events/{event.pk}/")
            messages.success(request, f"Public registration is {state} for {event.title}.")
        return redirect("events")

    events = Event.objects.order_by("date")
    event_rows = [
        {
            "event": event,
            "share_url": request.build_absolute_uri(f"/register/?event={event.id}"),
        }
        for event in events
    ]
    return render(request, "core/events.html", {"event_rows": event_rows})


@capability_required("can_manage_events")
def event_edit(request, event_id=None):
    event = get_object_or_404(Event, id=event_id) if event_id else None
    if request.method == "POST":
        form = EventForm(request.POST, instance=event)
        if form.is_valid():
            is_new = event is None
            changed = bool(form.changed_data)
            saved = form.save()
            if is_new:
                _notify_members(f"New event: {saved.title}", "A new club event is on the calendar.", f"/events/{saved.pk}/")
            elif changed:
                _notify_members(f"Event updated: {saved.title}", "Event details have changed. Check the latest information.", f"/events/{saved.pk}/")
            messages.success(request, f"{saved.title} has been saved.")
            return redirect("events")
    else:
        form = EventForm(instance=event)
    return render(request, "core/event_form.html", {"form": form, "event": event})


@capability_required("can_manage_events")
def event_delete(request, event_id):
    event = get_object_or_404(Event, pk=event_id)
    if request.method == "POST":
        title = event.title
        event.delete()
        messages.success(request, f"Event '{title}' and its registrations were deleted.")
        return redirect("events")
    return render(request, "core/confirm_delete.html", {
        "title": "Delete event",
        "object_name": event.title,
        "warning": "This permanently removes guest requests, member RSVPs, attendance records, and event check-in data.",
        "cancel_url": "/events/",
    })


def event_detail(request, event_id):
    if request.user.is_authenticated and not has_capability(request.user, "can_access_events"):
        raise PermissionDenied
    event = get_object_or_404(Event, id=event_id)
    member = Member.objects.filter(user=request.user).first() if request.user.is_authenticated else None
    registered = bool(member and EventRegistration.objects.filter(member=member, event=event).exists())
    rsvp_count = event.member_registrations.count()
    guest_count = event.guests.filter(status="approved").count()
    full = bool(event.capacity and rsvp_count + guest_count >= event.capacity)
    can_feedback = bool(member and timezone.localtime(event.date).date() <= timezone.localdate() and Attendance.objects.filter(event=event, member=member).exists())
    has_feedback = bool(member and EventFeedback.objects.filter(event=event, member=member).exists())
    return render(request, "core/event_detail.html", {"event": event, "member": member, "registered": registered, "is_open": event.registration_open and event.date >= timezone.now(), "full": full, "rsvp_count": rsvp_count, "guest_count": guest_count, "can_feedback": can_feedback, "has_feedback": has_feedback})


@capability_required("can_access_events")
@require_POST
def event_rsvp(request, event_id):
    member = get_object_or_404(Member, user=request.user)
    with transaction.atomic():
        event = get_object_or_404(Event.objects.select_for_update(), id=event_id)
        existing = EventRegistration.objects.filter(member=member, event=event)
        if existing.exists():
            existing.delete()
            messages.success(request, f"Your RSVP for {event.title} was cancelled.")
        elif event.date < timezone.now() or not event.registration_open:
            messages.error(request, "Registration is closed for this event.")
        elif _event_is_full(event):
            messages.error(request, "This event has reached capacity.")
        else:
            EventRegistration.objects.create(member=member, event=event)
            Notification.objects.create(recipient=request.user, title=f"RSVP confirmed: {event.title}", message=f"You are registered for {event.title}.", url=f"/events/{event.pk}/")
            messages.success(request, f"You are registered for {event.title}.")
    return redirect("event_detail", event_id=event.id)


@capability_required("can_manage_events", "can_scan")
def event_report(request, event_id):
    event = get_object_or_404(Event, id=event_id)
    if request.GET.get("format") == "csv":
        response = HttpResponse(content_type="text/csv; charset=utf-8")
        response["Content-Disposition"] = f'attachment; filename="event-{event.id}-attendees.csv"'
        writer = csv.writer(response)
        writer.writerow(["Name", "Email", "Type", "Registration", "Checked in at"])
        member_ids = set(event.member_registrations.values_list("member_id", flat=True)) | set(Attendance.objects.filter(event=event).values_list("member_id", flat=True))
        for member in Member.objects.filter(id__in=member_ids).select_related("user"):
            attendance = Attendance.objects.filter(member=member, event=event).first()
            registered = EventRegistration.objects.filter(member=member, event=event).exists()
            writer.writerow([_safe_csv_cell(member.user.get_full_name()), _safe_csv_cell(member.user.email), "Member", "RSVP" if registered else "Walk-in", attendance.scanned_at.isoformat() if attendance else ""])
        for guest in event.guests.all():
            writer.writerow([_safe_csv_cell(guest.name), _safe_csv_cell(guest.email), "Guest", guest.get_status_display(), guest.checked_in_at.isoformat() if guest.checked_in_at else ""])
        return response
    member_ids = set(event.member_registrations.values_list("member_id", flat=True)) | set(Attendance.objects.filter(event=event).values_list("member_id", flat=True))
    members = Member.objects.filter(id__in=member_ids).select_related("user").order_by("user__last_name", "user__first_name")
    attendance_ids = set(Attendance.objects.filter(event=event).values_list("member_id", flat=True))
    registered_ids = set(event.member_registrations.values_list("member_id", flat=True))
    return render(request, "core/event_report.html", {"event": event, "members": members, "attendance_ids": attendance_ids, "registered_ids": registered_ids, "guests": event.guests.all(), "checked_in": Attendance.objects.filter(event=event).count() + event.guests.filter(checked_in_at__isnull=False).count()})


@capability_required("can_manage_announcements")
def announcement_list(request):
    announcements = Announcement.objects.select_related("event").order_by("-created_at")
    return render(request, "core/announcements.html", {"announcements": announcements})


@capability_required("can_manage_announcements")
def announcement_edit(request, announcement_id=None):
    announcement = get_object_or_404(Announcement, id=announcement_id) if announcement_id else None
    form = AnnouncementForm(request.POST or None, instance=announcement)
    if request.method == "POST" and form.is_valid():
        is_new = announcement is None
        changed = bool(form.changed_data)
        saved = form.save()
        if is_new and saved.published:
            _notify_members(f"New announcement: {saved.title}", saved.message[:250], "/home/")
        elif changed and saved.published:
            _notify_members(f"Announcement updated: {saved.title}", saved.message[:250], "/home/")
        messages.success(request, f"Announcement '{saved.title}' saved.")
        return redirect("announcements")
    return render(request, "core/announcement_form.html", {"form": form, "announcement": announcement})


@capability_required("can_manage_announcements")
def announcement_delete(request, announcement_id):
    announcement = get_object_or_404(Announcement, pk=announcement_id)
    if request.method == "POST":
        title = announcement.title
        announcement.delete()
        messages.success(request, f"Announcement '{title}' was deleted.")
        return redirect("announcements")
    return render(request, "core/confirm_delete.html", {
        "title": "Delete announcement",
        "object_name": announcement.title,
        "warning": "This permanently removes the announcement from member home pages.",
        "cancel_url": "/announcements/",
    })


@capability_required("can_scan")
def scan_page(request):
    events = Event.objects.filter(date__gte=timezone.now()).order_by("date")
    return render(request, "core/scan.html", {"events": events})


@capability_required("can_scan")
@require_POST
def mark_attendance(request):
    try:
        data = json.loads(request.body)
        token = data["token"]
        event = Event.objects.get(id=data["event"])
        member = Member.objects.select_related("user").filter(qr_token=token).first()
        guest = None if member else Guest.objects.filter(qr_token=token).first()
    except (ValueError, TypeError, KeyError, ValidationError, Event.DoesNotExist):
        return JsonResponse(
            {"status": "error", "message": "Unknown QR code or event"}, status=404
        )

    if member:
        name = member.user.get_full_name() or member.user.username
        _, created = Attendance.objects.get_or_create(member=member, event=event)
        registered = EventRegistration.objects.filter(member=member, event=event).exists()
        return JsonResponse({"status": "ok" if created else "already", "name": name, "entry_type": "rsvp" if registered else "walkin"})

    if guest:
        name = f"{guest.name} (guest)"
        if guest.status != "approved":
            return JsonResponse(
                {"status": "error", "message": f"{guest.name} is not approved"},
                status=400,
            )
        if guest.event_id != event.id:
            return JsonResponse(
                {"status": "error", "message": f"{guest.name} is invited to another event"},
                status=400,
            )
        if guest.checked_in_at:
            return JsonResponse({"status": "already", "name": name})
        checked_in = Guest.objects.filter(pk=guest.pk, status="approved", checked_in_at__isnull=True).update(checked_in_at=timezone.now())
        if checked_in:
            return JsonResponse({"status": "ok", "name": name})
        guest.refresh_from_db(fields=["status", "checked_in_at"])
        if guest.status != "approved":
            return JsonResponse({"status": "error", "message": f"{guest.name} is not approved"}, status=400)
        return JsonResponse({"status": "already", "name": name})

    return JsonResponse(
        {"status": "error", "message": "Unknown QR code or event"}, status=404
    )


@login_required
def profile(request):
    member = Member.objects.filter(user=request.user).first()

    if request.method == "POST" and member:
        user = request.user
        user.first_name = request.POST.get("first_name", "").strip()
        user.last_name = request.POST.get("last_name", "").strip()
        user.save()
        member.phone = request.POST.get("phone", "").strip()
        member.directory_visible = request.POST.get("directory_visible") == "on"
        member.save()
        return redirect("/profile/")

    attendances = []
    if member:
        attendances = (
            Attendance.objects.filter(member=member)
            .select_related("event")
            .order_by("-event__date")
        )
    registrations = EventRegistration.objects.filter(member=member, event__date__gte=timezone.now()).select_related("event") if member else []
    responsibilities = member.responsibilities.all() if member else []
    return render(request, "core/profile.html", {"member": member, "attendances": attendances, "registrations": registrations, "responsibilities": responsibilities})


@login_required
def home(request):
    upcoming = Event.objects.filter(date__gte=timezone.now()).order_by("date")[:4]
    announcements = Announcement.objects.filter(published=True).select_related("event")[:20]
    unread = Message.objects.filter(recipient=request.user, is_read=False).count()
    member = Member.objects.filter(user=request.user).first()
    my_tasks = Task.objects.filter(assignees=member, status__in=["todo", "active"]).select_related("project").order_by("due_date", "priority")[:5] if member else Task.objects.none()
    my_projects = Project.objects.filter(Q(members=member) | Q(leader=member)).distinct().order_by("status", "due_date")[:4] if member else Project.objects.none()
    is_manager = _can_manage_projects(request.user)
    admin_summary = {}
    if is_manager:
        admin_summary["upcoming_events"] = Event.objects.filter(date__gte=timezone.now()).count()
    if has_capability(request.user, "can_manage_guests"):
        admin_summary["pending_guests"] = Guest.objects.filter(status="pending").count()
    if has_capability(request.user, "can_manage_members"):
        admin_summary["pending_applications"] = JoinApplication.objects.filter(status="pending").count()
    if has_capability(request.user, "can_manage_announcements"):
        admin_summary["announcement_link"] = "/announcements/"
    return render(
        request,
        "core/home.html",
        {"upcoming": upcoming, "announcements": announcements, "unread": unread, "member": member, "my_tasks": my_tasks, "my_projects": my_projects, "dashboard_admin": bool(admin_summary), "admin_summary": admin_summary},
    )


@login_required
def manage_home(request):
    allowed = any(has_capability(request.user, capability) for capability in (
        "can_manage_members", "can_manage_events", "can_manage_guests", "can_scan",
        "can_manage_announcements", "can_manage_permissions",
    ))
    if not allowed and not request.user.is_staff:
        raise PermissionDenied
    return render(request, "core/manage.html", {
        "upcoming_event_count": Event.objects.filter(date__gte=timezone.now()).count() if has_capability(request.user, "can_manage_events") else 0,
        "pending_guest_count": Guest.objects.filter(status="pending").count() if has_capability(request.user, "can_manage_guests") else 0,
        "pending_application_count": JoinApplication.objects.filter(status="pending").count() if has_capability(request.user, "can_manage_members") else 0,
        "member_count": Member.objects.count() if has_capability(request.user, "can_manage_members") else 0,
    })


@capability_required("can_access_directory")
def directory(request):
    members = (
        Member.objects.select_related("user").prefetch_related("responsibilities")
        .exclude(user=request.user)
        .filter(directory_visible=True)
        .order_by("user__first_name", "user__last_name")
    )
    query = request.GET.get("q", "").strip()
    if query:
        members = members.filter(Q(user__first_name__icontains=query) | Q(user__last_name__icontains=query) | Q(location__icontains=query) | Q(responsibilities__name__icontains=query)).distinct()
    return render(request, "core/directory.html", {"members": Paginator(members, 25).get_page(request.GET.get("page")), "query": query})


@capability_required("can_access_messages")
def inbox(request):
    msgs = (
        Message.objects.filter(Q(sender=request.user) | Q(recipient=request.user))
        .select_related("sender", "recipient")
        .order_by("-created_at")
    )
    seen = {}
    for m in msgs:
        other = m.recipient if m.sender_id == request.user.id else m.sender
        if other.id not in seen:
            seen[other.id] = {"user": other, "last": m, "unread": 0}
        if m.recipient_id == request.user.id and not m.is_read:
            seen[other.id]["unread"] += 1
    return render(request, "core/inbox.html", {"conversations": list(seen.values())})


@capability_required("can_access_messages")
def conversation(request, user_id):
    other = get_object_or_404(User, id=user_id, member__isnull=False)
    if other == request.user:
        return redirect("/messages/")

    if request.method == "POST":
        body = request.POST.get("body", "").strip()
        if body:
            Message.objects.create(sender=request.user, recipient=other, body=body[:2000])
        return redirect(f"/messages/{other.id}/")

    Message.objects.filter(sender=other, recipient=request.user, is_read=False).update(is_read=True)
    thread = Message.objects.filter(
        Q(sender=request.user, recipient=other) | Q(sender=other, recipient=request.user)
    ).order_by("created_at")
    return render(request, "core/conversation.html", {"other": other, "thread": thread})

    

@capability_required("can_manage_members")
def add_member(request):
    error = ""
    created = None
    values = {"first_name": "", "last_name": "", "email": "", "phone": ""}

    if request.method == "POST":
        first = request.POST.get("first_name", "").strip()[:150]
        last = request.POST.get("last_name", "").strip()[:150]
        email = request.POST.get("email", "").strip().lower()
        phone = request.POST.get("phone", "").strip()[:30]
        values = {"first_name": first, "last_name": last, "email": email, "phone": phone}

        try:
            validate_email(email)
            email_ok = True
        except ValidationError:
            email_ok = False

        if not first:
            error = "Please enter a first name."
        elif not email_ok:
            error = "Please enter a valid email address."
        elif User.objects.filter(username__iexact=email).exists():
            error = "A member with this email already exists."
        else:
            user = User.objects.create_user(
                username=email,
                email=email,
                password=secrets.token_urlsafe(32),
                first_name=first,
                last_name=last,
            )
            Member.objects.create(user=user, phone=phone)
            from django.contrib.auth.tokens import default_token_generator
            from django.utils.http import urlsafe_base64_encode
            from django.utils.encoding import force_bytes
            setup_url = request.build_absolute_uri(f"/password/reset/{urlsafe_base64_encode(force_bytes(user.pk))}/{default_token_generator.make_token(user)}/")
            sent = send_mail("Set up your Orionex member account", f"Hello {first},\n\nYour member account is ready. Set your password using this one-time link:\n{setup_url}\n", None, [email], fail_silently=True)
            created = {
                "name": f"{first} {last}".strip(),
                "email": email,
                "sent": sent,
                "setup_url": setup_url,
                "console_email": "console.EmailBackend" in settings.EMAIL_BACKEND,
            }
            values = {"first_name": "", "last_name": "", "email": "", "phone": ""}

    return render(
        request,
        "core/add_member.html",
        {"error": error, "created": created, "values": values},
    )


def _notify_members(title, message, url):
    Notification.objects.bulk_create([
        Notification(recipient_id=user_id, title=title, message=message, url=url)
        for user_id in User.objects.filter(is_active=True, member__isnull=False).values_list("id", flat=True)
    ])


def about(request):
    return render(request, "core/about.html", {"team_roles": Responsibility.objects.prefetch_related("members__user")})


@never_cache
def team(request):
    team_members = Member.objects.filter(public_team_visible=True).select_related("user").prefetch_related("responsibilities").order_by("user__first_name", "user__last_name", "user__username")
    return render(request, "core/team_public.html", {"team_members": team_members})


def partner_list(request):
    return render(request, "core/partners.html", {"partners": Partner.objects.filter(active=True)})


def gallery(request):
    return render(request, "core/gallery.html", {"items": GalleryItem.objects.filter(published=True)})


def public_projects(request):
    visible_members = Member.objects.filter(directory_visible=True).select_related("user")
    projects = Project.objects.filter(public=True, status="completed").prefetch_related(Prefetch("members", queryset=visible_members, to_attr="public_members"), "tasks")
    return render(request, "core/public_projects.html", {"projects": projects})


def activity_list(request):
    if request.user.is_authenticated and not has_capability(request.user, "can_access_events"):
        raise PermissionDenied
    events = Event.objects.order_by("-date")
    return render(request, "core/activities.html", {"events": Paginator(events, 12).get_page(request.GET.get("page")), "today": timezone.now()})


@capability_required("can_access_projects")
def project_list(request):
    project = None
    form = ProjectForm()
    if request.method == "POST":
        if not _can_manage_projects(request.user):
            raise PermissionDenied
        form = ProjectForm(request.POST)
        if form.is_valid():
            project = form.save()
            creator = Member.objects.filter(user=request.user).first()
            if creator:
                project.members.add(creator)
            if not project.members.filter(pk=project.leader_id).exists() and project.leader_id:
                project.members.add(project.leader)
            messages.success(request, f"Project '{project.title}' created.")
            return redirect("project_detail", project_id=project.pk)
    projects = Project.objects.prefetch_related("members__user", "tasks").order_by("status", "due_date")
    member = Member.objects.filter(user=request.user).first()
    if not _can_manage_projects(request.user):
        projects = projects.filter(Q(members=member) | Q(leader=member)).distinct() if member else projects.none()
    return render(request, "core/projects.html", {"projects": projects, "form": form, "can_manage": _can_manage_projects(request.user)})


@capability_required("can_access_projects")
def project_detail(request, project_id):
    project = get_object_or_404(Project.objects.prefetch_related("members__user", "tasks__assignees__user"), pk=project_id)
    member = Member.objects.filter(user=request.user).first()
    can_edit = _can_manage_projects(request.user) or bool(member and project.leader_id == member.pk)
    if not can_edit and not project.members.filter(pk=getattr(member, "pk", None)).exists():
        raise PermissionDenied
    task_form = TaskForm()
    if request.method == "POST":
        if request.POST.get("action") == "status":
            task = get_object_or_404(Task, pk=request.POST.get("task_id"), project=project)
            if not can_edit and not task.assignees.filter(pk=getattr(member, "pk", None)).exists():
                raise PermissionDenied
            if request.POST.get("status") not in dict(Task.STATUS_CHOICES):
                raise Http404
            task.status = request.POST["status"]
            task.save(update_fields=["status"])
            return redirect("project_detail", project_id=project.pk)
        if not can_edit:
            raise PermissionDenied
        task_form = TaskForm(request.POST)
        if task_form.is_valid():
            task = task_form.save(commit=False)
            task.project = project
            task.save()
            task_form.save_m2m()
            project.members.add(*task.assignees.all())
            for assignee in task.assignees.select_related("user"):
                Notification.objects.create(recipient=assignee.user, title=f"New task: {task.title}", message=f"Assigned in {project.title}", url=f"/projects/{project.pk}/")
            messages.success(request, f"Task '{task.title}' added.")
            return redirect("project_detail", project_id=project.pk)
    return render(request, "core/project_detail.html", {"project": project, "task_form": task_form, "can_edit": can_edit, "today": timezone.localdate()})


@capability_required("can_access_projects")
def project_edit(request, project_id):
    project = get_object_or_404(Project, pk=project_id)
    member = Member.objects.filter(user=request.user).first()
    if not (_can_manage_projects(request.user) or (member and project.leader_id == member.pk)):
        raise PermissionDenied
    form = ProjectForm(request.POST or None, instance=project)
    if request.method == "POST" and form.is_valid():
        saved = form.save()
        if saved.leader_id:
            saved.members.add(saved.leader)
        messages.success(request, "Project updated.")
        return redirect("project_detail", project_id=saved.pk)
    return render(request, "core/simple_form.html", {"form": form, "title": "Edit project", "cancel_url": f"/projects/{project.pk}/"})


@capability_required("can_access_projects")
def task_list(request):
    member = Member.objects.filter(user=request.user).first()
    tasks = Task.objects.select_related("project").prefetch_related("assignees__user")
    if not _can_manage_projects(request.user):
        tasks = tasks.filter(assignees=member) if member else tasks.none()
    return render(request, "core/tasks.html", {"tasks": tasks.order_by("status", "due_date"), "today": timezone.localdate()})


@capability_required("can_access_calendar")
def calendar(request):
    view = request.GET.get("view", "month")
    today = timezone.localdate()
    month = today.replace(day=1)
    week_date = today
    try:
        month = month.replace(year=int(request.GET.get("year", month.year)), month=int(request.GET.get("month", month.month)))
    except (ValueError, TypeError):
        pass
    if view == "week":
        try:
            week_date = timezone.datetime.strptime(request.GET.get("date", today.isoformat()), "%Y-%m-%d").date()
        except (ValueError, TypeError):
            week_date = today
        start_date = week_date - timezone.timedelta(days=week_date.weekday())
        end_date = start_date + timezone.timedelta(days=7)
        previous = start_date - timezone.timedelta(days=7)
        next_period = start_date + timezone.timedelta(days=7)
    else:
        next_period = (month.replace(day=28) + timezone.timedelta(days=4)).replace(day=1)
        previous = (month - timezone.timedelta(days=1)).replace(day=1)
        start_date, end_date = month, next_period
    start = timezone.make_aware(timezone.datetime.combine(start_date, timezone.datetime.min.time()))
    end = timezone.make_aware(timezone.datetime.combine(end_date, timezone.datetime.min.time()))
    entries = []
    for event in Event.objects.filter(date__gte=start, date__lt=end).order_by("date"):
        entries.append({"date": timezone.localtime(event.date).date(), "time": timezone.localtime(event.date).strftime("%H:%M"), "title": event.title, "kind": "Event", "url": f"/events/{event.pk}/", "place": event.place})
    member = Member.objects.filter(user=request.user).first()
    projects = Project.objects.filter(due_date__gte=start_date, due_date__lt=end_date).order_by("due_date")
    if not _can_manage_projects(request.user):
        projects = projects.filter(Q(members=member) | Q(leader=member)).distinct() if member else projects.none()
    entries.extend({"date": p.due_date, "time": "", "title": f"{p.title} deadline", "kind": "Project", "url": f"/projects/{p.pk}/", "place": ""} for p in projects)
    entries.sort(key=lambda item: (item["date"], item["time"], item["title"]))
    return render(request, "core/calendar.html", {"month": month, "entries": entries, "previous": previous, "next": next_period, "view": view, "week_start": start_date, "week_end": end_date - timezone.timedelta(days=1)})


@capability_required("can_access_notifications")
def notifications(request):
    member = Member.objects.filter(user=request.user).first()
    today = timezone.localdate()
    now = timezone.now()
    if member:
        upcoming = EventRegistration.objects.filter(member=member, event__date__gte=now, event__date__lte=now + timezone.timedelta(days=1)).select_related("event")
        for registration in upcoming:
            title = f"Event reminder: {registration.event.title}"
            url = f"/events/{registration.event_id}/"
            if not Notification.objects.filter(recipient=request.user, title=title, url=url, created_at__date=today).exists():
                Notification.objects.create(recipient=request.user, title=title, message="Your event is coming up within 24 hours.", url=url)
        due_tasks = Task.objects.filter(assignees=member, status__in=["todo", "active"], due_date__gte=today, due_date__lte=today + timezone.timedelta(days=3)).select_related("project").distinct()
        for task in due_tasks:
            title = f"Task reminder: {task.title}"
            url = f"/projects/{task.project_id}/"
            if not Notification.objects.filter(recipient=request.user, title=title, url=url, created_at__date=today).exists():
                Notification.objects.create(recipient=request.user, title=title, message=f"Due {task.due_date:%d %b %Y} in {task.project.title}.", url=url)
    if request.method == "POST":
        action = request.POST.get("action")
        qs = Notification.objects.filter(recipient=request.user)
        if action == "read_all":
            qs.filter(read_at__isnull=True).update(read_at=timezone.now())
        else:
            qs.filter(pk=request.POST.get("notification_id"), read_at__isnull=True).update(read_at=timezone.now())
        return redirect("notifications")
    return render(request, "core/notifications.html", {"notifications": Notification.objects.filter(recipient=request.user)[:100]})


@capability_required("can_access_events")
def submit_feedback(request, event_id):
    event = get_object_or_404(Event, pk=event_id)
    member = get_object_or_404(Member, user=request.user)
    if event.date.date() > timezone.localdate() or not (Attendance.objects.filter(event=event, member=member).exists() or EventRegistration.objects.filter(event=event, member=member).exists()):
        raise PermissionDenied
    existing = EventFeedback.objects.filter(event=event, member=member).first()
    form = EventFeedbackForm(request.POST or None, instance=existing)
    if request.method == "POST" and form.is_valid():
        feedback = form.save(commit=False)
        feedback.event, feedback.member = event, member
        feedback.save()
        messages.success(request, "Thank you for sharing your feedback.")
        return redirect("event_detail", event_id=event.pk)
    return render(request, "core/simple_form.html", {"form": form, "title": f"Feedback: {event.title}", "cancel_url": f"/events/{event.pk}/"})


@capability_required("can_manage_events")
def feedback_report(request):
    events = Event.objects.filter(feedback__isnull=False).distinct().prefetch_related("feedback__member__user")
    if request.GET.get("format") == "csv":
        response = HttpResponse(content_type="text/csv; charset=utf-8")
        response["Content-Disposition"] = 'attachment; filename="orionex-event-feedback.csv"'
        writer = csv.writer(response)
        writer.writerow(["Event", "Member", "Rating", "Comments", "Submitted at"])
        for event in events:
            for item in event.feedback.all():
                writer.writerow([_safe_csv_cell(event.title), _safe_csv_cell(str(item.member)), item.rating, _safe_csv_cell(item.comments), item.created_at.isoformat()])
        return response
    return render(request, "core/feedback_report.html", {"events": events})


@capability_required("can_access_resources")
def resources(request):
    can_manage_resources = has_capability(request.user, "can_manage_members")
    if request.method != "GET":
        raise PermissionDenied
    items = LearningResource.objects.select_related("contributor__user").order_by("-created_at", "-pk")
    query = request.GET.get("q", "").strip()
    if query:
        items = items.filter(Q(title__icontains=query) | Q(description__icontains=query) | Q(topic__icontains=query))
    return render(request, "core/resources.html", {"items": items, "query": query, "can_manage_resources": can_manage_resources})


@capability_required("can_manage_members")
def resource_add(request):
    form = LearningResourceForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        resource = form.save(commit=False)
        resource.contributor = Member.objects.filter(user=request.user).first()
        resource.save()
        messages.success(request, "Resource added to the library.")
        return redirect("resources")
    return render(request, "core/resource_form.html", {"form": form, "title": "Add a library resource", "cancel_url": "/resources/"})


@capability_required("can_manage_members")
def resource_edit(request, resource_id):
    resource = get_object_or_404(LearningResource, pk=resource_id)
    previous_file = resource.file
    form = LearningResourceForm(request.POST or None, request.FILES or None, instance=resource)
    if request.method == "POST" and form.is_valid():
        saved = form.save()
        if previous_file and previous_file.name != saved.file.name:
            previous_file.delete(save=False)
        messages.success(request, f"Resource '{saved.title}' updated.")
        return redirect("resources")
    return render(request, "core/resource_form.html", {"form": form, "title": "Edit library resource", "cancel_url": "/resources/", "resource": resource})


@capability_required("can_manage_members")
def resource_delete(request, resource_id):
    resource = get_object_or_404(LearningResource, pk=resource_id)
    if request.method == "POST":
        title = resource.title
        if resource.file:
            resource.file.delete(save=False)
        resource.delete()
        messages.success(request, f"Resource '{title}' deleted.")
        return redirect("resources")
    return render(request, "core/confirm_delete.html", {
        "title": "Delete library resource",
        "object_name": resource.title,
        "warning": "This permanently removes the resource and its uploaded file from the library.",
        "cancel_url": "/resources/",
    })


@capability_required("can_access_resources")
def resource_file(request, resource_id):
    resource = get_object_or_404(LearningResource, pk=resource_id)
    if not resource.file:
        raise Http404
    return FileResponse(resource.file.open("rb"), as_attachment=True)


def apply_to_join(request):
    form = JoinApplicationForm(request.POST or None)
    submitted = False
    if request.method == "POST" and form.is_valid():
        form.save()
        submitted = True
        form = JoinApplicationForm()
    return render(request, "core/apply.html", {"form": form, "submitted": submitted})


@capability_required("can_manage_members")
def applications(request):
    if request.method == "POST":
        item = get_object_or_404(JoinApplication, pk=request.POST.get("application_id"))
        status = request.POST.get("status")
        if status not in {"accepted", "rejected", "pending"}:
            raise Http404
        item.status = status
        item.save(update_fields=["status"])
        if item.email:
            send_mail(
                f"Orionex membership application: {item.get_status_display()}",
                f"Hello {item.first_name},\n\nYour application to join Orionex has been marked as {item.get_status_display().lower()}. The club team will follow up with you if more information is needed.\n",
                None,
                [item.email],
                fail_silently=True,
            )
        messages.success(request, f"Application updated to {item.get_status_display().lower()}.")
        return redirect("applications")
    return render(request, "core/applications.html", {"applications": JoinApplication.objects.all()})


@capability_required("can_manage_events")
def analytics(request):
    period = request.GET.get("period", "year")
    today = timezone.localdate()
    period_filters = {}
    if period == "month":
        month_start = today.replace(day=1)
        month_end = (month_start.replace(day=28) + timezone.timedelta(days=4)).replace(day=1)
        start = timezone.make_aware(timezone.datetime.combine(month_start, timezone.datetime.min.time()))
        end = timezone.make_aware(timezone.datetime.combine(month_end, timezone.datetime.min.time()))
        period_filters = {"event_start": start, "event_end": end, "start": start, "end": end}
    elif period == "year":
        year_start = today.replace(month=1, day=1)
        year_end = year_start.replace(year=year_start.year + 1)
        start = timezone.make_aware(timezone.datetime.combine(year_start, timezone.datetime.min.time()))
        end = timezone.make_aware(timezone.datetime.combine(year_end, timezone.datetime.min.time()))
        period_filters = {"event_start": start, "event_end": end, "start": start, "end": end}
    else:
        period = "all"
    events = Event.objects.all()
    registrations_qs = EventRegistration.objects.all()
    attendance_qs = Attendance.objects.all()
    guests_qs = Guest.objects.all()
    tasks_qs = Task.objects.all()
    projects_qs = Project.objects.all()
    feedback_qs = EventFeedback.objects.all()
    if period_filters:
        events = events.filter(date__gte=period_filters["event_start"], date__lt=period_filters["event_end"])
        registrations_qs = registrations_qs.filter(created_at__gte=period_filters["start"], created_at__lt=period_filters["end"])
        attendance_qs = attendance_qs.filter(scanned_at__gte=period_filters["start"], scanned_at__lt=period_filters["end"])
        guests_qs = guests_qs.filter(created_at__gte=period_filters["start"], created_at__lt=period_filters["end"])
        tasks_qs = tasks_qs.filter(created_at__gte=period_filters["start"], created_at__lt=period_filters["end"])
        projects_qs = projects_qs.filter(created_at__gte=period_filters["start"], created_at__lt=period_filters["end"])
        feedback_qs = feedback_qs.filter(created_at__gte=period_filters["start"], created_at__lt=period_filters["end"])
    total_events = events.count()
    attendance = attendance_qs.count() + guests_qs.filter(checked_in_at__isnull=False).count()
    registrations = registrations_qs.count() + guests_qs.filter(status="approved").count()
    task_count = tasks_qs.count()
    completed_tasks = tasks_qs.filter(status="done").count()
    project_count = projects_qs.count()
    completed_projects = projects_qs.filter(status="completed").count()
    active_member_ids = set(attendance_qs.values_list("member_id", flat=True)) | set(registrations_qs.values_list("member_id", flat=True)) | set(tasks_qs.filter(status="done").values_list("assignees__id", flat=True))
    active_member_ids.discard(None)
    return render(request, "core/analytics.html", {"period": period, "total_events": total_events, "attendance": attendance, "registrations": registrations, "attendance_rate": round(attendance * 100 / registrations) if registrations else 0, "members": Member.objects.count(), "active_members": len(active_member_ids), "active_projects": projects_qs.exclude(status__in=["completed", "on_hold"]).count(), "project_count": project_count, "completed_projects": completed_projects, "project_rate": round(completed_projects * 100 / project_count) if project_count else 0, "task_count": task_count, "completed_tasks": completed_tasks, "task_rate": round(completed_tasks * 100 / task_count) if task_count else 0, "feedback_count": feedback_qs.count()})


@capability_required("can_manage_events", "can_manage_members")
def budget_tracker(request):
    form = BudgetEntryForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        entry = form.save(commit=False)
        entry.created_by = request.user
        entry.save()
        messages.success(request, "Budget entry saved.")
        return redirect("budget_tracker")
    year = request.GET.get("year", str(timezone.localdate().year))
    entries = BudgetEntry.objects.all()
    if year != "all":
        try:
            year_number = int(year)
            entries = entries.filter(date__year=year_number)
        except ValueError:
            year = str(timezone.localdate().year)
            entries = entries.filter(date__year=timezone.localdate().year)
    totals = entries.aggregate(income=Sum("amount", filter=Q(entry_type="income")), expenses=Sum("amount", filter=Q(entry_type="expense")))
    income = totals["income"] or 0
    expenses = totals["expenses"] or 0
    return render(request, "core/budget.html", {"form": form, "entries": entries, "year": year, "income": income, "expenses": expenses, "net": income - expenses, "years": BudgetEntry.objects.dates("date", "year", order="DESC")})

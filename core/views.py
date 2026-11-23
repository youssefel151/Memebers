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
from django.db.models import Q
from django.core.paginator import Paginator
from django.http import HttpResponse, Http404, JsonResponse
from django.shortcuts import render, redirect, get_object_or_404
from django.utils import timezone
from django.views.decorators.http import require_POST
from .models import Member, Event, Attendance, Announcement, Message, Guest, EventRegistration, Responsibility
from .forms import EventForm, AnnouncementForm, MemberPermissionsForm, ResponsibilityForm
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
            saved = form.save()
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
    event = get_object_or_404(Event, id=event_id)
    member = Member.objects.filter(user=request.user).first() if request.user.is_authenticated else None
    registered = bool(member and EventRegistration.objects.filter(member=member, event=event).exists())
    rsvp_count = event.member_registrations.count()
    guest_count = event.guests.filter(status="approved").count()
    full = bool(event.capacity and rsvp_count + guest_count >= event.capacity)
    return render(request, "core/event_detail.html", {"event": event, "member": member, "registered": registered, "is_open": event.registration_open and event.date >= timezone.now(), "full": full, "rsvp_count": rsvp_count, "guest_count": guest_count})


@login_required
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
        saved = form.save()
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
    upcoming = Event.objects.filter(date__gte=timezone.now()).order_by("date")
    announcements = Announcement.objects.filter(published=True).select_related("event")[:20]
    unread = Message.objects.filter(recipient=request.user, is_read=False).count()
    return render(
        request,
        "core/home.html",
        {"upcoming": upcoming, "announcements": announcements, "unread": unread},
    )


@login_required
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


@login_required
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


@login_required
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

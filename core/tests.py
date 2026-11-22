import json
from datetime import timedelta

from django.contrib import admin
from django.contrib.auth.models import User
from django.test import TestCase
from django.utils import timezone

from .admin import GuestAdmin
from .forms import EventForm
from .models import Attendance, Event, EventRegistration, Guest, Member
from .views import _safe_csv_cell


class CapacityPolicyTests(TestCase):
    def setUp(self):
        self.event = Event.objects.create(
            title="Full event", date=timezone.now() + timedelta(days=2),
            registration_open=True, capacity=1,
        )
        self.staff = User.objects.create_user("staff@example.com", password="a-long-test-password", is_staff=True)
        Member.objects.create(
            user=self.staff,
            can_manage_members=True,
            can_manage_guests=True,
            can_manage_permissions=True,
            can_scan=True,
        )

    def test_member_rsvp_is_rejected_when_full(self):
        guest = Guest.objects.create(event=self.event, name="Approved", status="approved")
        self.assertIsNotNone(guest.pk)
        member_user = User.objects.create_user("member@example.com", password="a-long-test-password")
        member = Member.objects.create(user=member_user)
        self.client.force_login(member_user)

        response = self.client.post(f"/events/{self.event.pk}/rsvp/")

        self.assertRedirects(response, f"/events/{self.event.pk}/")
        self.assertFalse(EventRegistration.objects.filter(member=member, event=self.event).exists())

    def test_guest_approval_is_rejected_when_full(self):
        Guest.objects.create(event=self.event, name="Already approved", status="approved")
        pending = Guest.objects.create(event=self.event, name="Pending")
        self.client.force_login(self.staff)

        self.client.post("/guests/", {"guest_id": pending.pk, "action": "approve"})

        pending.refresh_from_db()
        self.assertEqual(pending.status, "pending")

    def test_admin_does_not_offer_capacity_bypassing_approval(self):
        model_admin = GuestAdmin(Guest, admin.site)
        self.assertNotIn("approve_guests", model_admin.actions)

    def test_event_form_cannot_lower_capacity_below_attendees(self):
        member_user = User.objects.create_user("rsvp@example.com", password="a-long-test-password")
        member = Member.objects.create(user=member_user)
        EventRegistration.objects.create(event=self.event, member=member)
        Guest.objects.create(event=self.event, name="Guest", status="approved")
        form = EventForm({
            "title": self.event.title,
            "date": self.event.date.strftime("%Y-%m-%dT%H:%M"),
            "place": "",
            "description": "",
            "capacity": "1",
            "registration_open": "on",
        }, instance=self.event)

        self.assertFalse(form.is_valid())
        self.assertIn("capacity", form.errors)


class CheckInAndExportTests(TestCase):
    def setUp(self):
        self.staff = User.objects.create_user("door@example.com", password="a-long-test-password", is_staff=True)
        Member.objects.create(user=self.staff, can_scan=True)
        self.client.force_login(self.staff)
        self.event = Event.objects.create(title="Door", date=timezone.now() + timedelta(days=1))

    def scan(self, token, event=None):
        return self.client.post(
            "/scan/mark/",
            data=json.dumps({"token": str(token), "event": event or self.event.pk}),
            content_type="application/json",
        )

    def test_member_walk_in_is_allowed_and_labeled(self):
        member_user = User.objects.create_user("walkin@example.com", password="a-long-test-password")
        member = Member.objects.create(user=member_user)

        response = self.scan(member.qr_token)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["entry_type"], "walkin")
        self.assertTrue(Attendance.objects.filter(member=member, event=self.event).exists())

    def test_guest_pass_only_checks_in_once(self):
        guest = Guest.objects.create(event=self.event, name="Guest", status="approved")

        first = self.scan(guest.qr_token)
        second = self.scan(guest.qr_token)

        self.assertEqual(first.json()["status"], "ok")
        self.assertEqual(second.json()["status"], "already")

    def test_csv_formula_values_are_neutralized(self):
        self.assertEqual(_safe_csv_cell("=1+1"), "'=1+1")
        self.assertEqual(_safe_csv_cell("  @SUM(A1:A2)"), "'  @SUM(A1:A2)")
        self.assertEqual(_safe_csv_cell("ordinary name"), "ordinary name")

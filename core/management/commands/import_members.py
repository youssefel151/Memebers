from django.core.management.base import BaseCommand
from django.contrib.auth.models import User
from openpyxl import load_workbook
from core.models import Member


class Command(BaseCommand):
    help = "Import members from an Excel file"

    def add_arguments(self, parser):
        parser.add_argument("path")

    def handle(self, *args, **options):
        wb = load_workbook(options["path"])
        ws = wb.active
        created = 0

        for row in ws.iter_rows(min_row=2, values_only=True):
            first, last, email, phone = row[:4]
            if not email:
                continue

            user, is_new = User.objects.get_or_create(
                username=email,
                defaults={
                    "first_name": first or "",
                    "last_name": last or "",
                    "email": email,
                },
            )
            if is_new:
                user.set_password("ChangeMe123")
                user.save()
                created += 1

            Member.objects.get_or_create(
                user=user, defaults={"phone": str(phone or "")}
            )

        self.stdout.write(self.style.SUCCESS(f"Done: {created} new members"))
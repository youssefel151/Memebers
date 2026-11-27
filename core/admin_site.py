from django.contrib.admin import AdminSite
from django.contrib.auth.admin import GroupAdmin, UserAdmin
from django.contrib.auth.models import Group, User


class OrionexAdminSite(AdminSite):
    site_header = "Orionex control room"
    site_title = "Orionex control room"
    index_title = "Manage your club"

    def has_permission(self, request):
        # App capabilities are managed in the member roster. Django Admin is
        # reserved for superusers so is_staff cannot bypass those capabilities.
        return request.user.is_active and request.user.is_superuser


admin_site = OrionexAdminSite(name="orionex_admin")
admin_site.register(User, UserAdmin)
admin_site.register(Group, GroupAdmin)

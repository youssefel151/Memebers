from functools import wraps

from django.contrib.auth.views import redirect_to_login
from django.core.exceptions import PermissionDenied


CAPABILITIES = (
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


def has_capability(user, capability):
    if not user.is_authenticated:
        return False
    if user.is_superuser:
        return True
    try:
        member = user.member
    except user._meta.model.member.RelatedObjectDoesNotExist:
        member = None
    return bool(member and getattr(member, capability, False))


def capability_required(*capabilities):
    def decorate(view):
        @wraps(view)
        def wrapped(request, *args, **kwargs):
            if not request.user.is_authenticated:
                return redirect_to_login(request.get_full_path(), "/login/")
            if not any(has_capability(request.user, capability) for capability in capabilities):
                raise PermissionDenied
            return view(request, *args, **kwargs)
        return wrapped
    return decorate

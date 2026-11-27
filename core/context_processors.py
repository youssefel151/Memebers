from .permissions import CAPABILITIES, has_capability
from .models import Notification, Message


def member_capabilities(request):
    unread_notifications = Notification.objects.filter(
        recipient=request.user, read_at__isnull=True
    ).count() if request.user.is_authenticated else 0
    unread_messages = Message.objects.filter(recipient=request.user, is_read=False).count() if request.user.is_authenticated else 0
    return {
        "capabilities": {
            capability: has_capability(request.user, capability)
            for capability in CAPABILITIES
        },
        "notification_unread": unread_notifications,
        "unread": unread_messages,
    }

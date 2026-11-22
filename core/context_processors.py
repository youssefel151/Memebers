from .permissions import CAPABILITIES, has_capability


def member_capabilities(request):
    return {
        "capabilities": {
            capability: has_capability(request.user, capability)
            for capability in CAPABILITIES
        }
    }

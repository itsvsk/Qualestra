from sentinelqa.domain.enums import SafetyClass

SAFE_ACTIONS = frozenset({"navigate", "click", "refresh", "back", "forward", "wait_for"})
CONTROLLED_ACTIONS = frozenset(
    {"fill", "select", "check", "uncheck", "submit", "upload_fixture", "keyboard", "api_request"}
)
RESTRICTED_NAMES = frozenset(
    {
        "delete organization",
        "delete user",
        "change password",
        "change mfa",
        "payment",
        "purchase",
        "send email",
    }
)


def classify_action(action_type: str, control_name: str = "") -> SafetyClass:
    normalized = control_name.strip().lower()
    if any(name in normalized for name in RESTRICTED_NAMES):
        return SafetyClass.RESTRICTED
    if action_type in SAFE_ACTIONS:
        return SafetyClass.SAFE
    if action_type in CONTROLLED_ACTIONS:
        return SafetyClass.CONTROLLED
    return SafetyClass.RESTRICTED

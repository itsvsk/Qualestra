from sentinelqa.domain.enums import RunStatus

ALLOWED_TRANSITIONS = {
    RunStatus.QUEUED: {RunStatus.PREPARING, RunStatus.CANCELLING, RunStatus.FAILED},
    RunStatus.PREPARING: {RunStatus.EXPLORING, RunStatus.CANCELLING, RunStatus.FAILED},
    RunStatus.EXPLORING: {RunStatus.ANALYZING, RunStatus.CANCELLING, RunStatus.FAILED},
    RunStatus.ANALYZING: {RunStatus.VERIFYING, RunStatus.CANCELLING, RunStatus.FAILED},
    RunStatus.VERIFYING: {RunStatus.REPORTING, RunStatus.CANCELLING, RunStatus.FAILED},
    RunStatus.REPORTING: {RunStatus.GATING, RunStatus.CANCELLING, RunStatus.FAILED},
    RunStatus.GATING: {RunStatus.PASSED, RunStatus.WARNED, RunStatus.BLOCKED, RunStatus.FAILED},
    RunStatus.CANCELLING: {RunStatus.CANCELLED, RunStatus.FAILED},
}


def transition(current: RunStatus, target: RunStatus) -> RunStatus:
    if target not in ALLOWED_TRANSITIONS.get(current, set()):
        raise ValueError(f"invalid run transition: {current} -> {target}")
    return target

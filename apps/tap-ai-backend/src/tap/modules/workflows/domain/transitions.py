"""Explicit workflow lifecycle signals; terminal records stay historical."""

from tap.modules.workflows.domain.models import WorkflowStatus

# Retry of a failed node creates a new attempt in the later control service.
# It does not change an old terminal attempt's state through this function.
_TRANSITIONS: dict[tuple[WorkflowStatus, str], WorkflowStatus] = {
    (WorkflowStatus.QUEUED, "claim"): WorkflowStatus.RUNNING,
    (WorkflowStatus.WAITING_DEPENDENCY, "dependency_ready"): WorkflowStatus.QUEUED,
    (WorkflowStatus.RUNNING, "dependency_wait"): WorkflowStatus.WAITING_DEPENDENCY,
    (WorkflowStatus.RUNNING, "input_needed"): WorkflowStatus.AWAITING_INPUT,
    (WorkflowStatus.AWAITING_INPUT, "input_applied"): WorkflowStatus.QUEUED,
    (WorkflowStatus.RUNNING, "pause_requested"): WorkflowStatus.PAUSING,
    (WorkflowStatus.PAUSING, "runtime_paused"): WorkflowStatus.PAUSED,
    (WorkflowStatus.PAUSED, "resume_requested"): WorkflowStatus.QUEUED,
    (WorkflowStatus.RUNNING, "lease_lost"): WorkflowStatus.RECOVERING,
    (WorkflowStatus.PAUSING, "lease_lost"): WorkflowStatus.RECOVERING,
    (WorkflowStatus.CANCELING, "lease_lost"): WorkflowStatus.RECOVERING,
    (WorkflowStatus.RECOVERING, "reconciled"): WorkflowStatus.QUEUED,
    (WorkflowStatus.RUNNING, "cancel_requested"): WorkflowStatus.CANCELING,
    (WorkflowStatus.QUEUED, "cancel_requested"): WorkflowStatus.CANCELING,
    (WorkflowStatus.WAITING_DEPENDENCY, "cancel_requested"): WorkflowStatus.CANCELING,
    (WorkflowStatus.AWAITING_INPUT, "cancel_requested"): WorkflowStatus.CANCELING,
    (WorkflowStatus.PAUSED, "cancel_requested"): WorkflowStatus.CANCELING,
    (WorkflowStatus.CANCELING, "runtime_canceled"): WorkflowStatus.CANCELED,
    (WorkflowStatus.RUNNING, "runtime_succeeded"): WorkflowStatus.SUCCEEDED,
    (WorkflowStatus.RUNNING, "runtime_failed"): WorkflowStatus.FAILED,
    (WorkflowStatus.QUEUED, "plan_skipped"): WorkflowStatus.SKIPPED,
    (WorkflowStatus.WAITING_DEPENDENCY, "plan_skipped"): WorkflowStatus.SKIPPED,
}


def transition(current: str, signal: str) -> str:
    """Return the accepted next status or reject an unconfirmed transition."""
    try:
        status = WorkflowStatus(current)
        return _TRANSITIONS[(status, signal)].value
    except (ValueError, KeyError) as error:
        raise ValueError(f"invalid workflow transition: {current} + {signal}") from error

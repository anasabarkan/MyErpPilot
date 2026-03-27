from app.models.approval import ApprovalStatus

class ApprovalWorkflowTransitionError(Exception):
    pass

class ApprovalWorkflow:
    """
    Centralize state transitions for the Approval workflow.
    Ensures valid transitions:
      pending -> approved / rejected
      approved -> executed / failed
    """

    VALID_TRANSITIONS = {
        ApprovalStatus.pending: [ApprovalStatus.approved, ApprovalStatus.rejected],
        ApprovalStatus.approved: [ApprovalStatus.executed, ApprovalStatus.failed],
        ApprovalStatus.rejected: [],
        ApprovalStatus.executed: [],
        ApprovalStatus.failed: []
    }

    @classmethod
    def can_transition(cls, current_status: ApprovalStatus, next_status: ApprovalStatus) -> bool:
        return next_status in cls.VALID_TRANSITIONS.get(current_status, [])

    @classmethod
    def validate_transition(cls, current_status: ApprovalStatus, next_status: ApprovalStatus):
        if not cls.can_transition(current_status, next_status):
            raise ApprovalWorkflowTransitionError(
                f"Invalid status transition from {current_status.value} to {next_status.value}"
            )

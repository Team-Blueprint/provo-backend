from enum import Enum

from app.core.enums import DayStatus, JobStatus


class InvalidStateTransitionError(ValueError):
    """Raised when an illegal state transition is attempted."""


class JobEvent(str, Enum):
    REQUEST_FUNDING = "REQUEST_FUNDING"
    PAYMENT_RECEIVED = "PAYMENT_RECEIVED"
    START_WORK = "START_WORK"
    ALL_DAYS_SETTLED = "ALL_DAYS_SETTLED"
    CANCEL = "CANCEL"
    DISPUTE_RAISED = "DISPUTE_RAISED"


class DayEvent(str, Enum):
    WORKER_DONE = "WORKER_DONE"
    CLIENT_CONFIRM = "CLIENT_CONFIRM"
    TIMER_EXPIRED = "TIMER_EXPIRED"
    CLIENT_DISPUTE = "CLIENT_DISPUTE"
    PAYOUT_SUCCESS = "PAYOUT_SUCCESS"


# Allowed Job transitions: current_status -> {event: next_status}
JOB_TRANSITION_MAP: dict[JobStatus, dict[JobEvent, JobStatus]] = {
    JobStatus.DRAFT: {
        JobEvent.REQUEST_FUNDING: JobStatus.AWAITING_FUNDING,
        JobEvent.CANCEL: JobStatus.CANCELLED,
    },
    JobStatus.AWAITING_FUNDING: {
        JobEvent.PAYMENT_RECEIVED: JobStatus.FUNDED,
        JobEvent.CANCEL: JobStatus.CANCELLED,
    },
    JobStatus.FUNDED: {
        JobEvent.START_WORK: JobStatus.IN_PROGRESS,
        JobEvent.ALL_DAYS_SETTLED: JobStatus.COMPLETED,
        JobEvent.CANCEL: JobStatus.CANCELLED,
        JobEvent.DISPUTE_RAISED: JobStatus.DISPUTED,
    },
    JobStatus.IN_PROGRESS: {
        JobEvent.ALL_DAYS_SETTLED: JobStatus.COMPLETED,
        JobEvent.DISPUTE_RAISED: JobStatus.DISPUTED,
    },
    JobStatus.COMPLETED: {},  # Terminal
    JobStatus.CANCELLED: {},  # Terminal
    JobStatus.DISPUTED: {
        JobEvent.ALL_DAYS_SETTLED: JobStatus.COMPLETED,
    },
}

# Allowed Day transitions: current_status -> {event: next_status}
DAY_TRANSITION_MAP: dict[DayStatus, dict[DayEvent, DayStatus]] = {
    DayStatus.PENDING: {
        DayEvent.WORKER_DONE: DayStatus.DONE_REPORTED,
    },
    DayStatus.DONE_REPORTED: {
        DayEvent.CLIENT_CONFIRM: DayStatus.CONFIRMED,
        DayEvent.TIMER_EXPIRED: DayStatus.AUTO_RELEASED,
        DayEvent.CLIENT_DISPUTE: DayStatus.DISPUTED,
    },
    DayStatus.CONFIRMED: {
        DayEvent.PAYOUT_SUCCESS: DayStatus.PAID,
    },
    DayStatus.AUTO_RELEASED: {
        DayEvent.PAYOUT_SUCCESS: DayStatus.PAID,
    },
    DayStatus.DISPUTED: {},  # Frozen
    DayStatus.PAID: {},      # Terminal
}


def transition_job_state(current_status: JobStatus | str, event: JobEvent | str) -> JobStatus:
    """
    Pure state machine function for Job transitions.
    Raises InvalidStateTransitionError if the event is not permitted.
    """
    status = JobStatus(current_status)
    evt = JobEvent(event)

    allowed = JOB_TRANSITION_MAP.get(status, {})
    if evt not in allowed:
        raise InvalidStateTransitionError(
            f"Illegal Job transition: Cannot apply event '{evt.value}' to status '{status.value}'"
        )
    return allowed[evt]


def transition_day_state(current_status: DayStatus | str, event: DayEvent | str) -> DayStatus:
    """
    Pure state machine function for JobDay transitions.
    Raises InvalidStateTransitionError if the event is not permitted.
    """
    status = DayStatus(current_status)
    evt = DayEvent(event)

    allowed = DAY_TRANSITION_MAP.get(status, {})
    if evt not in allowed:
        raise InvalidStateTransitionError(
            f"Illegal Day transition: Cannot apply event '{evt.value}' to status '{status.value}'"
        )
    return allowed[evt]
